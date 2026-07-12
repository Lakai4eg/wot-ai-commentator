"""Голосовой worker: отдельный процесс, держит S1-mini в VRAM.

Запускается client.py. Ничего не импортирует из stream_director: только
stdlib и gpu-runtime (путь приходит аргументом). Умирает по EOF stdin —
так осиротевший процесс не удержит видеопамять, даже если родителя убили.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path

PHASE = {"value": "loading"}
ENGINE = None
LOCK = threading.Lock()  # один синтез за раз: VRAM и очередь бережём
ARGS = None


def watch_parent_stdin() -> None:
    sys.stdin.buffer.read()  # блокируется до смерти родителя
    os._exit(0)


def load_engine():
    import torch
    from fish_speech.inference_engine import TTSInferenceEngine
    from fish_speech.models.dac.inference import load_model as load_decoder_model
    from fish_speech.models.text2semantic.inference import launch_thread_safe_queue

    precision = torch.bfloat16
    llama_queue = launch_thread_safe_queue(
        checkpoint_path=str(ARGS.model_dir), device="cuda",
        precision=precision, compile=False,
    )
    decoder = load_decoder_model(
        config_name="modded_dac_vq",
        checkpoint_path=str(Path(ARGS.model_dir) / "codec.pth"), device="cuda",
    )
    return TTSInferenceEngine(
        llama_queue=llama_queue, decoder_model=decoder,
        precision=precision, compile=False,
    )


def synth_wav(text: str, voice: str | None) -> bytes:
    import numpy as np
    from fish_speech.utils.schema import ServeReferenceAudio, ServeTTSRequest

    references = []
    if voice and voice != "default":
        wav_p = Path(ARGS.voices_dir) / f"{voice}.wav"
        txt_p = Path(ARGS.voices_dir) / f"{voice}.txt"
        if wav_p.is_file() and txt_p.is_file():
            references = [ServeReferenceAudio(
                audio=wav_p.read_bytes(),
                text=txt_p.read_text(encoding="utf-8"),
            )]
    request = ServeTTSRequest(
        text=text, references=references, format="wav", streaming=False,
        max_new_tokens=1024, chunk_length=300,
        top_p=0.8, repetition_penalty=1.1, temperature=0.8,
    )
    sample_rate, parts = 44100, []
    with LOCK:
        for result in ENGINE.inference(request):
            if result.code == "error":
                raise RuntimeError(str(result.error))
            if result.audio is not None:
                sample_rate, data = result.audio
                parts.append(data)
    if not parts:
        raise RuntimeError("модель не вернула аудио")
    audio = np.concatenate(parts)
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    buf = BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sample_rate))
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
            data = synth_wav(req["text"], req.get("voice"))
        except Exception as e:  # любой сбой синтеза — это 500, а не смерть worker-а
            return self._json(500, {"error": str(e)})
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    global ARGS, ENGINE
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--voices-dir", required=True)
    parser.add_argument("--port", type=int, required=True)
    ARGS = parser.parse_args()

    # gpu-runtime первым в sys.path: его пины важнее site-packages приложения.
    sys.path.insert(0, str(Path(ARGS.runtime).resolve()))
    threading.Thread(target=watch_parent_stdin, daemon=True).start()

    # health отвечает «loading» уже во время разворота модели в VRAM.
    server = ThreadingHTTPServer(("127.0.0.1", ARGS.port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    ENGINE = load_engine()
    PHASE["value"] = "ready"
    threading.Event().wait()  # живём, пока не убьют / EOF stdin


if __name__ == "__main__":
    main()
