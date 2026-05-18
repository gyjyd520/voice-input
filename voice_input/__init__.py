"""voice_input - Voice input package."""

from voice_input.config import get_config, CONFIG_DIR, CONFIG_FILE, RATE
from voice_input.audio import (
    _find_mic_source, _apply_mic_gain, beep,
    record_audio_batch, record_recognize,
)
from voice_input.input import clip, paste_text
from voice_input.notify import notify
from voice_input.daemon import VoiceDaemon
from voice_input.hotkey import install_hotkey, remove_hotkey
from voice_input.service import install_service, remove_service
from voice_input.config_wizard import interactive_config
from voice_input.test import test_mic
from voice_input.engines import VoskEngine, WhisperEngine, FasterWhisperEngine, GoogleEngine


def oneshot():
    """Single-shot record → recognize → input."""
    from voice_input.daemon import _oneshot_impl
    _oneshot_impl()


def main():
    """CLI entry point. Parses args and dispatches."""
    import argparse
    import os
    import sys
    import subprocess
    import json
    from pathlib import Path

    parser = argparse.ArgumentParser(description="🎙️ 实时语音输入")
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument("--oneshot", action="store_true")
    parser.add_argument("--trigger", action="store_true")
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--install-hotkey", action="store_true")
    parser.add_argument("--remove-hotkey", action="store_true")
    parser.add_argument("--config", action="store_true", help="交互式配置")
    parser.add_argument("--key", type=str, default="<Ctrl>space")
    parser.add_argument("--install-service", action="store_true")
    parser.add_argument("--remove-service", action="store_true")
    parser.add_argument("--mic-gain", type=int, default=None,
                       help="麦克风增益 1-100 (默认20，太大=削波)")

    args = parser.parse_args()
    if len(sys.argv) == 1:
        VoiceDaemon().start()
        return

    if args.mic_gain is not None:
        g = max(1, min(100, args.mic_gain))
        src = _find_mic_source()
        if src:
            subprocess.run(["pactl", "set-source-volume", src, f"{g}%"],
                         capture_output=True, timeout=3)
            config = get_config()
            config["mic_gain"] = g
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(json.dumps(config, indent=2, ensure_ascii=False))
            print(f"✅ 麦克风增益设为 {g}%")
        else:
            print("❌ 未找到麦克风")

    elif args.test:
        test_mic()
    elif args.config:
        interactive_config()
    elif args.install_hotkey:
        install_hotkey(args.key)
    elif args.remove_hotkey:
        remove_hotkey()
    elif args.install_service:
        install_service()
    elif args.remove_service:
        remove_service()
    elif args.daemon:
        VoiceDaemon().start()
    elif args.oneshot:
        oneshot()
    elif args.trigger:
        fifo = Path("/tmp/voice-input.fifo")
        if fifo.exists():
            try:
                fd = os.open(str(fifo), os.O_WRONLY | os.O_NONBLOCK)
                os.write(fd, b"record\n")
                os.close(fd)
            except Exception:
                oneshot()
        else:
            oneshot()
