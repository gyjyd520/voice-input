"""Microphone test utility."""

import struct
import subprocess
import time

from voice_input.config import RATE
from voice_input.audio import _find_mic_source


def test_mic():
    source = _find_mic_source()
    print("🎤 麦克风测试")
    print("=" * 40)
    print(f"   音频源: {source or '❌ 未找到'}")
    if not source:
        return

    import webrtcvad
    vad = webrtcvad.Vad(2)
    rate = RATE
    VAD_FRAME = int(rate * 0.03) * 2  # 30ms

    cmd = ["pw-record", "--target=" + source, "--format=s16",
           "--channels=1", "--rate=" + str(rate), "-"]
    import os
    env = {**os.environ, "PIPEWIRE_DEBUG": "0", "JACK_NO_START_SERVER": "1"}
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
    time.sleep(0.3)

    print("   录音中，请说话...(按 Ctrl+C 停止)")
    print()
    print(f"   {'时间':>6} {'RMS':>6} {'VAD':>4}  音量条")
    print(f"   {'-'*50}")

    buf = b""
    try:
        for _ in range(150):
            data = proc.stdout.read1(3200)
            if len(data) < 2:
                break
            buf += data
            # RMS
            count = len(data) // 2
            vals = struct.unpack("<" + str(count) + "h", data[:count*2])
            rms = int((sum(v*v for v in vals) / len(vals)) ** 0.5)
            # VAD
            is_speech = False
            while len(buf) >= VAD_FRAME:
                frame = buf[:VAD_FRAME]
                buf = buf[VAD_FRAME:]
                try:
                    if vad.is_speech(frame, rate):
                        is_speech = True
                except Exception:
                    pass
            bars = min(int(rms / 100), 40)
            bar = "█" * bars + "░" * (40 - bars)
            vad_mark = "🗣️" if is_speech else "  "
            t = time.time()
            print(f"\r   {t%100:>6.1f} {rms:>6d} {vad_mark:>4} |{bar}| ", end="", flush=True)
            time.sleep(0.02)
    except KeyboardInterrupt:
        pass

    proc.terminate()
    proc.wait()
    print()
