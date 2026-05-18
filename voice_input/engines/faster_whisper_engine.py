"""Faster-Whisper engine — CTranslate2 backend, ~4x faster on CPU."""

import os
import re

from voice_input.notify import notify

FASTER_WHISPER_MODEL_DIR = os.path.expanduser("~/.local/share/faster-whisper/models")

# Known hallucination patterns on non-speech/noise input
_HALLUCINATION_PATTERNS = [
    re.compile(r"字幕\s*by\s*索兰娅", re.I),
    re.compile(r"字幕\s*by\s*[a-z]+", re.I),
    re.compile(r"^[\s,.，。！？!?]+$"),  # punctuation-only
]


class FasterWhisperEngine:
    def __init__(self, model_name="small"):
        self.model_name = model_name
        self.model = None

    def _model_path(self):
        return os.path.join(FASTER_WHISPER_MODEL_DIR, self.model_name)

    def load(self):
        if self.model is not None:
            return True
        try:
            from faster_whisper import WhisperModel
            local = self._model_path()
            if os.path.exists(local) and os.path.exists(os.path.join(local, "model.bin")):
                self.model = WhisperModel(
                    local, device="cpu", compute_type="int8",
                    num_workers=2, cpu_threads=4, local_files_only=True)
            else:
                self.model = WhisperModel(
                    self.model_name, device="cpu", compute_type="int8",
                    num_workers=2, cpu_threads=4)
            return True
        except ImportError:
            notify("⚠️ faster-whisper 未安装", "pip install faster-whisper", "dialog-error")
            return False
        except Exception as e:
            notify("❌ Faster-Whisper 加载失败", str(e)[:80], "dialog-error")
            return False

    def transcribe(self, wav_path):
        if not self.model:
            return ""
        segments, info = self.model.transcribe(
            wav_path, language="zh", beam_size=5,
            vad_filter=True,
            vad_parameters=dict(
                threshold=0.5,
                min_speech_duration_ms=250,
                min_silence_duration_ms=300,
            ),
        )
        # If VAD found no speech, return empty
        if getattr(info, 'duration_after_vad', 1.0) <= 0.01:
            return ""
        text = "".join(seg.text for seg in segments).strip()
        return _filter_hallucination(text)


def _filter_hallucination(text):
    """Remove known hallucination patterns from model output."""
    for pat in _HALLUCINATION_PATTERNS:
        text = pat.sub("", text)
    return text.strip()
