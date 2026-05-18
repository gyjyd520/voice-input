"""Text input: clipboard + ydotool paste, with clipboard preservation and CJK IME handling."""

import subprocess
import threading
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


def _get_clipboard_text():
    """Read current Wayland clipboard content. Returns str or None."""
    try:
        r = subprocess.run(["wl-paste", "--no-newline"], capture_output=True, timeout=2)
        if r.returncode == 0:
            return r.stdout.decode("utf-8", errors="replace") or None
    except Exception:
        pass
    return None


def _restore_clipboard(text):
    """Restore clipboard to original content after paste completes."""
    try:
        if text:
            subprocess.run(["wl-copy"], input=text.encode(), capture_output=True, timeout=3)
        else:
            subprocess.run(["wl-copy", "--clear"], capture_output=True, timeout=3)
    except Exception:
        pass


# ── CJK input method helpers ──

_CJK_ENGINES = {"pinyin", "bopomofo", "chewing", "mozc", "hangul", "korean",
                "anthy", "unikey", "bamboo", "rime", "libpinyin", "googlepinyin",
                "sunpinyin", "fcitx-keyboard-zh", "kkc"}


def _detect_im():
    """Detect current input method engine name. Returns str or None."""
    # IBus
    try:
        r = subprocess.run(["ibus", "engine"], capture_output=True, timeout=1)
        if r.returncode == 0:
            eng = r.stdout.decode().strip()
            if eng:
                return eng
    except Exception:
        pass

    # Fcitx5
    try:
        r = subprocess.run(["fcitx5-remote"], capture_output=True, timeout=1)
        if r.returncode == 0:
            out = r.stdout.decode().strip()
            if out and out != "0" and out != "1":
                return out
    except Exception:
        pass

    # GNOME input sources (gsettings)
    try:
        r = subprocess.run(["gsettings", "get", "org.gnome.desktop.input-sources",
                           "mru-sources"], capture_output=True, timeout=1)
        if r.returncode == 0:
            import re
            # Parse e.g. "[('xkb', 'us'), ('ibus', 'pinyin')]"
            match = re.search(r"\('([^']+)',\s*'([^']+)'\)", r.stdout.decode())
            if match:
                return match.group(2)
    except Exception:
        pass

    return None


def _is_cjk_im(engine):
    """Check if the engine name indicates a CJK input method."""
    if not engine:
        return False
    eng_lower = engine.lower()
    return any(cjk in eng_lower for cjk in _CJK_ENGINES)


def _switch_im(engine):
    """Switch to a specific input method. Returns True on success."""
    if not engine:
        return False

    # IBus
    try:
        r = subprocess.run(["ibus", "engine", engine], capture_output=True, timeout=1)
        if r.returncode == 0:
            return True
    except Exception:
        pass

    # Fcitx5: group number
    if engine.isdigit():
        try:
            r = subprocess.run(["fcitx5-remote", "-s", engine], capture_output=True, timeout=1)
            if r.returncode == 0:
                return True
        except Exception:
            pass

    return False


_ASCII_IMS = ["xkb:us::eng", "us", "1"]


def _get_ascii_im():
    """Find an available ASCII-capable input method."""
    for im in _ASCII_IMS:
        try:
            if im.isdigit():
                r = subprocess.run(["fcitx5-remote", "-s"], capture_output=True, timeout=1)
                if r.returncode == 0:
                    return im
            else:
                r = subprocess.run(["ibus", "list-engine"], capture_output=True, timeout=1)
                if r.returncode == 0 and im.encode() in r.stdout:
                    return im
        except Exception:
            pass

    # Best guess
    try:
        r = subprocess.run(["ibus", "engine"], capture_output=True, timeout=1)
        if r.returncode == 0:
            return "xkb:us::eng"
    except Exception:
        pass
    return "1"  # Fcitx5 default first group


def paste_text(text):
    """Paste text into the focused window, preserving original clipboard
    and handling CJK input method switching."""
    if not text:
        return

    # Detect CJK IME and switch to ASCII if needed
    original_im = _detect_im()
    need_switch = _is_cjk_im(original_im)

    if need_switch:
        ascii_im = _get_ascii_im()
        _switch_im(ascii_im)
        time.sleep(0.05)  # Let IME settle

    saved = _get_clipboard_text()

    # Copy transcription to clipboard
    clip(text)

    # Wait for focus window to restore
    time.sleep(0.3)

    # 1. Clipboard + Ctrl+V paste (fast, handles long text)
    pasted = False
    try:
        r = subprocess.run(["ydotool", "key", "ctrl+v"], capture_output=True, timeout=5)
        pasted = r.returncode == 0
    except Exception:
        pass

    # 2. ydotool type — character-by-character (slower but reliable)
    if not pasted:
        try:
            r = subprocess.run(["ydotool", "type", text], capture_output=True, timeout=10)
            pasted = r.returncode == 0
        except Exception:
            pass

    # 3. Fallback: clipboard already has text, user can manually Ctrl+V
    if not pasted:
        notify("📋 已复制到剪贴板", text[:50], "edit-paste")

    # Restore original IME after paste
    if need_switch and original_im:
        timer = threading.Timer(0.3, _switch_im, args=(original_im,))
        timer.daemon = True
        timer.start()

    # Restore original clipboard after paste has been consumed
    if saved is not None and saved != text:
        timer = threading.Timer(0.5, _restore_clipboard, args=(saved,))
        timer.daemon = True
        timer.start()
