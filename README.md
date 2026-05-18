# 🎙️ Voice Input - 系统级全局语音输入

在 Ubuntu GNOME Wayland 下，在任何应用中通过**语音输入文字**。

按一下 `Super+Space`，说话，文字自动输入到当前焦点窗口。

## 快速开始

```bash
# 运行安装脚本（推荐）
cd ~/claudecode/voice-input
bash setup.sh

# 然后就可用 Super+Space 开始语音输入了！
```

## 使用方式

### 方式一：一键模式（推荐）

按 `Super+Space` → 说话 → 静音自动停止 → 屏幕浮窗显示识别结果 → 确认/编辑/取消 → 文字输入到当前窗口。

> 支持 GTK3 屏幕浮窗（OSD），实时显示录音状态、音频电平、Vosk 流式识别文字。

### 方式二：守护进程模式

```bash
voice-input --daemon
```

后台运行，通过 FIFO 接收快捷键触发。支持所有引擎（Vosk 流式 / Whisper / Faster-Whisper / Google）。

### 方式三：单次模式

```bash
voice-input --oneshot          # 一键录音→识别→输入
voice-input --test             # 测试麦克风
```

## 命令参考

| 命令 | 说明 |
|------|------|
| `voice-input --oneshot` | 一键录音、识别、输入 |
| `voice-input --daemon` | 后台守护进程 |
| `voice-input --test` | 测试麦克风（实时 RMS + VAD 显示） |
| `voice-input --config` | 交互式配置（引擎、热键、OSD 等） |
| `voice-input --install-hotkey` | 注册 GNOME 快捷键 (Super+Space) |
| `voice-input --remove-hotkey` | 移除 GNOME 快捷键 |
| `voice-input --help` | 查看帮助 |

## 配置

```bash
voice-input --config
```

可配置项：
- **引擎**: Vosk（本地流式）/ Google（在线）/ Whisper（本地）/ Faster-Whisper（本地快 4x）
- **Whisper 模型**: tiny / small / medium / large
- **OSD 浮动窗口**: 屏幕浮窗实时反馈（录音状态、音频电平、识别文字）
- **热键**: 守护进程模式的热键
- **自动输入**: 是否自动输入到焦点窗口
- **提示音**: 录音开始/结束提示音
- **麦克风增益**: 1-100%

## OSD 屏幕浮窗（新功能）

录音时会显示半透明浮动窗口：
- **录音指示器**：显示 "正在聆听..." 状态
- **音频电平条**：实时 RMS 音量可视化（绿→黄→红渐变）
- **流式文字**：Vosk 引擎实时显示部分识别结果
- **处理动画**：Whisper/Google 引擎识别时显示进度动画
- **结果确认**：识别完成后显示文字 + 确认/编辑/取消 按钮
  - **确认**：自动粘贴到当前窗口
  - **编辑**：复制到剪贴板，手动编辑后粘贴
  - **取消**：丢弃识别结果

可在配置中关闭 OSD（`voice-input --config` → 关闭 OSD 浮动窗口）。

## 引擎对比

| 引擎 | 速度 | 准确率 | 离线 | 流式 |
|------|------|--------|------|------|
| Vosk | 实时 | ★★★ | ✅ | ✅ |
| Faster-Whisper | 快 | ★★★★ | ✅ | ❌ |
| Whisper | 慢 | ★★★★ | ✅ | ❌ |
| Google | 快 | ★★★★★ | ❌ | ❌ |

## 项目结构

```
voice_input/
├── __init__.py          # 包入口
├── audio.py             # 麦克风检测、录音、VAD、提示音
├── config.py            # 配置管理
├── input.py             # 剪贴板 + 文字输入
├── notify.py            # 桌面通知
├── daemon.py            # 守护进程（GTK OSD 集成）
├── hotkey.py            # GNOME 快捷键管理
├── service.py           # systemd 服务管理
├── config_wizard.py     # 交互式配置向导
├── test.py              # 麦克风测试
├── engines/             # ASR 引擎
│   ├── base.py          # 抽象基类
│   ├── vosk_engine.py   # Vosk 流式引擎
│   ├── whisper_engine.py
│   ├── faster_whisper_engine.py
│   └── google_engine.py
└── ui/                  # 界面组件
    └── osd.py           # GTK3 屏幕浮窗 + 音频电平表
```

## 技术细节

- **录音**: PipeWire pw-record，WebRTC VAD 静音检测自动停止
- **识别**: Vosk（流式）/ Faster-Whisper / OpenAI Whisper / Google Speech API
- **输入**: ydotool（Ctrl+V 或 type）→ 自动粘贴
- **UI**: GTK3 Cairo OSD 浮窗（实时反馈）+ notify-send 桌面通知
- **触发**: GNOME 自定义快捷键 + FIFO 通信

## 常见问题

**Q: 麦克风没反应？**
```bash
voice-input --test    # 测试麦克风
```
确保系统麦克风权限已开启：设置 → 隐私 → 麦克风

**Q: OSD 浮窗不显示？**
确认 GTK3 已安装：`sudo apt install gir1.2-gtk-3.0`
或在配置中关闭 OSD 使用纯通知模式。

**Q: 识别不准？**
- 换用 Google 引擎（在线，最准）
- 或在安静环境使用
- 或安装 Faster-Whisper medium 模型

**Q: Wayland 下文字没输入？**
确保已安装 `ydotool`（安装脚本已自动安装）
