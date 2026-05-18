"""Configuration management."""

import json
from pathlib import Path

RATE = 16000
VOSK_MODEL_DIR = Path.home() / ".local" / "share" / "vosk"
VOSK_MODEL_PATH = VOSK_MODEL_DIR / "vosk-model-small-cn-0.22"
CONFIG_DIR = Path.home() / ".config" / "voice-input"
CONFIG_FILE = CONFIG_DIR / "config.json"


def get_config():
    """Read config, return dict with defaults."""
    defaults = {
        "engine": "vosk",
        "whisper_model": "small",
        "auto_input": True,
        "beep": True,
        "mic_gain": 20,
        "hotkey": "<Ctrl>space",
        "osd_enabled": True,
        "osd_timeout": 30,
        "auto_confirm": False,
        "llm_enabled": False,
        "llm_api_base_url": "https://api.openai.com/v1",
        "llm_api_key": "",
        "llm_model": "gpt-4o-mini",
        "iflytek_app_id": "",
        "iflytek_api_key": "",
        "iflytek_api_secret": "",
        "iflytek_language": "zh_cn",
        "iflytek_accent": "mandarin",
        "iflytek_vad_eos": 3000,
        "iflytek_ptt": 1,
        "iflytek_nunum": 1,
    }
    if CONFIG_FILE.exists():
        try:
            d = json.loads(CONFIG_FILE.read_text())
            defaults.update(d)
        except Exception:
            pass
    return defaults
