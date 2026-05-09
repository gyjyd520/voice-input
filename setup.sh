#!/bin/bash
# ============================================================
# voice-input 安装 / 设置脚本
# Ubuntu 24.04 GNOME Wayland 系统级语音输入
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MAIN_SCRIPT="$SCRIPT_DIR/voice-input.py"
BIN_LINK="$HOME/.local/bin/voice-input"
AUTOSTART_DIR="$HOME/.config/autostart"
AUTOSTART_FILE="$AUTOSTART_DIR/voice-input.desktop"

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
if ! command -v wtype &>/dev/null; then
    sudo apt-get install -y wtype
    info "wtype 已安装"
else
    info "wtype 已存在"
fi

if ! command -v notify-send &>/dev/null; then
    sudo apt-get install -y libnotify-bin
    info "libnotify-bin 已安装"
else
    info "notify-send 已存在"
fi

# 2. 安装 Python 依赖
echo ""
echo "📦 检查 Python 依赖..."
pip3 install pyaudio speechrecognition pynput --quiet 2>/dev/null
info "Python 依赖已确认"

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

# 7. 可选：开机自启
echo ""
echo "🔄 是否设置开机自启（守护进程模式）？"
read -p "  设置开机自启? [y/N]: " autostart_choice
if [ "$autostart_choice" = "y" ] || [ "$autostart_choice" = "Y" ]; then
    mkdir -p "$AUTOSTART_DIR"
    cat > "$AUTOSTART_FILE" << EOF
[Desktop Entry]
Type=Application
Name=语音输入守护进程
Comment=系统级语音输入后台服务
Exec=$MAIN_SCRIPT --daemon
Terminal=false
Categories=Utility;Audio;
X-GNOME-Autostart-enabled=true
EOF
    info "开机自启已设置"
    echo "   (下次登录自动启动，或现在运行: voice-input --daemon)"
else
    echo "  跳过开机自启"
fi

echo ""
echo "========================================="
echo "  ✅  安装完成!"
echo "========================================="
echo ""
echo "使用方式:"
echo "  1. 按 Super+Space 录音，说完自动停止并输入"
echo "  2. 或运行: voice-input --daemon (按住右 Alt 录音)"
echo "  3. 测试:   voice-input --test"
echo "  4. 配置:   voice-input --config"
echo ""
echo "详细文档: $SCRIPT_DIR/README.md"
echo "========================================="
