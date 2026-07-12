"""Озвучка OpenAudio S1-mini: worker-подпроцесс на GPU + вспомогательные части."""

from .audio import AudioStore
from .client import S1MiniTTS
from .markers import EMOTION_MARKERS, strip_markers
from .voices import DEFAULT_VOICE, delete_voice, list_voices, pick_voice, save_voice

__all__ = [
    "AudioStore", "S1MiniTTS", "EMOTION_MARKERS", "strip_markers",
    "DEFAULT_VOICE", "delete_voice", "list_voices", "pick_voice", "save_voice",
]
