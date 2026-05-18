"""Text input: clipboard + ydotool paste."""

import subprocess
import time

from voice_input.notify import notify


def clip(text):
    if not text:
        return False
    try:
        r = subprocess.run(["wl-copy"], input=text.encode(), capture_output=True, timeout=3)
        return r.returncode == 0
    except Exception:
        return False


def paste_text(text):
    """Paste text into the focused window."""
    if not text:
        return

    # Copy to clipboard first
    clip(text)

    # Wait for focus window to restore
    time.sleep(0.3)

    # 1. Clipboard + Ctrl+V paste (fast, handles long text)
    try:
        r = subprocess.run(["ydotool", "key", "ctrl+v"], capture_output=True, timeout=5)
        if r.returncode == 0:
            return
    except Exception:
        pass

    # 2. ydotool type — character-by-character (slower but reliable)
    try:
        r = subprocess.run(["ydotool", "type", text], capture_output=True, timeout=10)
        if r.returncode == 0:
            return
    except Exception:
        pass

    # 3. Fallback: clipboard already has text, user can manually Ctrl+V
    notify("📋 已复制到剪贴板", text[:50], "edit-paste")
