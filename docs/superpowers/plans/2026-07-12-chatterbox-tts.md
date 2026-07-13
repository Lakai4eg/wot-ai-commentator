# Chatterbox TTS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Заменить озвучку OpenAudio S1-mini (fish-speech, CC-BY-NC) на Chatterbox Multilingual (Resemble AI, MIT) с сохранением каркаса worker-подпроцесса и добавлением эмоций через `exaggeration`/`cfg_weight`.

**Architecture:** Worker-подпроцесс с моделью в VRAM и HTTP `/health`+`/synth` сохраняется; меняются движок внутри worker-а, пины рантайма, манифест весов и путь модели. Эмо-маркеры LLM (`(angry)` и т.п.) больше не уходят в модель текстом: `client.synth` разбирает их в пару `(exaggeration, cfg_weight)`, `broadcast.publish` — в чистый текст для оверлея и в выбор голоса `voice_by_marker`.

**Tech Stack:** Python 3.12 (portable — 3.12.10), FastAPI, `chatterbox-tts==0.1.7` (установка `--no-deps`), torch cu126, React/TS (панель).

**Spec:** `docs/superpowers/specs/2026-07-12-chatterbox-tts-design.md` — читать перед началом любой задачи.

## Global Constraints

- **Тестов в проекте нет и заводить их ЗАПРЕЩЕНО.** Проверка каждой задачи: `python -m py_compile <files>`, `ruff check src`, и живой прогон, где указан.
- **Коммиты не делать.** Ни одна задача не коммитит — пользователь коммитит сам.
- `requires-python = ">=3.12"`; portable-сборка везёт Python 3.12.10 → действует пин `torch==2.6.0` у chatterbox-tts (обходится `--no-deps`).
- `language_id="ru"` — константа в worker, в настройки не выносить.
- Сигнатура `synth(text, voice) -> bytes | None` не меняется.
- Контракт статусов TTS (`checking/no_gpu/downloading_runtime/downloading_model/starting/loading/ready/error`) не меняется — панель на него завязана.
- Точные версии пакетов, список файлов весов, sha256, размеры и значения по умолчанию для `exaggeration` берутся ТОЛЬКО из `docs/superpowers/plans/spike-chatterbox-results.md` (продукт Задачи 1). До его появления Задачи 3–9 не начинать.
- Все пользовательские строки — на русском.

---

### Task 1: Спайк на живой машине

**Files:**
- Create: `build/spike-chatterbox/spike.py` (build/ в .gitignore — в репо не попадёт)
- Create: `docs/superpowers/plans/spike-chatterbox-results.md`

**Interfaces:**
- Produces: `spike-chatterbox-results.md` с разделами: `## Пины` (точный RUNTIME_PACKAGES + вердикт по torch), `## Веса` (таблица имя/sha256/размер), `## Чекпоинт` (v2 или v3), `## Метрики` (VRAM МБ, секунд/фраза), `## Стили` (подтверждённая таблица MARKER_STYLE), `## Референс` (мин. длина, формулировка подсказки для панели).

- [ ] **Step 1: Создать venv спайка и попытаться поставить chatterbox-tts БЕЗ отката torch**

```powershell
py -3.12 -m venv build\spike-chatterbox\venv
build\spike-chatterbox\venv\Scripts\python -m pip install --index-url https://download.pytorch.org/whl/cu126 --extra-index-url https://pypi.org/simple torch torchaudio
build\spike-chatterbox\venv\Scripts\python -m pip install --no-deps chatterbox-tts==0.1.7
build\spike-chatterbox\venv\Scripts\python -m pip install transformers diffusers librosa s3tokenizer resemble-perth conformer safetensors omegaconf numpy spacy-pkuseg pykakasi pyloudnorm
```

Записать в результаты, какие версии выбрал резолвер (`pip freeze`). Если импорт движка (Step 3) упадёт из-за несовместимости с новым torch — повторить с `torch==2.6.0 torchaudio==2.6.0` и зафиксировать в результатах: «Blackwell теряем, torch 2.6.0» (и это уйдёт в README в Задаче 9).

- [ ] **Step 2: Скачать веса multilingual с HF (здесь VPN у автора есть; зеркало соберём в Задаче 2)**

```powershell
build\spike-chatterbox\venv\Scripts\python -m pip install huggingface-hub
build\spike-chatterbox\venv\Scripts\python -c "from huggingface_hub import snapshot_download; snapshot_download('ResembleAI/chatterbox', local_dir='build/spike-chatterbox/weights')"
```

- [ ] **Step 3: Написать и прогнать spike.py**

```python
"""Спайк Chatterbox: русская фраза, клон, метрики. Запуск из venv спайка."""
import time
from pathlib import Path

import torch
import torchaudio

from chatterbox.mtl_tts import ChatterboxMultilingualTTS

WEIGHTS = Path(__file__).parent / "weights"
OUT = Path(__file__).parent / "out"
OUT.mkdir(exist_ok=True)

model = ChatterboxMultilingualTTS.from_local(WEIGHTS, device="cuda")
print("sr =", model.sr)

PHRASE = "Ну и куда ты поехал, гений тактики? Вся команда смотрит на этот позор."
STYLES = {  # маркер: (exaggeration, cfg_weight) — стартовые из спеки
    "neutral": (0.5, 0.5), "angry": (0.8, 0.3), "shouting": (0.9, 0.3),
    "excited": (0.75, 0.35), "laughing": (0.7, 0.4), "confident": (0.6, 0.4),
    "sad": (0.4, 0.6), "whispering": (0.3, 0.6),
}
for name, (ex, cfg) in STYLES.items():
    t0 = time.monotonic()
    wav = model.generate(PHRASE, language_id="ru", exaggeration=ex, cfg_weight=cfg)
    dt = time.monotonic() - t0
    torchaudio.save(str(OUT / f"builtin_{name}.wav"), wav, model.sr)
    print(f"{name}: {dt:.1f}s, VRAM {torch.cuda.memory_allocated()//2**20} MB")

# Клон: свой референс положить в build/spike-chatterbox/ref.wav (10 с) и ref3.wav (3 с)
for ref in ("ref.wav", "ref3.wav"):
    p = Path(__file__).parent / ref
    if p.is_file():
        wav = model.generate(PHRASE, language_id="ru", audio_prompt_path=str(p),
                             exaggeration=0.6, cfg_weight=0.4)
        torchaudio.save(str(OUT / f"clone_{ref}"), wav, model.sr)
```

Run: `build\spike-chatterbox\venv\Scripts\python build\spike-chatterbox\spike.py`
Expected: печатает `sr`, времена и VRAM; в `out/` появляются wav-ы. Если `from_local` падает `FileNotFoundError` — записать, какого файла не хватило (это уточняет манифест весов).

- [ ] **Step 4: Определить точный список файлов, нужных from_local**

Переместить `weights/` во временное имя, создать пустую папку, копировать файлы по одному в порядке: `t3_mtl23ls_v2.safetensors`, `s3gen.pt`, `ve.pt`, `grapheme_mtl_merged_expanded_v1.json`, `conds.pt`, — запуская Step 3 после каждого, пока не заведётся. Проверить, требуется ли `Cangjie5_TC.json`. Если в снапшоте есть `t3_mtl23ls_v3.safetensors` — прогнать Step 3 и на нём (подменой файла) и сохранить wav-ы отдельно как `out/v3_*.wav`.

- [ ] **Step 5: Посчитать sha256 и размеры финального набора**

```powershell
Get-ChildItem build\spike-chatterbox\weights | ForEach-Object { "{0}  {1}  {2}" -f (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower(), $_.Length, $_.Name }
```

- [ ] **Step 6: КОНТРОЛЬНАЯ ТОЧКА ПОЛЬЗОВАТЕЛЯ — прослушивание**

Остановиться и попросить пользователя прослушать `build/spike-chatterbox/out/*.wav` и решить: (а) v2 или v3; (б) слышна ли разница стилей, править ли таблицу; (в) достаточно ли 3-секундного референса или требовать 10 с. Без его ответов Задачу 1 не закрывать.

- [ ] **Step 7: Записать spike-chatterbox-results.md**

Заполнить все шесть разделов из Interfaces фактами (не планами): точный `RUNTIME_PACKAGES` с версиями из `pip freeze`, таблицу весов, выбранный чекпоинт, метрики, подтверждённую таблицу стилей, рекомендацию по референсу.

---

### Task 2: Релиз весов models-chatterbox-v1

**Files:**
- (нет изменений в коде; артефакт — GitHub Release)

**Interfaces:**
- Consumes: `build/spike-chatterbox/weights/` и таблицу весов из spike-chatterbox-results.md.
- Produces: релиз `models-chatterbox-v1` в репо `Lakai4eg/game-ai-commentator` с файлами весов как ассетами; URL вида `https://github.com/Lakai4eg/game-ai-commentator/releases/download/models-chatterbox-v1/<имя>`.

Примечание по итогам спайка: заливать набор из 5 файлов (`weights_min` — если выбран v2, `weights_v3` — если v3; в обоих чекпоинт уже лежит под именем `t3_mtl23ls_v2.safetensors`, потому что это имя зашито в библиотеку).

- [ ] **Step 1: Создать релиз и залить ассеты**

```powershell
rtk gh release create models-chatterbox-v1 --title "Chatterbox Multilingual weights" --notes "Зеркало весов ResembleAI/chatterbox (MIT) для озвучки Stream Director. Файлы соответствуют spike-chatterbox-results.md." --repo Lakai4eg/game-ai-commentator
Get-ChildItem build\spike-chatterbox\weights | ForEach-Object { gh release upload models-chatterbox-v1 $_.FullName --repo Lakai4eg/game-ai-commentator }
```

- [ ] **Step 2: Проверить скачиваемость и sha256 одного файла**

```powershell
curl -L -o $env:TEMP\conds.pt https://github.com/Lakai4eg/game-ai-commentator/releases/download/models-chatterbox-v1/conds.pt
Get-FileHash $env:TEMP\conds.pt -Algorithm SHA256
```

Expected: hash совпадает с таблицей из spike-chatterbox-results.md.

---

### Task 3: pins.py, paths.py и двухфазная установка в bootstrap.py

**Files:**
- Modify: `src/stream_director/tts/pins.py` (переписать целиком)
- Modify: `src/stream_director/paths.py:27`
- Modify: `src/stream_director/tts/bootstrap.py:52-56, 60-91`

**Interfaces:**
- Consumes: spike-chatterbox-results.md (версии, веса, sha256).
- Produces: `RUNTIME_PACKAGES: list[str]`, `NO_DEPS_PACKAGES: list[str]`, `TORCH_INDEX_URL: str`, `WEIGHTS: dict[str, tuple[str, int]]`, `WEIGHTS_BASE_URL: str`; `paths.MODEL_DIR == HOME/"models"/"chatterbox"`; `bootstrap.ensure_runtime` ставит сначала RUNTIME_PACKAGES, затем NO_DEPS_PACKAGES с `--no-deps`.

- [ ] **Step 1: Переписать pins.py**

Версии и sha256 ниже — ЗАГЛУШКИ СТРУКТУРЫ; реальные значения взять из spike-chatterbox-results.md (раздел «Пины» и «Веса»). Если спайк выбрал v3 — имя файла чекпоинта соответственно.

```python
"""Пины GPU-стека: версии пакетов, индекс torch, манифест весов.

Значения — только из docs/superpowers/plans/spike-chatterbox-results.md
(проверено на живой машине). Руками не менять.
"""

TORCH_INDEX_URL = "https://download.pytorch.org/whl/cu128"  # cu126 НЕ содержит sm_120 (RTX 50xx) — проверено спайком
# Зависимости chatterbox-tts, поставленные явно: сам пакет идёт --no-deps,
# потому что его пины (gradio==6.8.0, torch==2.6.0) тянут лишнее и старое.
RUNTIME_PACKAGES = [
    "torch==<из спайка>",
    "torchaudio==<из спайка>",
    "transformers==<из спайка>",
    "diffusers==<из спайка>",
    "librosa==<из спайка>",
    "s3tokenizer==<из спайка>",
    "resemble-perth==<из спайка>",
    "conformer==<из спайка>",
    "safetensors==<из спайка>",
    "omegaconf==<из спайка>",
    "numpy==<из спайка>",
    "spacy-pkuseg==<из спайка>",
    "pykakasi==<из спайка>",
    "pyloudnorm==<из спайка>",
]
# Ставятся вторым проходом pip с --no-deps.
NO_DEPS_PACKAGES = ["chatterbox-tts==0.1.7"]

WEIGHTS_BASE_URL = (
    "https://github.com/Lakai4eg/game-ai-commentator/releases/download/models-chatterbox-v1"
)
# имя файла → (sha256, размер в байтах). Из spike-chatterbox-results.md.
WEIGHTS: dict[str, tuple[str, int]] = {
    # Имя чекпоинта зашито в chatterbox (mtl_tts.py:179) как *_v2: если пользователь
    # выбрал v3 — его файл кладётся в релиз и в манифест ПОД ЭТИМ ЖЕ ИМЕНЕМ.
    "t3_mtl23ls_v2.safetensors": ("<sha>", 0),
    "s3gen.pt": ("<sha>", 0),
    "ve.pt": ("<sha>", 0),
    "grapheme_mtl_merged_expanded_v1.json": ("<sha>", 0),
    "conds.pt": ("<sha>", 0),
    # "Cangjie5_TC.json": ("<sha>", 0),  # только если спайк показал, что нужен
}
```

- [ ] **Step 2: paths.py — новый MODEL_DIR**

В `src/stream_director/paths.py:27` заменить:

```python
MODEL_DIR = HOME / "models" / "s1-mini"
```

на:

```python
MODEL_DIR = HOME / "models" / "chatterbox"
```

- [ ] **Step 3: bootstrap.py — двухфазная установка и уборка костыля**

Импорт пинов (строка 20): добавить `NO_DEPS_PACKAGES`:

```python
from .pins import NO_DEPS_PACKAGES, RUNTIME_PACKAGES, TORCH_INDEX_URL, WEIGHTS, WEIGHTS_BASE_URL
```

`_pins_fingerprint` (строки 52-54) — включить NO_DEPS_PACKAGES, чтобы смена пакета сносила рантайм:

```python
def _pins_fingerprint() -> str:
    raw = json.dumps([TORCH_INDEX_URL, RUNTIME_PACKAGES, NO_DEPS_PACKAGES], ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()
```

В `ensure_runtime` вынести запуск pip в помощник и вызвать дважды; удалить создание `.project-root` (строки 84-89 — комментарий и `touch`). Итоговое тело после `if RUNTIME_DIR.exists(): shutil.rmtree(RUNTIME_DIR)`:

```python
    status(_st("downloading_runtime", {"step": "подготовка…"}))
    _pip_install(RUNTIME_PACKAGES, extra=["--index-url", TORCH_INDEX_URL,
                                          "--extra-index-url", "https://pypi.org/simple"],
                 status=status)
    # chatterbox-tts пинит gradio==6.8.0 и torch==2.6.0 — его зависимости
    # уже стоят явным списком выше, сам пакет въезжает без них.
    _pip_install(NO_DEPS_PACKAGES, extra=["--no-deps"], status=status)
    marker.write_text(_pins_fingerprint())
```

Помощник (тело — прежний код Popen/tail из ensure_runtime, строки 61-83, без изменений логики):

```python
def _pip_install(packages: list[str], extra: list[str], status: StatusCb) -> None:
    cmd = [sys.executable, "-m", "pip", "install",
           "--target", str(RUNTIME_DIR), "--progress-bar", "off", *extra, *packages]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW,
    )
    tail: list[str] = []
    for line in proc.stdout:
        tail.append(line.rstrip())
        tail = tail[-30:]
        m = re.match(r"\s*(Collecting|Downloading)\s+(\S+)", line)
        if m:
            status(_st("downloading_runtime", {"step": m.group(2)}))
    if proc.wait() != 0:
        raise BootstrapError("pip не смог установить GPU-рантайм:\n" + "\n".join(tail[-8:]))
```

Примечание: во втором вызове `--target` уже непуст — pip дописывает в то же дерево, это штатно (`chatterbox` не пересекается с уже установленными пакетами по путям).

- [ ] **Step 4: Проверить**

Run: `python -m py_compile src/stream_director/tts/pins.py src/stream_director/tts/bootstrap.py src/stream_director/paths.py && ruff check src/stream_director/tts src/stream_director/paths.py`
Expected: без ошибок. Grep-контроль: `grep -rn "project-root\|s1-mini" src/` — пусто.

---

### Task 4: worker.py — движок Chatterbox

**Files:**
- Modify: `src/stream_director/tts/worker.py` (функции `load_engine`, `synth_wav`, `do_POST`, docstring)

**Interfaces:**
- Consumes: веса в `--model-dir`, референсы в `--voices-dir` (только `.wav`).
- Produces: HTTP `POST /synth` c телом `{"text": str, "voice": str|null, "exaggeration": float, "cfg_weight": float}` → `audio/wav`; `GET /health` без изменений.

- [ ] **Step 1: Заменить docstring и load_engine**

Docstring модуля: слово «S1-mini» → «Chatterbox». `load_engine` (строки 30-49) целиком:

```python
def load_engine():
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS

    return ChatterboxMultilingualTTS.from_local(ARGS.model_dir, device="cuda")
```

- [ ] **Step 2: Заменить synth_wav**

Целиком (строки 52-88). Референс — один wav без транскрипта; `default` — встроенный голос (conds.pt), `audio_prompt_path` не передаём:

```python
def synth_wav(text: str, voice: str | None,
              exaggeration: float, cfg_weight: float) -> bytes:
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
```

- [ ] **Step 3: do_POST — прокинуть новые поля**

В `do_POST` заменить вызов:

```python
            data = synth_wav(req["text"], req.get("voice"),
                             float(req.get("exaggeration", 0.5)),
                             float(req.get("cfg_weight", 0.5)))
```

- [ ] **Step 4: Проверить**

Run: `python -m py_compile src/stream_director/tts/worker.py && ruff check src/stream_director/tts/worker.py`
Expected: чисто. Grep: `grep -n "fish_speech\|ServeTTSRequest\|np\.concatenate" src/stream_director/tts/worker.py` — пусто (заодно убрать ставший ненужным `import numpy` если он больше не используется... в новом коде numpy не импортируется — данные идут через `.cpu().numpy()`).

---

### Task 5: markers.py, voices.py, client.py, tts/__init__.py

**Files:**
- Modify: `src/stream_director/tts/markers.py`
- Modify: `src/stream_director/tts/voices.py`
- Modify: `src/stream_director/tts/client.py`
- Modify: `src/stream_director/tts/__init__.py`

**Interfaces:**
- Produces:
  - `markers.parse(text: str) -> tuple[str | None, str]` — первый известный маркер и текст без всех известных маркеров;
  - `markers.MARKER_STYLE: dict[str, tuple[float, float]]`, `markers.DEFAULT_STYLE: tuple[float, float]`;
  - `voices.save_voice(name: str, wav: bytes) -> None` (без транскрипта);
  - `voices.pick_voice(settings, stim_type: str, priority, marker: str | None) -> str`;
  - класс `client.ChatterboxTTS` (бывший `S1MiniTTS`, публичный контракт `available`/`start`/`synth(text, voice)`/`stop` прежний);
  - реэкспорты в `tts/__init__.py`: `ChatterboxTTS`, `parse`, `EMOTION_MARKERS`, `pick_voice`, остальное как было.
- Consumes: контракт `/synth` из Задачи 4.

- [ ] **Step 1: markers.py — parse и таблица стилей**

Файл целиком (значения таблицы сверить с разделом «Стили» spike-chatterbox-results.md; ниже — из спеки):

```python
"""Эмо-маркеры: список для промпта LLM и превращение маркера в стиль синтеза.

Chatterbox не понимает маркеры текстом — маркер снимается из реплики и
превращается в пару (exaggeration, cfg_weight); зрителю уходит чистый текст.
"""

from __future__ import annotations

import re

# Список, попадающий в инструкцию LLM: четыре контрастные подачи (вердикт
# прослушивания: больше градаций на слух неразличимы).
EMOTION_MARKERS: tuple[str, ...] = ("angry", "excited", "sad", "whispering")

# маркер → (exaggeration, cfg_weight): четыре угла пространства стилей.
DEFAULT_STYLE: tuple[float, float] = (0.5, 0.5)
MARKER_STYLE: dict[str, tuple[float, float]] = {
    "angry": (0.9, 0.25),
    "excited": (0.75, 0.35),
    "sad": (0.35, 0.65),
    "whispering": (0.25, 0.7),
}

# Маркеры старого (широкого) списка: сохранённые в БД персоны могут их ставить —
# вырезаем и ведём к ближайшему стилю, иначе движок зачитает маркер вслух.
_LEGACY: dict[str, str] = {
    "shouting": "angry",
    "disdainful": "angry",
    "laughing": "excited",
    "chuckling": "excited",
    "confident": "excited",
    "surprised": "excited",
    "sighing": "sad",
}

_MARKER_RE = re.compile(r"\((%s)\)\s*" % "|".join((*MARKER_STYLE, *_LEGACY)))


def parse(text: str) -> tuple[str | None, str]:
    """Первый известный маркер + текст, очищенный от ВСЕХ известных маркеров.

    LLM просят ставить один маркер в начале, но полагаться на это нельзя.
    Легаси-маркеры приводятся к канонному имени. Незнакомые скобки не трогаем:
    LLM может законно их использовать.
    """
    m = _MARKER_RE.search(text)
    marker = _LEGACY.get(m.group(1), m.group(1)) if m else None
    return marker, _MARKER_RE.sub("", text).strip()
```

- [ ] **Step 2: voices.py — без транскриптов, с маркерным уровнем**

Изменения по функциям (docstring модуля: убрать упоминание `<имя>.txt`):

```python
def list_voices() -> list[str]:
    names = []
    if VOICES_DIR.is_dir():
        names = [wav.stem for wav in VOICES_DIR.glob("*.wav")]
    return [DEFAULT_VOICE, *sorted(names)]


def save_voice(name: str, wav: bytes) -> None:
    if not _NAME_RE.match(name) or name == DEFAULT_VOICE:
        raise ValueError("имя: буквы/цифры/дефис/подчёркивание, до 32 символов")
    VOICES_DIR.mkdir(parents=True, exist_ok=True)
    (VOICES_DIR / f"{name}.wav").write_bytes(wav)


def delete_voice(name: str) -> bool:
    if name == DEFAULT_VOICE or not _NAME_RE.match(name):
        return False
    wav = VOICES_DIR / f"{name}.wav"
    if not wav.is_file():
        return False
    wav.unlink()
    # Транскрипты эпохи S1-mini: больше не читаются, но не должны сиротеть.
    (VOICES_DIR / f"{name}.txt").unlink(missing_ok=True)
    return True


def pick_voice(settings, stim_type: str, priority, marker: str | None = None) -> str:
    """Голос под контекст: маркер > override по типу > приоритет > дефолт."""
    known = set(list_voices())
    for candidate in (
        settings.voice_by_marker.get(marker) if marker else None,
        settings.voice_overrides.get(stim_type),
        settings.voice_by_priority.get(priority.name.lower()),
        settings.default_voice,
    ):
        if candidate in known:
            return candidate
    return DEFAULT_VOICE
```

Функцию `voice_paths` удалить (единственный потребитель — старый worker через файловую систему; новый worker сам строит путь по имени).

- [ ] **Step 3: client.py — переименование и разбор маркера в synth**

Класс `S1MiniTTS` → `ChatterboxTTS`; docstring класса не меняется. Импорт добавить:

```python
from .markers import DEFAULT_STYLE, MARKER_STYLE, parse
```

`synth` целиком:

```python
    def synth(self, text: str, voice: str | None = None) -> bytes | None:
        if not self._ready:
            return None
        # Маркер разбирается здесь, а не в broadcast: /api/tts/preview зовёт
        # synth напрямую, и маркеры в превью должны работать так же, как в эфире.
        marker, clean = parse(text)
        exaggeration, cfg_weight = MARKER_STYLE.get(marker, DEFAULT_STYLE)
        try:
            r = self._http.post(self._url("/synth"), json={
                "text": clean, "voice": voice,
                "exaggeration": exaggeration, "cfg_weight": cfg_weight,
            })
            if r.status_code != 200:
                log.warning("synth %s: %s", r.status_code, r.text[:200])
                return None
            return r.content
        except httpx.HTTPError:
            log.warning("Голосовой worker не отвечает — перезапуск в фоне")
            self._ready = False
            threading.Thread(target=self._restart, daemon=True).start()
            return None
```

Также: docstring модуля «S1-mini» → «Chatterbox»; импорт `from .voices import VOICES_DIR` остаётся (путь передаётся worker-у аргументом).

- [ ] **Step 4: tts/__init__.py целиком**

```python
"""Озвучка Chatterbox Multilingual: worker-подпроцесс на GPU + вспомогательные части."""

from .audio import AudioStore
from .client import ChatterboxTTS
from .markers import EMOTION_MARKERS, parse
from .voices import DEFAULT_VOICE, delete_voice, list_voices, pick_voice, save_voice

__all__ = [
    "AudioStore", "ChatterboxTTS", "EMOTION_MARKERS", "parse",
    "DEFAULT_VOICE", "delete_voice", "list_voices", "pick_voice", "save_voice",
]
```

- [ ] **Step 5: Проверить**

Run: `python -m py_compile src/stream_director/tts/markers.py src/stream_director/tts/voices.py src/stream_director/tts/client.py src/stream_director/tts/__init__.py && ruff check src/stream_director/tts`
Expected: чисто. Импорт-проверка последствий: `grep -rn "S1MiniTTS\|strip_markers\|voice_paths" src/ web/` — должны остаться ТОЛЬКО `broadcast.py` и `main.py` (чинятся в Задаче 6). Быстрый smoke parse:

```powershell
python -c "import sys; sys.path.insert(0, 'src'); from stream_director.tts.markers import parse; print(parse('(angry) Ну и (laughing) поехал?')); print(parse('без маркера'))"
```

Expected: `('angry', 'Ну и поехал?')` и `(None, 'без маркера')`.

---

### Task 6: broadcast.py, main.py, config.py

**Files:**
- Modify: `src/stream_director/broadcast.py:15, 30, 42-43, 52-56`
- Modify: `src/stream_director/main.py:31, 178`
- Modify: `src/stream_director/config.py` (поле после `voice_overrides`)

**Interfaces:**
- Consumes: `ChatterboxTTS`, `parse`, `pick_voice(..., marker)` из Задачи 5.
- Produces: `Settings.voice_by_marker: dict[str, str]` — читается voices.pick_voice и server.py (Задача 7).

- [ ] **Step 1: config.py — новое поле**

После `voice_overrides` (строка 54):

```python
    # voice_by_marker: эмо-маркер ("angry"…) → голос; сильнее всех прочих правил.
    voice_by_marker: dict[str, str] = field(default_factory=dict)
```

(load_settings фильтрует по `dataclasses.fields`, отдельных правок не требует.)

- [ ] **Step 2: broadcast.py — parse вместо strip_markers, маркер в pick_voice**

Строка 15:

```python
from .tts import AudioStore, ChatterboxTTS, parse, pick_voice
```

Строка 30: `tts: ChatterboxTTS | None = None`.

В `publish` (строки 36-56) — разобрать один раз, в оверлей чистый текст, в синтез оригинал (стиль с него снимет client.synth):

```python
    async def publish(self, text: str, stimulus: Stimulus) -> None:
        self.replica_counter += 1
        replica_id = self.replica_counter
        marker, clean = parse(text)
        message: dict[str, Any] = {
            "type": "replica",
            "id": replica_id,
            # Зрителю — текст без эмо-маркеров; в синтез уходит оригинал.
            "text": clean if self.settings.text_enabled else "",
            "effect": stimulus.type,
        }
        voice_on = (
            self.settings.voice_enabled
            and self._voice_fresh(stimulus)
            and self.tts is not None
            and self.tts.available
        )
        if voice_on:
            voice = pick_voice(self.settings, stimulus.type, stimulus.priority, marker)
            task = asyncio.get_running_loop().create_task(
                self._send_audio(replica_id, text, voice)
            )
```

(остальное тело без изменений).

- [ ] **Step 3: main.py — новое имя класса**

Строка 31: `from .tts import ChatterboxTTS`; строка 178: `tts = ChatterboxTTS(on_status=set_tts_status)`.

- [ ] **Step 4: Проверить**

Run: `python -m py_compile src/stream_director/broadcast.py src/stream_director/main.py src/stream_director/config.py && ruff check src`
Expected: чисто. `grep -rn "S1MiniTTS\|strip_markers" src/` — пусто.

---

### Task 7: server.py — API голосов и настроек

**Files:**
- Modify: `src/stream_director/server.py:36, 84-86 (SettingsIn), 306-323 (voices)`

**Interfaces:**
- Consumes: `save_voice(name, wav)`, `EMOTION_MARKERS`, `Settings.voice_by_marker`.
- Produces: `GET /api/voices` → `{"voices": [...], "markers": [...]}`; `POST /api/voices` без поля `transcript`; `PATCH`-путь настроек принимает `voice_by_marker`.

- [ ] **Step 1: Импорт маркеров**

Строка 36:

```python
from .tts import DEFAULT_VOICE, EMOTION_MARKERS, delete_voice, list_voices, save_voice
```

- [ ] **Step 2: SettingsIn — поле voice_by_marker**

Рядом с `voice_overrides` (строка 86):

```python
    voice_by_marker: dict[str, str] | None = None
```

(Механизм применения настроек общий — отдельного кода не нужно, если PATCH копирует не-None поля; проверить по соседним dict-полям, что voice_by_marker попадает в тот же цикл.)

- [ ] **Step 3: /api/voices — маркеры в ответе, аплоад без транскрипта**

```python
    @app.get("/api/voices")
    async def get_voices():
        return {"voices": list_voices(), "markers": list(EMOTION_MARKERS)}

    @app.post("/api/voices", status_code=201)
    async def add_voice(name: str = Form(...), file: UploadFile = File(...)):
        data = await file.read()
        if len(data) > 15 * 2**20:
            raise HTTPException(400, "референс больше 15 МБ")
        if not data.startswith(b"RIFF"):
            raise HTTPException(400, "нужен WAV-файл (рекомендация по длине — из спайка)")
        try:
            save_voice(name, data)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"ok": True}
```

Текст подсказки в HTTPException взять из раздела «Референс» spike-chatterbox-results.md (например «нужен WAV-файл (около 10 секунд чистой речи)»).

- [ ] **Step 4: Проверить**

Run: `python -m py_compile src/stream_director/server.py && ruff check src/stream_director/server.py`
Expected: чисто. `grep -n "transcript" src/stream_director/server.py` — пусто.

---

### Task 8: Панель — api.ts и Panel.tsx

**Files:**
- Modify: `web/src/shared/api.ts:26-28, 114-126`
- Modify: `web/src/panel/Panel.tsx:57-61, 96, 505-527 (образец блока), 577-599 (форма аплоада)`

**Interfaces:**
- Consumes: `GET /api/voices` → `{voices, markers}`, `POST /api/voices` (name+file), `voice_by_marker` в настройках.
- Produces: рабочая панель (собирается `npm run build`).

- [ ] **Step 1: api.ts**

В `SettingsDto` после `voice_overrides` (строка 28): `voice_by_marker: Record<string, string>;`
`getVoices` (строка 114): `getVoices: () => req<{ voices: string[]; markers: string[] }>("/api/voices"),`
`uploadVoice` (строки 115-124) — убрать transcript:

```typescript
  uploadVoice: (name: string, file: File) => {
    const form = new FormData();
    form.append("name", name);
    form.append("file", file);
    return fetch("/api/voices", { method: "POST", body: form }).then(async (r) => {
```

(хвост функции без изменений).

- [ ] **Step 2: Panel.tsx — состояние и загрузка маркеров**

Удалить `newVoiceText` (строка 60) и все его использования. Добавить состояние маркеров рядом с `voices` (строка 57):

```tsx
  const [markers, setMarkers] = useState<string[]>([]);
```

Строка 96:

```tsx
    api.getVoices().then((v) => { setVoices(v.voices); setMarkers(v.markers); }).catch(() => {});
```

- [ ] **Step 3: Panel.tsx — форма аплоада без транскрипта**

В блоке 577-599: подсказку заменить на текст из раздела «Референс» спайка (например: «Свой голос: WAV ~10 секунд чистой речи, текст не нужен. «default» — голос модели.»); удалить `<input ... value={newVoiceText} ...>`; условие кнопки: `if (!newVoiceName.trim() || !newVoiceFile) return;`; вызов: `api.uploadVoice(newVoiceName.trim(), newVoiceFile)`.

- [ ] **Step 4: Panel.tsx — блок «Голос по эмоции»**

Вставить после блока `voice_overrides` (после строки ~575), по образцу блока `voice_by_priority` (строки 505-527):

```tsx
        <h3>Голос по эмоции</h3>
        <p className="hint">Маркер, который LLM ставит в реплике, может включать свой референс. Пусто — действуют правила ниже по приоритету.</p>
        {markers.map((m) => (
          <div className="row" key={m}>
            <label>({m})</label>
            <select
              value={settings.voice_by_marker[m] ?? ""}
              onChange={(e) => {
                const next = { ...settings.voice_by_marker };
                if (e.target.value) next[m] = e.target.value;
                else delete next[m];
                patch({ voice_by_marker: next });
              }}
            >
              <option value="">—</option>
              {voices.map((v) => (
                <option key={v} value={v}>{v}</option>
              ))}
            </select>
            {settings.voice_by_marker[m] && (
              <button onClick={() => preview(settings.voice_by_marker[m])}>прослушать</button>
            )}
          </div>
        ))}
```

Классы/структуру строк скопировать из фактического блока voice_by_priority (если там другие имена классов — использовать их, образец главнее этого снипета).

- [ ] **Step 5: Собрать фронт**

Run: `cd web && rtk npm run build`
Expected: сборка без ошибок TypeScript.

---

### Task 10: Ударения — RUAccent в worker (выполняется ПЕРЕД Task 9)

Основание: вердикт пользователя «ударения работают» по A/B `stress_plain.wav` /
`stress_marked.wav`; факты спайка RUAccent — turbo-модель, 0–172 мс/фраза,
загрузка ~15 с, workdir ~465 МБ.

**Files:**
- Modify: `src/stream_director/tts/pins.py` (RUNTIME_PACKAGES + WEIGHTS)
- Modify: `src/stream_director/tts/bootstrap.py` (распаковка zip-весов)
- Modify: `src/stream_director/tts/worker.py` (аккцентизация перед синтезом)

**Interfaces:**
- Consumes: ассет `ruaccent-data.zip` в релизе `models-chatterbox-v1` (sha256 см. в шаге заливки).
- Produces: worker, который сам расставляет ударения; контракт `/synth` не меняется.

- [ ] **Step 1: pins.py — пакеты и архив данных**

В `RUNTIME_PACKAGES` добавить (версии — из pip freeze venv спайка после установки ruaccent):

```python
    "ruaccent==1.5.8.3",
    "onnxruntime==1.27.0",
    "razdel==0.5.0",
    "python-crfsuite==0.9.12",
    "sentencepiece==0.2.2",
    "flatbuffers==25.12.19",
]
```

В `WEIGHTS` добавить (sha256/размер — из шага заливки ассета):

```python
    # Архив данных RUAccent (словари + ONNX + koziev): bootstrap распаковывает
    # его в MODEL_DIR/ruaccent. koziev/ — подпакет (rupostagger+rulemma), который
    # RUAccent.load() безусловно докачивает с HF в каталог ПАКЕТА (не workdir!)
    # ещё до гарда tiny_mode — оффлайн это краш/зависание (найдено живым прогоном
    # Task 9). worker копирует koziev из зеркала в пакет перед load().
    "ruaccent-data.zip": ("4b9e2f764b009f27ca0b55f3b4c81e70c48e657fa76f230cd8c9b2ddba5eca4a", 518955327),
```

- [ ] **Step 2: bootstrap.py — распаковка zip после докачки**

В `ensure_weights`, после проверки sha256 и записи ok-маркера, добавить:

```python
        if name.endswith(".zip"):
            unpack_dir = MODEL_DIR / name.removesuffix(".zip").split("-")[0]
            if not (unpack_dir / ".unpacked").is_file():
                import zipfile
                with zipfile.ZipFile(dest) as z:
                    z.extractall(unpack_dir)
                (unpack_dir / ".unpacked").write_text("ok")
```

(даёт `MODEL_DIR/ruaccent/…`; сам zip остаётся лежать — он же под sha-контролем докачки).

- [ ] **Step 3: worker.py — аккцентизация**

После `load_engine()` добавить загрузку и хелпер:

```python
ACCENT = None
_PLUS_RE = re.compile(r"\+(.)")


def load_accentizer():
    from ruaccent import RUAccent

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
```

В `synth_wav` первой строкой: `text = accentize(text)`. В `main()` после
`ENGINE = load_engine()`: `ACCENT = load_accentizer()` (до `PHASE = ready`).
Добавить `import re` в шапку, если его нет.

- [ ] **Step 4: Проверить оффлайн-поведение RUAccent**

Прочитать код установленного пакета (`venv/Lib/site-packages/ruaccent/`):
убедиться, что при полном workdir он НЕ ходит в сеть (иначе — задать
`HF_HUB_OFFLINE=1` в окружении worker-процесса в client.py и записать это
в notes). Прогнать компиляцию: `python -m py_compile` всех трёх файлов.

- [ ] **Step 5: Смок в venv спайка**

Из venv спайка запустить worker против `weights_v3` + `ruaccent-data` и дёрнуть
`/synth` фразой «Он стоит на мосту и стоит дорого» — в ответе валидный WAV.

---

### Task 9: Живой прогон, уборка, README

**Files:**
- Modify: `README.md` (раздел «Голос»)
- Delete (руками, вне git): см. Step 3

**Interfaces:**
- Consumes: всё предыдущее; скилл `verify` (`.claude/skills/verify/SKILL.md`).

- [ ] **Step 1: Первый запуск с чистым рантаймом**

Запустить приложение из репо (`python -m stream_director` c `src` в path — как принято в проекте). Отпечаток пинов изменился → старый gpu-runtime снесётся и поставится новый; затем докачаются веса с models-chatterbox-v1. Дождаться `tts_status == ready` в `GET /api/status`.
Expected: бейдж «голос» зелёный; в логах нет fish_speech.

- [ ] **Step 2: Проверка по спеке (verify)**

```powershell
curl -X POST http://127.0.0.1:8710/api/tts/preview -H "Content-Type: application/json" -d '{"text": "Проверка голоса. Раз, два, три.", "voice": "default"}' -o t_neutral.wav
curl -X POST http://127.0.0.1:8710/api/tts/preview -H "Content-Type: application/json" -d '{"text": "(whispering) Проверка голоса. Раз, два, три.", "voice": "default"}' -o t_whisper.wav
curl -X POST http://127.0.0.1:8710/api/tts/preview -H "Content-Type: application/json" -d '{"text": "(shouting) Проверка голоса. Раз, два, три.", "voice": "default"}' -o t_shout.wav
```

Expected: три валидных WAV, на слух (пользователь) шёпот/крик различимы; загрузка референса через панель работает без поля транскрипта; тембр узнаваем; `GET /health` worker-а показывает VRAM меньше, чем было у S1-mini (~6 ГБ); время фразы укладывается в 20 с.

- [ ] **Step 3: Уборка старого (только после успешных Step 1-2)**

```powershell
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\StreamDirector\models\s1-mini" -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force build\s1-mini
Remove-Item -Force build\cache\silero_v4_ru.pt
Remove-Item build\spike-chatterbox\venv -Recurse -Force   # weights оставить до выкладки релиза; после Задачи 2 можно снести целиком
Get-ChildItem "$env:LOCALAPPDATA\StreamDirector\data\voices\*.txt" | Remove-Item
rtk gh release delete models-s1-mini-v1 --yes --cleanup-tag --repo Lakai4eg/game-ai-commentator
```

Если запуск из репо (без STREAM_DIRECTOR_HOME) — старые модели лежат в `.\models\s1-mini`, снести и их.

- [ ] **Step 4: README**

В описании и разделе «Голос»: «OpenAudio S1-mini» → «Chatterbox Multilingual (Resemble AI, лицензия MIT)»; упомянуть: транскрипт для своего голоса больше не нужен; если спайк зафиксировал torch 2.6.0 — добавить строку об отсутствии поддержки RTX 50xx (Blackwell). Проверить `grep -rn "S1-mini\|s1-mini\|OpenAudio" README.md docs/ src/ web/src/` — упоминаний не осталось (кроме спеки и этого плана — они история).

---

## Self-Review (выполнен)

- Покрытие спеки: риск №1 (`--no-deps`, torch/Blackwell) → Задачи 1, 3, 9; манифест весов и v2/v3 → Задачи 1–3; разбор маркера в client.synth + повторный в broadcast → Задачи 5–6; paths.py → Задача 3; voice_by_marker сквозь config/server/api/панель → Задачи 6–8; сироты `.txt` → Задачи 5 (delete_voice) и 9 (уборка); проверка и README → Задача 9.
- Типы сквозные: `parse -> tuple[str | None, str]`, `pick_voice(..., marker: str | None)`, `/synth` c `exaggeration/cfg_weight` — согласованы между задачами 4, 5, 6, 7.
- Тестов нет намеренно (запрет проекта), коммитов нет намеренно (запрет пользователя) — шаги TDD заменены компиляцией, ruff и живым прогоном.
