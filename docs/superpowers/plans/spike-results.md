# Spike-протокол: GPU-стек S1-mini на живой машине (Task 1)

Дата: 2026-07-12. Машина: Windows 11, NVIDIA GPU (`torch.cuda.is_available() == True`).
Python: 3.12.10 (`C:\Users\lakai\AppData\Local\Programs\Python\Python312\python.exe`), pip 25.0.1.

## Итог

- Установка через `pip install --target` под Python 3.12 на Windows **работает**,
  но команде из плана нужны четыре дополнительных пина (см. «Конфликты» ниже).
- CUDA-индекс: **cu126** — torch нашёлся, fallback на cu124 не понадобился.
- Импорты worker-а (Task 5) подтверждены как в плане, **плюс обязательный
  файл-маркер `.project-root`** в корне рантайма (см. ниже).
- Размер `build/spike-runtime/`: **5.4 ГБ**, 173 пакета.

## Источник fish-speech

**PyPI, `fish-speech==0.1.0`** — git-тег не понадобился. Номер версии обманчив:
wheel с PyPI содержит код эпохи S1 (`fish_speech/inference_engine/`,
`fish_speech/models/dac/` с `modded_dac_vq`, `fish_speech/utils/schema.py`
с `ServeTTSRequest`/`ServeReferenceAudio`) — тот же API, что в теге `v2.0.0-beta`.

## Конфликты резолвера и их решение

Команда из плана в лоб (`pip install --target ... torch fish-speech`) **падает**:

1. `descript-audiotools` (зависимость fish-speech) требует `protobuf<3.20`,
   а свежие `wandb` — `protobuf>4.21`. Резолвер откатывает wandb до 0.15.12,
   тот тянет `pathtools==0.1.2` — sdist 2015 года, чей setup.py делает
   `import imp` и не собирается на Python 3.12 (`ModuleNotFoundError: No module named 'imp'`).
   Решение: пины `protobuf==3.19.6` (есть универсальный wheel `py2.py3-none-any`)
   и `wandb==0.19.11` (новейший wandb, допускающий protobuf 3.19.x; pathtools не тянет).
2. PyPI-метаданные fish-speech не ограничивают transformers сверху, и резолвер
   берёт 5.x. Upstream-репозиторий (тег `v2.0.0-beta`) сам ограничивает
   `transformers<=4.57.3` — зафиксировано `transformers==4.57.3`.
3. `transformers==4.57.3` требует `huggingface-hub<1.0`, а `gradio` 6.x —
   `huggingface-hub>=1.2` (несовместимо). Зафиксировано `gradio==5.50.0`
   (fish-speech требует лишь `gradio>5.0.0`; worker gradio не импортирует).

Рабочая команда установки (одним вызовом, проверено — exit 0):

```
python -m pip install --target build/spike-runtime ^
  --index-url https://download.pytorch.org/whl/cu126 ^
  --extra-index-url https://pypi.org/simple ^
  torch fish-speech "protobuf==3.19.6" "wandb==0.19.11"
```

после чего transformers/gradio доводились до пинов выше отдельными вызовами
(`--upgrade` при `--target` перезаписывает общие зависимости — приходилось
удерживать `numpy==1.26.4`, `fsspec==2024.2.0`, `pydantic==2.9.2` и подчищать
осиротевшие dist-info). Bootstrap (Task 3) ставит всё **одним** вызовом с полным
списком пинов — этой возни у него не будет. `pip check` итогового каталога:
`No broken requirements found.`

## Значения для `pins.py` (Task 2)

```python
TORCH_INDEX_URL = "https://download.pytorch.org/whl/cu126"
RUNTIME_PACKAGES = [
    "torch==2.13.0",           # с индекса cu126 резолвится в 2.13.0+cu126
    "fish-speech==0.1.0",
    # Пины, разрубающие конфликты резолвера (см. протокол):
    "transformers==4.57.3",    # кэп upstream: с 5.x рантайм не тестировался
    "protobuf==3.19.6",        # descript-audiotools требует <3.20
    "wandb==0.19.11",          # новее — требует protobuf>4.21
    "gradio==5.50.0",          # 6.x требует huggingface-hub>=1.2 (конфликт с transformers)
]
```

Ключевые фактические версии (полный список — `pip list --path build/spike-runtime`):
`torch==2.13.0+cu126`, `torchaudio==2.11.0+cu126`, `fish-speech==0.1.0`,
`transformers==4.57.3`, `tokenizers==0.22.2`, `huggingface_hub==0.36.2`,
`numpy==1.26.4`, `pydantic==2.9.2`, `protobuf==3.19.6`, `wandb==0.19.11`,
`gradio==5.50.0`, `tensorboard==2.20.0`, `datasets==2.18.0`, `fsspec==2024.2.0`,
`tiktoken==0.13.0`, `descript-audio-codec==1.0.0`, `descript-audiotools==0.7.2`,
`lightning==2.6.5`, `PyAudio==0.2.14` (wheel cp312 есть).

## Обязательный маркер `.project-root` (для Task 3 / Task 5)

`fish_speech/models/dac/inference.py` на импорте вызывает
`pyrootutils.setup_root(__file__, indicator=".project-root", pythonpath=True)` —
ищет файл `.project-root` вверх по дереву. pip этот файл не устанавливает,
без него импорт падает `FileNotFoundError: Project root directory not found`.
Решение: создать **пустой** файл `.project-root` в корне каталога рантайма
(`gpu-runtime/.project-root`). В спайке хватило `touch build/spike-runtime/.project-root`.
Логичное место — `ensure_runtime()` в bootstrap, перед записью маркера `.complete`.

## Подтверждённые импорты и сигнатуры (для Task 5)

Проверка из плана прошла дословно (пути импортов менять не нужно):

```
$ python -c "import sys; sys.path.insert(0, 'build/spike-runtime'); import torch; \
    from fish_speech.inference_engine import TTSInferenceEngine; \
    from fish_speech.models.dac.inference import load_model; \
    from fish_speech.models.text2semantic.inference import launch_thread_safe_queue; \
    from fish_speech.utils.schema import ServeTTSRequest, ServeReferenceAudio; \
    print('ok', torch.__version__, torch.cuda.is_available())"
ok 2.13.0+cu126 True
```

(Единственный warning: `triton not found; flop counting will not work` — безвреден,
triton под Windows не ставится и для инференса не нужен.)

Сигнатуры совпадают с кодом worker.py из Task 5:

- `launch_thread_safe_queue(checkpoint_path, device, precision, compile=False)`
- `load_model(config_name, checkpoint_path, device='cuda')` (`config_name="modded_dac_vq"` в коде есть)
- `TTSInferenceEngine(llama_queue, decoder_model, precision, compile)`
- `ServeTTSRequest`: поля `text, references, format, streaming, max_new_tokens,
  chunk_length, top_p, repetition_penalty, temperature` (+ `normalize, reference_id,
  seed, use_memory_cache`)
- `ServeReferenceAudio(audio, text)`
- `TTSInferenceEngine.inference()` — генератор `InferenceResult` с
  `code ∈ {"header", "segment", "error", ...}` и полем `audio` — проверка
  `result.code == "error"` / `result.audio is not None` в worker.py корректна.
