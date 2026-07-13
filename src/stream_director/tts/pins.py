"""Пины GPU-стека: версии пакетов, индекс torch, манифест весов.

Значения — только из docs/superpowers/plans/spike-chatterbox-results.md
(проверено на живой машине). Руками не менять.
"""

TORCH_INDEX_URL = "https://download.pytorch.org/whl/cu128"  # cu126 НЕ содержит sm_120 (RTX 50xx) — проверено спайком
# Зависимости chatterbox-tts, поставленные явно: сам пакет идёт --no-deps,
# потому что его пины (gradio==6.8.0, torch==2.6.0) тянут лишнее и старое.
RUNTIME_PACKAGES = [
    "torch==2.11.0+cu128",
    "torchaudio==2.11.0+cu128",
    "transformers==5.13.1",
    "diffusers==0.39.0",
    "librosa==0.11.0",
    "s3tokenizer==0.3.0",
    "resemble-perth==1.0.1",
    "conformer==0.3.2",
    "safetensors==0.8.0",
    "omegaconf==2.3.1",
    "numpy==2.4.6",
    "spacy-pkuseg==1.0.1",
    "pykakasi==2.3.0",
    "pyloudnorm==0.2.0",
    # Ударения: RUAccent + его зависимости (обычная установка, конфликтов с
    # пинами chatterbox нет — проверено спайком).
    "ruaccent==1.5.8.3",
    "onnxruntime==1.27.0",
    "razdel==0.5.0",
    "python-crfsuite==0.9.12",
    "sentencepiece==0.2.2",
    "flatbuffers==25.12.19",
]
# Ставятся вторым проходом pip с --no-deps.
NO_DEPS_PACKAGES = ["chatterbox-tts==0.1.7"]

WEIGHTS_BASE_URL = (
    "https://github.com/Lakai4eg/game-ai-commentator/releases/download/models-chatterbox-v1"
)
# имя файла → (sha256, размер в байтах). Из spike-chatterbox-results.md.
WEIGHTS: dict[str, tuple[str, int]] = {
    # Имя чекпоинта зашито в chatterbox (mtl_tts.py:179) как *_v2: выбран v3,
    # его файл лежит в релизе и в манифесте ПОД ЭТИМ ЖЕ ИМЕНЕМ; sha и размер —
    # содержимого v3.
    "t3_mtl23ls_v2.safetensors": (
        "5abca8321ede76f8e61f1cc0d19aea6c946b28871017ce8726f8a69203f05953", 2143989928),
    "s3gen.pt": ("9b9ff07e60b20c136e2b1b3d7563a24604e8d2c4c267888d1ee929dd0151d2a3", 1057165844),
    "ve.pt": ("4b16d836bc598509860f6fa068165a8bb5e9ac84f05582dfcf278a5a372879f1", 5698626),
    "grapheme_mtl_merged_expanded_v1.json": (
        "69632f47220a788a52ce2661d096453c5655e9bf25289d89a8d832c46ee07dbf", 69989),
    "conds.pt": ("6552d70568833628ba019c6b03459e77fe71ca197d5c560cef9411bee9d87f4e", 107374),
    # Архив данных RUAccent (словари + ONNX + koziev): bootstrap распаковывает
    # его в MODEL_DIR/ruaccent. Худший вариант — RUAccent сам пошёл бы на HF
    # с машины пользователя, где HF может быть недоступен. koziev/ — подпакет,
    # который ruaccent докачивает в каталог ПАКЕТА; worker подкладывает его
    # оттуда перед load().
    "ruaccent-data.zip": (
        "4b9e2f764b009f27ca0b55f3b4c81e70c48e657fa76f230cd8c9b2ddba5eca4a", 518955327),
}
