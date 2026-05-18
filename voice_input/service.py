"""systemd user service management."""

import os
import subprocess
import sys
from pathlib import Path


def _get_script_path():
    pkg_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(pkg_dir, "voice-input.py")


def install_service():
    d = Path.home() / ".config" / "systemd" / "user"
    d.mkdir(parents=True, exist_ok=True)
    s = _get_script_path()
    (d / "voice-input.service").write_text(
        f"[Unit]\nDescription=Voice Input Daemon\nAfter=sound.target\n\n"
        f"[Service]\nExecStart={sys.executable} {s}\nRestart=on-failure\nRestartSec=2\n\n"
        f"[Install]\nWantedBy=default.target\n")
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, timeout=5)
    subprocess.run(["systemctl", "--user", "enable", "voice-input.service"], capture_output=True, timeout=5)
    subprocess.run(["systemctl", "--user", "start", "voice-input.service"], capture_output=True, timeout=5)
    print("✅ 开机自启已安装")


def remove_service():
    subprocess.run(["systemctl", "--user", "stop", "voice-input.service"], capture_output=True, timeout=5)
    subprocess.run(["systemctl", "--user", "disable", "voice-input.service"], capture_output=True, timeout=5)
    print("✅ 已移除")
