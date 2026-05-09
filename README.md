# 🎙️ Voice Input - 系统级全局语音输入

在 Ubuntu GNOME Wayland 下，在任何应用中通过**语音输入文字**。

按一下 `Super+Space`，说话，自动输入到当前焦点窗口。

## 快速开始

```bash
# 运行安装脚本（推荐）
cd ~/claudecode/voice-input
bash setup.sh

# 然后就可用 Super+Space 开始语音输入了！
```

## 使用方式

### 方式一：一键模式（推荐）

按 `Super+Space` → 说话 → 静音自动停止 → 文字自动输入到当前窗口。

> 就像 macOS 的听写功能一样。

### 方式二：守护进程模式（按住说话）

```bash
voice-input --daemon
```

按住 `Right Alt` 说话，松手自动识别并输入。

### 方式三：直接运行

```bash
voice-input --oneshot          # 一键录音→识别→输入
voice-input --test             # 测试麦克风
```

## 命令参考

| 命令 | 说明 |
|------|------|
| `voice-input --oneshot` | 一键录音、识别、输入 |
| `voice-input --daemon` | 后台守护进程（按住右 Alt） |
| `voice-input --test` | 测试麦克风 |
| `voice-input --config` | 交互式配置（引擎、热键等） |
| `voice-input --install-hotkey` | 注册 GNOME 快捷键 (Super+Space) |
| `voice-input --remove-hotkey` | 移除 GNOME 快捷键 |
| `voice-input --help` | 查看帮助 |

## 配置

```bash
voice-input --config
```

可配置项：
- **引擎**: Google（在线，准确）或 Whisper（本地，离线）
- **Whisper 模型**: tiny / small / medium / large
- **热键**: 守护进程模式的热键
- **自动输入**: 是否自动输入到焦点窗口
- **提示音**: 录音开始/结束提示音

## 引擎选择

### Google Speech Recognition（默认）
- ✅ 无需额外安装
- ✅ 中文识别准确率高
- ❌ 需要网络

### Whisper（本地）
- ✅ 完全离线
- ✅ 隐私安全
- ❌ 需要安装: `pip install openai-whisper`
- ❌ 首次运行需下载模型

```bash
# 切换到 Whisper 本地引擎
voice-input --config
# 选择引擎: 2) Whisper
```

## 技术细节

- **录音**: PyAudio，静音检测自动停止
- **识别**: Google Speech Recognition API 或 OpenAI Whisper
- **输入**: wtype（Wayland 原生文字输入）
- **触发**: GNOME 自定义快捷键或 pynput 全局热键
- **通知**: notify-send 显示状态

## 文件结构

```
~/.config/voice-input/
├── config.json        # 配置文件
└── data/              # 数据目录

~/.local/bin/voice-input  # 命令行入口（软链接）
```

## 常见问题

**Q: 麦克风没反应？**
```bash
voice-input --test    # 测试麦克风
```
确保系统麦克风权限已开启：设置 → 隐私 → 麦克风

**Q: 识别不准？**
- 换用 Google 引擎（默认最准）
- 或在安静环境使用
- 或安装 Whisper medium 模型

**Q: Wayland 下文字没输入？**
确保已安装 `wtype`（安装脚本已自动安装）
