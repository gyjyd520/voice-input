"""Engine base class and dispatch."""

from voice_input.engines.vosk_engine import VoskEngine
from voice_input.engines.whisper_engine import WhisperEngine
from voice_input.engines.faster_whisper_engine import FasterWhisperEngine
from voice_input.engines.google_engine import GoogleEngine


def get_engine_class(name):
    """Return engine class by name."""
    engines = {
        "vosk": VoskEngine,
        "whisper": WhisperEngine,
        "faster-whisper": FasterWhisperEngine,
        "google": GoogleEngine,
    }
    return engines.get(name)
