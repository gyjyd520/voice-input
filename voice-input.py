#!/usr/bin/env python3
"""
voice-input - 按住说话语音输入工具
===================================
按住 Ctrl+Space 说话，松开自动识别并输入文字。
Wayland/X11 通用（基于 evdev 键盘轮询）。

用法:
  voice-input.py              # 启动守护进程
  voice-input.py --oneshot    # 单次录音识别
  voice-input.py --test       # 测试麦克风
  voice-input.py --config     # 交互式配置
"""

import sys
import os

# Ensure package is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    from voice_input import main
    main()
