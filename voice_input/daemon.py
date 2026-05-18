"""Voice daemon — background process with hotkey trigger and optional GTK OSD."""

import os
import threading
import time
from pathlib import Path

from voice_input.config import get_config
from voice_input.audio import _find_mic_source, _apply_mic_gain, beep, record_audio_batch, record_recognize
from voice_input.input import paste_text, clip
from voice_input.notify import notify
from voice_input.engines import VoskEngine, WhisperEngine, FasterWhisperEngine, GoogleEngine


class VoiceDaemon:
    def __init__(self):
        self.vosk_engine = VoskEngine()
        self.whisper_engine = None
        self.faster_whisper_engine = None
        self.running = False
        self._recording = False
        self._stop_requested = False
        self._fifo_path = Path("/tmp/voice-input.fifo")
        self._pid_file = Path("/tmp/voice-input-daemon.pid")
        self._osd = None
        self._osd_ready = threading.Event()
        self._use_gtk = False

    def start(self):
        if self._pid_file.exists():
            pid = self._pid_file.read_text().strip()
            if pid and os.path.exists(f"/proc/{pid}"):
                print(f"已在运行 (PID={pid})")
                return
        self._pid_file.write_text(str(os.getpid()))

        config = get_config()
        engine_name = config.get("engine", "vosk")
        source = _find_mic_source()
        if source:
            _apply_mic_gain(source)

        if engine_name == "faster-whisper":
            model_name = config.get("whisper_model", "small")
            print(f"🎙️  加载 Faster-Whisper {model_name} 模型...")
            self.faster_whisper_engine = FasterWhisperEngine(model_name)
            if not self.faster_whisper_engine.load():
                return
        elif engine_name == "whisper":
            model_name = config.get("whisper_model", "small")
            print(f"🎙️  加载 Whisper {model_name} 模型...")
            self.whisper_engine = WhisperEngine(model_name)
            if not self.whisper_engine.load():
                return
        elif engine_name == "vosk":
            print("🎙️  加载 Vosk 模型...")
            if not self.vosk_engine.load():
                return

        self.running = True

        # FIFO for GNOME hotkey trigger
        self._fifo_path.unlink(missing_ok=True)
        os.mkfifo(self._fifo_path)
        threading.Thread(target=self._fifo_loop, daemon=True).start()

        # Try GTK main loop if OSD enabled; fall back to sleep loop
        use_osd = config.get("osd_enabled", True)
        if use_osd:
            try:
                self._use_gtk = True
                self._start_gtk_loop()
                return
            except Exception as e:
                print(f"⚠️  GTK OSD 不可用 ({e})，使用通知模式")

        self._use_gtk = False
        print("✅ 就绪！Ctrl+Space 开始录音，说完自动停止")
        notify("🎙️ 语音输入已就绪", "Ctrl+Space 开始，说完自动停止")
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            self.running = False
            self._fifo_path.unlink(missing_ok=True)
            self._pid_file.unlink(missing_ok=True)

    def _start_gtk_loop(self):
        """Start GTK3 main loop on the main thread."""
        import gi
        gi.require_version('Gtk', '3.0')
        from gi.repository import Gtk, GLib

        print("✅ 就绪！Ctrl+Space 开始录音，说完自动停止")
        notify("🎙️ 语音输入已就绪", "Ctrl+Space 开始，说完自动停止")

        try:
            Gtk.main()
        except KeyboardInterrupt:
            pass
        finally:
            self.running = False
            self._fifo_path.unlink(missing_ok=True)
            self._pid_file.unlink(missing_ok=True)

    def _fifo_loop(self):
        while self.running:
            try:
                with open(self._fifo_path, "r") as f:
                    cmd = f.read().strip()
                    if cmd == "record":
                        if self._use_gtk:
                            # Schedule trigger on GTK thread
                            import gi
                            gi.require_version('Gtk', '3.0')
                            from gi.repository import GLib
                            GLib.idle_add(self._on_trigger_gtk)
                        else:
                            threading.Thread(target=self._on_trigger, daemon=True).start()
            except Exception:
                time.sleep(0.1)

    def _on_trigger_gtk(self):
        """Called on GTK main thread. Handles start/stop toggle."""
        if self._recording:
            self._stop_requested = True
        else:
            # Create/ensure OSD on GTK thread
            if self._osd is None:
                from voice_input.ui.osd import OsdWindow
                self._osd = OsdWindow()
            self._osd.show_recording()
            # Start recording in background thread
            threading.Thread(target=self._on_record_gtk, daemon=True).start()
        return False  # Don't repeat idle callback

    def _on_trigger(self):
        """Non-GTK mode trigger handler."""
        if self._recording:
            self._stop_requested = True
        else:
            threading.Thread(target=self._on_record, daemon=True).start()

    def _on_record_gtk(self):
        """Recording thread — GTK mode: OSD already created and shown."""
        if self._recording:
            return
        self._recording = True
        self._stop_requested = False
        try:
            self._do_record(osd=self._osd)
        finally:
            self._recording = False

    def _on_record(self):
        """Recording thread — non-GTK mode."""
        if self._recording:
            return
        self._recording = True
        self._stop_requested = False
        try:
            self._do_record(osd=None)
        finally:
            self._recording = False

    def _do_record(self, osd=None):
        config = get_config()
        engine_name = config.get("engine", "vosk")
        do_beep = config.get("beep", True)
        do_input = config.get("auto_input", True)

        if do_beep:
            beep(880)
        source = _find_mic_source()
        if not source:
            notify("❌ 未找到麦克风", "", "dialog-error")
            if osd:
                osd.show_error("未找到麦克风")
            return

        notify("🎤 录音中...", "说完自动停止，或 Ctrl+Space 手动停止", "audio-input-microphone")

        stop_fn = lambda: self._stop_requested

        def _on_level(rms):
            if osd:
                osd.update_level(rms)

        def _on_partial(text):
            if osd:
                osd.update_text(text)

        text = ""
        if engine_name == "google":
            wav_path = record_audio_batch(source, stop_fn=stop_fn, on_level=_on_level)
            if osd:
                osd.show_processing()
            if do_beep:
                beep(660)
            if not wav_path:
                if osd:
                    osd.show_error("未检测到语音")
                else:
                    notify("⚠️ 未检测到语音", "请再试一次", "dialog-warning")
                return
            engine = GoogleEngine()
            text = engine.transcribe(wav_path)
            os.unlink(wav_path)
        elif engine_name == "faster-whisper":
            wav_path = record_audio_batch(source, stop_fn=stop_fn, on_level=_on_level)
            if osd:
                osd.show_processing()
            if do_beep:
                beep(660)
            if not wav_path:
                if osd:
                    osd.show_error("未检测到语音")
                else:
                    notify("⚠️ 未检测到语音", "请再试一次", "dialog-warning")
                return
            if self.faster_whisper_engine is None:
                model = config.get("whisper_model", "small")
                self.faster_whisper_engine = FasterWhisperEngine(model)
                if not self.faster_whisper_engine.load():
                    return
            text = self.faster_whisper_engine.transcribe(wav_path)
            os.unlink(wav_path)
        elif engine_name == "whisper":
            wav_path = record_audio_batch(source, stop_fn=stop_fn, on_level=_on_level)
            if osd:
                osd.show_processing()
            if do_beep:
                beep(660)
            if not wav_path:
                if osd:
                    osd.show_error("未检测到语音")
                else:
                    notify("⚠️ 未检测到语音", "请再试一次", "dialog-warning")
                return
            if self.whisper_engine is None:
                model = config.get("whisper_model", "small")
                self.whisper_engine = WhisperEngine(model)
                if not self.whisper_engine.load():
                    return
            text = self.whisper_engine.transcribe(wav_path)
            os.unlink(wav_path)
        else:
            # vosk streaming
            self.vosk_engine.reset()
            text, _ = record_recognize(self.vosk_engine, source,
                                       on_partial=_on_partial, on_level=_on_level)
            if do_beep:
                beep(660)

        # Post-recognition handling
        if text:
            if osd:
                action = osd.show_result(text)
                if action == "confirm":
                    if do_input:
                        paste_text(text)
                    notify("✅ 已输入", text[:60], "dialog-positive")
                elif action == "edit":
                    clip(text)
                    notify("📋 已复制到剪贴板", text[:60], "edit-paste")
                # discard/timeout → do nothing
                osd.hide()
            else:
                if do_input:
                    paste_text(text)
                notify("✅ 已输入", text[:60], "dialog-positive")
            print(f"📝 {text}")
        else:
            if osd:
                osd.show_error("未识别到语音")
            else:
                notify("⚠️ 未识别到语音", "请再试一次", "dialog-warning")


def _oneshot_impl():
    """Single-shot record → recognize → input."""
    config = get_config()
    cfg_engine = config.get("engine", "vosk")
    do_beep = config.get("beep", True)
    do_input = config.get("auto_input", True)

    if cfg_engine == "google":
        source = _find_mic_source()
        if not source:
            print("❌ 未找到麦克风")
            return
        if do_beep:
            beep(880)
        print("🎤 请说话...")
        notify("🎤 请说话...", "说完自动识别", "audio-input-microphone")
        wav_path = record_audio_batch(source)
        if do_beep:
            beep(660)
        if not wav_path:
            print("⚠️ 未检测到语音")
            return
        print("🔍 识别中...")
        engine = GoogleEngine()
        text = engine.transcribe(wav_path)
        os.unlink(wav_path)
        if text:
            print(f"📝 {text}")
            if do_input:
                paste_text(text)
                notify("✅ 已输入", text[:60], "dialog-positive")
        else:
            notify("⚠️ 未识别到语音", "请再试一次", "dialog-warning")
        return

    if cfg_engine == "faster-whisper":
        source = _find_mic_source()
        if not source:
            print("❌ 未找到麦克风")
            return
        model = config.get("whisper_model", "small")
        engine = FasterWhisperEngine(model)
        print(f"🔍 加载 Faster-Whisper {model} 模型...")
        if not engine.load():
            return
        if do_beep:
            beep(880)
        print("🎤 请说话...")
        notify("🎤 请说话...", "说完自动识别", "audio-input-microphone")
        wav_path = record_audio_batch(source)
        if do_beep:
            beep(660)
        if not wav_path:
            print("⚠️ 未检测到语音")
            return
        print("🔍 识别中...")
        text = engine.transcribe(wav_path)
        os.unlink(wav_path)
        if text:
            print(f"📝 {text}")
            if do_input:
                paste_text(text)
            notify("✅ 已输入", text[:60], "dialog-positive")
        else:
            notify("⚠️ 未识别到语音", "请再试一次", "dialog-warning")
        return

    if cfg_engine == "whisper":
        source = _find_mic_source()
        if not source:
            print("❌ 未找到麦克风")
            return
        model = config.get("whisper_model", "small")
        engine = WhisperEngine(model)
        print("🔍 加载 Whisper 模型...")
        if not engine.load():
            return
        if do_beep:
            beep(880)
        print("🎤 请说话...")
        notify("🎤 请说话...", "说完自动识别", "audio-input-microphone")
        wav_path = record_audio_batch(source)
        if do_beep:
            beep(660)
        if not wav_path:
            print("⚠️ 未检测到语音")
            return
        print("🔍 识别中...")
        text = engine.transcribe(wav_path)
        os.unlink(wav_path)
        if text:
            print(f"📝 {text}")
            if do_input:
                paste_text(text)
                notify("✅ 已输入", text[:60], "dialog-positive")
        else:
            notify("⚠️ 未识别到语音", "请再试一次", "dialog-warning")
        return

    # Default Vosk (streaming)
    engine = VoskEngine()
    print("🎙️  加载模型...")
    if not engine.load():
        return
    source = _find_mic_source()
    if not source:
        print("❌ 未找到麦克风")
        return
    if do_beep:
        beep(880)
    print("🎤 请说话...")
    text, _ = record_recognize(engine, source)
    if do_beep:
        beep(660)
    if text:
        if do_input:
            paste_text(text)
        print(f"📝 {text}")
    else:
        print("⚠️ 未识别到语音")
