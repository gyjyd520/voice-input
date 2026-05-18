"""OpenAI Whisper engine."""

import re

from voice_input.notify import notify

_HALLUCINATION_PATTERNS = [
    re.compile(r"字幕\s*by\s*索兰娅", re.I),
    re.compile(r"字幕\s*by\s*[a-z]+", re.I),
]


class WhisperEngine:
    """OpenAI Whisper — local offline."""

    def __init__(self, model_name="small"):
        self.model_name = model_name
        self.model = None

    def load(self):
        if self.model is not None:
            return True
        try:
            import whisper
            self.model = whisper.load_model(self.model_name)
            return True
        except ImportError:
            notify("⚠️ Whisper 未安装", "pip install openai-whisper", "dialog-error")
            return False
        except Exception as e:
            notify("❌ Whisper 加载失败", str(e), "dialog-error")
            return False

    def transcribe(self, wav_path):
        if not self.model:
            return ""
        result = self.model.transcribe(
            wav_path, language="zh",
            no_speech_threshold=0.6,
            logprob_threshold=-1.0,
            compression_ratio_threshold=2.4,
        )
        text = result["text"].strip()
        for pat in _HALLUCINATION_PATTERNS:
            text = pat.sub("", text)
        return text.strip()
