"""Пины GPU-стека: версии пакетов, индекс torch, манифест весов.

Значения меняются только после проверки на живой машине (spike / Task 11).
Пустой sha256 — маркер «зеркало ещё не опубликовано»: bootstrap честно
останавливается с ошибкой, а не качает непроверенное.
"""

TORCH_INDEX_URL = "https://download.pytorch.org/whl/cu126"  # из spike-results.md
RUNTIME_PACKAGES = [
    # Проверено спайком на живой машине: docs/superpowers/plans/spike-results.md.
    "torch==2.13.0",  # с индекса cu126 резолвится в 2.13.0+cu126
    "fish-speech==0.1.0",
    # Пины, разрубающие конфликты резолвера под Python 3.12 (см. spike-протокол):
    "transformers==4.57.3",  # кэп upstream: с 5.x рантайм не тестировался
    "protobuf==3.19.6",  # descript-audiotools требует <3.20
    "wandb==0.19.11",  # новее — требует protobuf>4.21
    "gradio==5.50.0",  # 6.x требует huggingface-hub>=1.2 (конфликт с transformers)
]

WEIGHTS_BASE_URL = (
    "https://github.com/Lakai4eg/game-ai-commentator/releases/download/models-s1-mini-v1"
)
# имя файла → (sha256, размер в байтах). Посчитано по официальным файлам
# fishaudio/openaudio-s1-mini (ModelScope, размеры совпадают с HF байт в байт).
WEIGHTS: dict[str, tuple[str, int]] = {
    "model.pth": ("9e59be7dc6714040dce3cde1f41e730c2f0daa5339785b1cd3b60041208c35e6", 1735122974),
    "codec.pth": ("74fc41c5a7151c6f350af8bd7e5d6e3accfcc7f3dfbfac23afd35af07052bb2f", 1871099728),
    "tokenizer.tiktoken": ("b2b1b8dfb5cc5f024bafc373121c6aba3f66f9a5a0269e243470a1de16a33186", 2561218),
    "special_tokens.json": ("efc4b254afdf5c3898dc26bd2b7791a9cae993747d92a3176cf9ce4ccfe40526", 126275),
    "config.json": ("0e46c32fd452751d48a553283e16a30819ea7321b32b9bf9e4a8b9c9cc02fa50", 844),
}
