"""GNOME custom keybinding management."""

import ast
import os
import subprocess
import sys


def _get_script_path():
    """Get the path to voice-input.py entry point."""
    # When running as a module, __file__ points to this file in the package.
    # The entry point is the voice-input.py in the parent directory.
    pkg_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(pkg_dir, "voice-input.py")


def install_hotkey(key="<Ctrl>space"):
    script = _get_script_path()
    cmd = f"{sys.executable} {script} --trigger"
    key_name = "custom0"
    try:
        r = subprocess.run(["gsettings", "get", "org.gnome.settings-daemon.plugins.media-keys",
                          "custom-keybindings"], capture_output=True, text=True, timeout=5)
        existing = r.stdout.strip()
        bindings = []
        if existing and existing != "@as []":
            try:
                bindings = list(ast.literal_eval(existing))
            except Exception:
                pass
        path = f"/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/{key_name}/"
        if path not in bindings:
            bindings.append(path)
        subprocess.run(["gsettings", "set", "org.gnome.settings-daemon.plugins.media-keys",
                       "custom-keybindings", str(bindings)], capture_output=True, timeout=5)
        for k, v in [("name", "语音输入"), ("command", cmd), ("binding", key)]:
            subprocess.run(["gsettings", "set",
                          f"org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:{path}",
                          k, v], capture_output=True, timeout=5)
        print(f"✅ 快捷键: {key}")
    except Exception as e:
        print(f"❌ {e}")


def remove_hotkey():
    key_name = "custom0"
    path = f"/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/{key_name}/"
    try:
        r = subprocess.run(["gsettings", "get", "org.gnome.settings-daemon.plugins.media-keys",
                          "custom-keybindings"], capture_output=True, text=True, timeout=5)
        existing = r.stdout.strip()
        bindings = []
        if existing and existing != "@as []":
            try:
                bindings = [b for b in ast.literal_eval(existing) if b != path]
            except Exception:
                pass
        subprocess.run(["gsettings", "set", "org.gnome.settings-daemon.plugins.media-keys",
                       "custom-keybindings", str(bindings)], capture_output=True, timeout=5)
        print("✅ 已移除")
    except Exception as e:
        print(f"❌ {e}")
