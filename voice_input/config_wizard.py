"""Interactive configuration wizard."""

import json
import subprocess

from voice_input.config import get_config, CONFIG_DIR, CONFIG_FILE
from voice_input.audio import _find_mic_source


def interactive_config():
    """Interactive configuration: engine, hotkey, beep, etc."""
    config = get_config()
    print("⚙️  语音输入配置")
    print("=" * 40)

    # Engine selection
    engines = {"1": "vosk", "2": "google", "3": "whisper", "4": "faster-whisper"}
    engine_names = {"vosk": "Vosk（本地流式）", "google": "Google（在线）",
                    "whisper": "Whisper（本地离线）", "faster-whisper": "Faster-Whisper（本地，CPU快4x）"}
    current = config.get("engine", "vosk")
    print(f"\n当前引擎: {engine_names.get(current, current)}")
    print("  1) Vosk — 本地流式实时识别")
    print("  2) Google — 在线 Web Speech API（推荐）")
    print("  3) Whisper — 本地离线 OpenAI Whisper")
    print("  4) Faster-Whisper — 本地 faster-whisper 服务器")
    choice = input("选择引擎 [1/2/3/4，回车跳过]: ").strip()
    if choice in engines:
        config["engine"] = engines[choice]
        print(f"  ✅ 已设为 {engine_names[engines[choice]]}")

    # Whisper model
    if config.get("engine") in ("whisper", "faster-whisper"):
        models = ["tiny", "small", "medium", "large"]
        current_model = config.get("whisper_model", "small")
        print(f"\n当前 Whisper 模型: {current_model}")
        for i, m in enumerate(models, 1):
            print(f"  {i}) {m}")
        choice = input("选择模型 [1-4，回车跳过]: ").strip()
        if choice and int(choice) in range(1, 5):
            config["whisper_model"] = models[int(choice) - 1]
            print(f"  ✅ 已设为 {config['whisper_model']}")

    # Auto input
    auto = config.get("auto_input", True)
    print(f"\n自动输入到焦点窗口: {'开启' if auto else '关闭'}")
    choice = input("开启自动输入? [Y/n，回车跳过]: ").strip().lower()
    if choice == "n":
        config["auto_input"] = False
        print("  ✅ 已关闭自动输入")
    elif choice == "y":
        config["auto_input"] = True
        print("  ✅ 已开启自动输入")

    # Beep
    bp = config.get("beep", True)
    print(f"\n提示音: {'开启' if bp else '关闭'}")
    choice = input("开启提示音? [Y/n，回车跳过]: ").strip().lower()
    if choice == "n":
        config["beep"] = False
        print("  ✅ 已关闭提示音")
    elif choice == "y":
        config["beep"] = True
        print("  ✅ 已开启提示音")

    # OSD
    osd = config.get("osd_enabled", True)
    print(f"\n屏幕浮动窗口 (OSD): {'开启' if osd else '关闭'}")
    choice = input("开启 OSD 浮动窗口? [Y/n，回车跳过]: ").strip().lower()
    if choice == "n":
        config["osd_enabled"] = False
        print("  ✅ 已关闭 OSD")
    elif choice == "y":
        config["osd_enabled"] = True
        print("  ✅ 已开启 OSD")

    # Mic gain
    gain = config.get("mic_gain", 20)
    print(f"\n麦克风增益: {gain}%（默认 20，太大=削波）")
    choice = input("设置增益 1-100 [回车跳过]: ").strip()
    if choice and choice.isdigit():
        g = max(1, min(100, int(choice)))
        config["mic_gain"] = g
        src = _find_mic_source()
        if src:
            subprocess.run(["pactl", "set-source-volume", src, f"{g}%"],
                         capture_output=True, timeout=3)
        print(f"  ✅ 已设为 {g}%")

    # Hotkey
    hotkey = config.get("hotkey", "<Ctrl>space")
    print(f"\n守护进程热键: {hotkey}")
    print("  格式如: <Ctrl>space, <Alt>space, <Super>space")
    choice = input("设置热键 [回车跳过]: ").strip()
    if choice:
        config["hotkey"] = choice
        print(f"  ✅ 已设为 {choice}")

    # Save
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2, ensure_ascii=False))
    print(f"\n✅ 配置已保存到 {CONFIG_FILE}")
    print(f"   当前引擎: {engine_names.get(config['engine'], config['engine'])}")
    print(f"   自动输入: {'开启' if config['auto_input'] else '关闭'}")
    print(f"   提示音: {'开启' if config['beep'] else '关闭'}")
    print(f"   OSD 浮动窗口: {'开启' if config.get('osd_enabled', True) else '关闭'}")
