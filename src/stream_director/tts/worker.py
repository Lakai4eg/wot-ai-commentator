"""Голосовой worker: отдельный процесс, держит Chatterbox в VRAM.

Запускается client.py. Ничего не импортирует из stream_director: только
stdlib и gpu-runtime (путь приходит аргументом). Умирает вместе с родителем
(см. watch_parent) — так осиротевший процесс не удержит видеопамять, даже
если родителя убили.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path

PHASE = {"value": "loading"}
ENGINE = None
ACCENT = None
LOCK = threading.Lock()  # один синтез за раз: VRAM и очередь бережём
ARGS = None
_PLUS_RE = re.compile(r"\+(.)")


def watch_parent() -> None:
    """Умереть вместе с родителем: осиротевший worker не должен держать VRAM.

    На Windows ждём хэндл родителя, а не EOF stdin. Блокирующее чтение stdin
    держит критическую секцию ucrt для fd 0 всё время ожидания, а инициализация
    нативных модулей (numpy, torch) внутри LoadLibrary берёт эту же секцию под
    loader lock — импорт движка вставал намертво, и голос не поднимался.
    """
    if sys.platform == "win32":
        import ctypes

        SYNCHRONIZE, INFINITE = 0x00100000, 0xFFFFFFFF
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(SYNCHRONIZE, False, os.getppid())
        if not handle:  # без хэндла остаёмся под terminate() от клиента
            return
        k32.WaitForSingleObject(handle, INFINITE)
    else:
        sys.stdin.buffer.read()  # блокируется до смерти родителя
    os._exit(0)


def load_engine():
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS

    return ChatterboxMultilingualTTS.from_local(ARGS.model_dir, device="cuda")


def load_accentizer():
    import ruaccent
    from ruaccent import RUAccent

    # RUAccent.load() безусловно докачивает с HF подпакет koziev
    # (rupostagger+rulemma) в каталог САМОГО пакета, а не в workdir — оффлайн
    # это краш (HF_HUB_OFFLINE=1) или зависание. Кладём koziev туда из зеркала:
    # существующий каталог гасит докачку (проверка в ruaccent.load).
    pkg_koziev = Path(ruaccent.__file__).parent / "koziev"
    mirror_koziev = Path(ARGS.model_dir) / "ruaccent" / "koziev"
    if not pkg_koziev.exists() and mirror_koziev.is_dir():
        shutil.copytree(mirror_koziev, pkg_koziev)

    acc = RUAccent()
    acc.load(omograph_model_size="turbo", use_dictionary=True,
             workdir=str(Path(ARGS.model_dir) / "ruaccent"))
    return acc


def accentize(text: str) -> str:
    """RUAccent ставит '+' перед ударной гласной; движку нужен U+0301 после неё.

    Ошибка аккцентизации не валит синтез: ударения — улучшение, не точка отказа.
    """
    try:
        return _PLUS_RE.sub("\\1́", ACCENT.process_all(text))
    except Exception:
        return text


def synth_wav(text: str, voice: str | None,
              exaggeration: float, cfg_weight: float) -> bytes:
    text = accentize(text)
    ref: str | None = None
    if voice and voice != "default":
        p = Path(ARGS.voices_dir) / f"{voice}.wav"
        if p.is_file():
            ref = str(p)
    with LOCK:
        wav = ENGINE.generate(
            text, language_id="ru", audio_prompt_path=ref,
            exaggeration=exaggeration, cfg_weight=cfg_weight,
        )
    data = wav.squeeze(0).clamp(-1.0, 1.0).cpu().numpy()
    pcm = (data * 32767).astype("<i2").tobytes()
    buf = BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(ENGINE.sr))
        w.writeframes(pcm)
    return buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):  # не засорять stderr родителя
        pass

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path != "/health":
            return self._json(404, {"error": "not found"})
        vram = 0
        if PHASE["value"] == "ready":
            import torch
            vram = torch.cuda.memory_allocated() // 2**20
        self._json(200, {"phase": PHASE["value"], "vram_mb": vram})

    def do_POST(self):
        if self.path != "/synth":
            return self._json(404, {"error": "not found"})
        if PHASE["value"] != "ready":
            return self._json(503, {"error": "модель ещё загружается"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            req = json.loads(self.rfile.read(length))
            data = synth_wav(req["text"], req.get("voice"),
                             float(req.get("exaggeration", 0.5)),
                             float(req.get("cfg_weight", 0.5)))
        except Exception as e:  # любой сбой синтеза — это 500, а не смерть worker-а
            return self._json(500, {"error": str(e)})
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    global ARGS, ENGINE, ACCENT
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--voices-dir", required=True)
    parser.add_argument("--port", type=int, required=True)
    ARGS = parser.parse_args()

    # gpu-runtime первым в sys.path: его пины важнее site-packages приложения.
    sys.path.insert(0, str(Path(ARGS.runtime).resolve()))
    threading.Thread(target=watch_parent, daemon=True).start()

    # health отвечает «loading» уже во время разворота модели в VRAM.
    server = ThreadingHTTPServer(("127.0.0.1", ARGS.port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    ENGINE = load_engine()
    ACCENT = load_accentizer()
    PHASE["value"] = "ready"
    threading.Event().wait()  # живём, пока не убьют / EOF stdin


if __name__ == "__main__":
    main()
