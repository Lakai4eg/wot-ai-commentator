"""Озвучка Chatterbox Multilingual: worker-подпроцесс на GPU + вспомогательные части."""

from .audio import AudioStore
from .client import ChatterboxTTS
from .markers import EMOTION_MARKERS, parse
from .voices import DEFAULT_VOICE, delete_voice, list_voices, pick_voice, save_voice

__all__ = [
    "AudioStore", "ChatterboxTTS", "EMOTION_MARKERS", "parse",
    "DEFAULT_VOICE", "delete_voice", "list_voices", "pick_voice", "save_voice",
]
