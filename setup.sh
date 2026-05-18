#!/bin/bash
# ============================================================
# voice-input 安装 / 设置脚本
# Ubuntu 24.04 GNOME Wayland 系统级语音输入
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MAIN_SCRIPT="$SCRIPT_DIR/voice-input.py"
BIN_LINK="$HOME/.local/bin/voice-input"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}✓${NC} $1"; }
warn()  { echo -e "${YELLOW}⚠${NC} $1"; }
err()   { echo -e "${RED}✗${NC} $1"; }

echo "========================================="
echo "  🎙️  语音输入 - 安装设置"
echo "========================================="
echo ""

# 1. 安装系统依赖
echo "📦 检查系统依赖..."

# wtype (Wayland 文字输入)
if ! command -v wtype &>/dev/null; then
    sudo apt-get install -y wtype
    info "wtype 已安装"
else
    info "wtype 已存在"
fi

# ydotool (跨平台键盘输入)
if ! command -v ydotool &>/dev/null; then
    sudo apt-get install -y ydotool
    info "ydotool 已安装"
else
    info "ydotool 已存在"
fi

# notify-send
if ! command -v notify-send &>/dev/null; then
    sudo apt-get install -y libnotify-bin
    info "libnotify-bin 已安装"
else
    info "notify-send 已存在"
fi

# wl-clipboard
if ! command -v wl-copy &>/dev/null; then
    sudo apt-get install -y wl-clipboard
    info "wl-clipboard 已安装"
else
    info "wl-copy 已存在"
fi

# GTK3 for OSD
if ! dpkg -l | grep -q gir1.2-gtk-3.0; then
    sudo apt-get install -y gir1.2-gtk-3.0
    info "GTK3 已安装（用于 OSD 浮窗）"
else
    info "GTK3 已存在"
fi

# PulseAudio/PipeWire utils
if ! command -v pactl &>/dev/null; then
    sudo apt-get install -y pulseaudio-utils
    info "pulseaudio-utils 已安装"
else
    info "pactl 已存在"
fi

if ! command -v pw-record &>/dev/null; then
    sudo apt-get install -y pipewire
    info "pipewire 已安装"
else
    info "pw-record 已存在"
fi

# 2. 安装 Python 依赖
echo ""
echo "📦 检查 Python 依赖..."
pip3 install numpy pyaudio speechrecognition webrtcvad --quiet 2>/dev/null
info "Python 基础依赖已确认"

# Optional: ASR engines
echo ""
echo "📦 可选 ASR 引擎:"
if python3 -c "import vosk" 2>/dev/null; then
    info "Vosk 已安装（本地流式识别）"
else
    warn "Vosk 未安装 — pip install vosk"
fi

if python3 -c "import faster_whisper" 2>/dev/null; then
    info "faster-whisper 已安装（本地快速识别）"
else
    warn "faster-whisper 未安装 — pip install faster-whisper"
fi

if python3 -c "import whisper" 2>/dev/null; then
    info "openai-whisper 已安装（本地离线识别）"
else
    warn "openai-whisper 未安装 — pip install openai-whisper"
fi

if python3 -c "import gi; gi.require_version('Gtk', '3.0')" 2>/dev/null; then
    info "PyGObject GTK3 可用（OSD 浮窗）"
else
    warn "PyGObject 未安装 — sudo apt install python3-gi"
fi

# 3. 设置可执行权限
chmod +x "$MAIN_SCRIPT"
info "脚本已赋予执行权限"

# 4. 创建 ~/.local/bin 软链接
mkdir -p "$HOME/.local/bin"
if [ -L "$BIN_LINK" ]; then
    rm "$BIN_LINK"
fi
ln -sf "$MAIN_SCRIPT" "$BIN_LINK"
info "已创建命令: voice-input"

# 确保 ~/.local/bin 在 PATH 中
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    warn "~/.local/bin 不在 PATH 中"
    echo "   请将以下内容添加到 ~/.bashrc:"
    echo "   export PATH=\"\$HOME/.local/bin:\$PATH\""
fi

# 5. 注册 GNOME 快捷键
echo ""
echo "⌨️  注册 GNOME 快捷键..."
if python3 "$MAIN_SCRIPT" --install-hotkey; then
    info "快捷键已注册: Super+Space"
else
    warn "快捷键注册失败，请手动设置"
fi

# 6. 配置脚本
echo ""
echo "⚙️  初始配置..."
python3 "$MAIN_SCRIPT" --config || true

echo ""
echo "========================================="
echo "  ✅  安装完成!"
echo "========================================="
echo ""
echo "使用方式:"
echo "  1. 按 Super+Space 录音，说完自动停止并输入"
echo "     （支持 OSD 浮窗实时反馈）"
echo "  2. 或运行: voice-input --daemon"
echo "  3. 测试:   voice-input --test"
echo "  4. 配置:   voice-input --config"
echo ""
echo "配置选项包括:"
echo "  - 引擎选择（Vosk/Google/Whisper/Faster-Whisper）"
echo "  - OSD 屏幕浮窗开关"
echo "  - 提示音开关"
echo "  - 自动输入开关"
echo "  - 麦克风增益"
echo ""
echo "详细文档: $SCRIPT_DIR/README.md"
echo "========================================="
