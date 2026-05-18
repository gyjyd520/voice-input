"""Vosk streaming engine."""

import json
import threading

from voice_input.config import RATE, VOSK_MODEL_PATH
from voice_input.notify import notify


class VoskEngine:
    def __init__(self):
        self.model = None
        self.rec = None
        self._lock = threading.Lock()

    def load(self):
        if self.model is not None:
            return True
        if not VOSK_MODEL_PATH.exists():
            notify("⚠️ Vosk 模型未找到", "", "dialog-error")
            return False
        try:
            import vosk
            old = _redirect_stderr()
            try:
                self.model = vosk.Model(str(VOSK_MODEL_PATH))
            finally:
                _restore_stderr(old)
            return self.reset()
        except Exception as e:
            notify("❌ Vosk 加载失败", str(e), "dialog-error")
            return False

    def reset(self):
        with self._lock:
            try:
                import vosk
                self.rec = vosk.KaldiRecognizer(self.model, RATE)
                self.rec.SetWords(False)
                return True
            except Exception:
                return False

    def feed(self, data):
        with self._lock:
            if not self.rec or not data:
                return "", ""
            try:
                if self.rec.AcceptWaveform(data):
                    r = json.loads(self.rec.Result())
                    return "", r.get("text", "")
                else:
                    p = json.loads(self.rec.PartialResult())
                    return p.get("partial", ""), ""
            except Exception:
                return "", ""

    def final(self):
        with self._lock:
            if not self.rec:
                return ""
            try:
                r = json.loads(self.rec.FinalResult())
                return r.get("text", "")
            except Exception:
                return ""


def _redirect_stderr():
    """Redirect fd 2 to /dev/null. Returns old fd copy."""
    import os
    devnull = os.open(os.devnull, os.O_WRONLY)
    old = os.dup(2)
    os.dup2(devnull, 2)
    os.close(devnull)
    return old


def _restore_stderr(old):
    """Restore fd 2."""
    import os
    os.dup2(old, 2)
    os.close(old)
