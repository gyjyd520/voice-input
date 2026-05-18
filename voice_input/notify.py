"""Desktop notification wrapper."""

import subprocess


def notify(title, msg, icon="audio-input-microphone"):
    try:
        subprocess.run(["notify-send", "-i", icon, "-t", "1500", title, msg],
                      capture_output=True, timeout=2)
    except Exception:
        pass
