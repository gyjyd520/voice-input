"""OpenAI Whisper engine."""

from voice_input.notify import notify


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
            initial_prompt="以下是简体中文普通话的句子。")
        return result["text"].strip()
