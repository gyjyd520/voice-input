"""Google Web Speech API engine — online, high Chinese accuracy."""

from voice_input.notify import notify


class GoogleEngine:
    """Google Web Speech API — online, high Chinese accuracy."""

    def transcribe(self, wav_path):
        import speech_recognition as sr
        r = sr.Recognizer()
        try:
            with sr.AudioFile(wav_path) as source:
                audio = r.record(source)
            return r.recognize_google(audio, language="zh-CN")
        except sr.UnknownValueError:
            return ""
        except sr.RequestError as e:
            notify("❌ Google API 错误", str(e), "dialog-error")
            return ""
