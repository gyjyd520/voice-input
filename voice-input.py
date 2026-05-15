#!/usr/bin/env python3
"""
voice-input - 实时语音输入工具
================================
像豆包/Gemini一样，一边说话一边出字。

架构：
  - WebRTC VAD — 高精度静音检测
  - Vosk — 本地流式实时识别（即时显示）  
  - Google API — 后台准确识别（最终结果）
  - ydotool type — 直接打字到焦点窗口

用法:
  voice-input.py              # 启动守护进程
  voice-input.py --test       # 测试麦克风
  voice-input.py --trigger    # 发送录音信号
"""

import argparse
import ast
import json
import os
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

# ============================================================
# 配置
# ============================================================
VOSK_MODEL_DIR = Path.home() / ".local" / "share" / "vosk"
VOSK_MODEL_PATH = VOSK_MODEL_DIR / "vosk-model-small-cn-0.22"
RATE = 16000
CONFIG_DIR = Path.home() / ".config" / "voice-input"
CONFIG_FILE = CONFIG_DIR / "config.json"


def get_config():
    """读取配置，返回 dict"""
    defaults = {
        "engine": "vosk",
        "whisper_model": "small",
        "auto_input": True,
        "beep": True,
        "mic_gain": 20,
        "hotkey": "<Ctrl>space",
    }
    if CONFIG_FILE.exists():
        try:
            d = json.loads(CONFIG_FILE.read_text())
            defaults.update(d)
        except Exception:
            pass
    return defaults

# ============================================================
# 工具函数
# ============================================================

def _find_mic_source():
    """找到最佳麦克风源，优先蓝牙耳机 > USB外接 > 内置"""
    try:
        r = subprocess.run(["pactl", "list", "sources", "short"],
                         capture_output=True, text=True, timeout=5)
        lines = r.stdout.strip().split("\n")
        mics = []
        for line in lines:
            parts = line.strip().split()
            if len(parts) >= 2 and "monitor" not in parts[1]:
                mics.append(parts[1])
        if not mics:
            return None
        # 优先蓝牙 (bluez_input)
        for m in mics:
            if "bluez_input" in m:
                return m
        # 其次返回第一个
        return mics[0]
    except Exception:
        pass
    return None


def _apply_mic_gain(source):
    """将配置中的 mic_gain 应用到音频源"""
    config = get_config()
    gain = config.get("mic_gain", 20)
    try:
        subprocess.run(["pactl", "set-source-volume", source, f"{gain}%"],
                      capture_output=True, timeout=3)
    except Exception:
        pass


def _redirect_stderr():
    """在 OS 层面将 fd 2 重定向到 /dev/null。返回旧的 fd 副本。"""
    devnull = os.open(os.devnull, os.O_WRONLY)
    old = os.dup(2)
    os.dup2(devnull, 2)
    os.close(devnull)
    return old


def _restore_stderr(old):
    """恢复 fd 2"""
    os.dup2(old, 2)
    os.close(old)


def notify(title, msg, icon="audio-input-microphone"):
    try:
        subprocess.run(["notify-send", "-i", icon, "-t", "1500", title, msg],
                      capture_output=True, timeout=2)
    except Exception:
        pass


def clip(text):
    if not text:
        return False
    try:
        r = subprocess.run(["wl-copy"], input=text.encode(), capture_output=True, timeout=3)
        return r.returncode == 0
    except Exception:
        return False


def paste_text(text):
    """将文字输入到焦点窗口"""
    if not text:
        return

    # 先复制到剪贴板（快）
    clip(text)

    # 等待焦点窗口恢复
    time.sleep(0.3)

    # 1. 剪贴板 + Ctrl+V 粘贴（快，不怕长文本）
    try:
        r = subprocess.run(["ydotool", "key", "ctrl+v"], capture_output=True, timeout=5)
        if r.returncode == 0:
            return
    except Exception:
        pass

    # 2. ydotool type — 逐字输入（较慢但也能用）
    try:
        r = subprocess.run(["ydotool", "type", text], capture_output=True, timeout=10)
        if r.returncode == 0:
            return
    except Exception:
        pass

    # 3. 兜底：剪贴板已有文字，用户手动 Ctrl+V
    notify("📋 已复制到剪贴板", text[:50], "edit-paste")


def beep(freq=800, dur=0.08):
    try:
        import numpy as np
        old = _redirect_stderr()
        try:
            import pyaudio
            n = int(RATE * dur)
            t = np.linspace(0, dur, n, endpoint=False)
            fade = np.ones(n)
            fade[:n//10] = np.linspace(0, 1, n//10)
            fade[-n//10:] = np.linspace(1, 0, n//10)
            s = (np.sin(2*np.pi*freq*t) * 0.3 * fade).astype(np.float32)
            p = pyaudio.PyAudio()
            st = p.open(format=pyaudio.paFloat32, channels=1, rate=RATE, output=True)
            data = (s*32767).astype(np.int16).tobytes()
            st.write(data)
            # 等待硬件缓冲区播放完毕，避免 p.terminate() 产生电流声
            time.sleep(dur + 0.05)
            st.close()
            p.terminate()
        finally:
            _restore_stderr(old)
    except Exception:
        pass


# ============================================================
# Vosk 引擎
# ============================================================

class VoskEngine:
    def __init__(self):
        self.model = None
        self.rec = None
        self._lock = threading.Lock()

    def load(self):
        if self.model is not None:
            return True
        if not VOSK_MODEL_PATH.exists():
            notify("⚠️ Vosk 模型未找到", "", "dialog-error")
            return False
        try:
            import vosk
            old = _redirect_stderr()
            try:
                self.model = vosk.Model(str(VOSK_MODEL_PATH))
            finally:
                _restore_stderr(old)
            return self.reset()
        except Exception as e:
            notify("❌ Vosk 加载失败", str(e), "dialog-error")
            return False

    def reset(self):
        with self._lock:
            try:
                import vosk
                self.rec = vosk.KaldiRecognizer(self.model, RATE)
                self.rec.SetWords(False)
                return True
            except Exception:
                return False

    def feed(self, data):
        with self._lock:
            if not self.rec or not data:
                return "", ""
            try:
                if self.rec.AcceptWaveform(data):
                    r = json.loads(self.rec.Result())
                    return "", r.get("text", "")
                else:
                    p = json.loads(self.rec.PartialResult())
                    return p.get("partial", ""), ""
            except Exception:
                return "", ""

    def final(self):
        with self._lock:
            if not self.rec:
                return ""
            try:
                r = json.loads(self.rec.FinalResult())
                return r.get("text", "")
            except Exception:
                return ""


FASTER_WHISPER_MODEL_DIR = os.path.expanduser("~/.local/share/faster-whisper/models")


class FasterWhisperEngine:
    """faster-whisper 本地引擎 — CTranslate2 后端，CPU 上比 openai-whisper 快 4x"""

    def __init__(self, model_name="small"):
        self.model_name = model_name
        self.model = None

    def _model_path(self):
        return os.path.join(FASTER_WHISPER_MODEL_DIR, self.model_name)

    def load(self):
        if self.model is not None:
            return True
        try:
            from faster_whisper import WhisperModel
            local = self._model_path()
            if os.path.exists(local) and os.path.exists(os.path.join(local, "model.bin")):
                self.model = WhisperModel(
                    local, device="cpu", compute_type="int8",
                    num_workers=2, cpu_threads=4, local_files_only=True)
            else:
                self.model = WhisperModel(
                    self.model_name, device="cpu", compute_type="int8",
                    num_workers=2, cpu_threads=4)
            return True
        except ImportError:
            notify("⚠️ faster-whisper 未安装", "pip install faster-whisper", "dialog-error")
            return False
        except Exception as e:
            notify("❌ Faster-Whisper 加载失败", str(e)[:80], "dialog-error")
            return False

    def transcribe(self, wav_path):
        if not self.model:
            return ""
        segments, _ = self.model.transcribe(wav_path, language="zh", beam_size=5)
        return "".join(seg.text for seg in segments).strip()


class GoogleEngine:
    """Google Web Speech API — 在线，中文准确率高"""

    def transcribe(self, wav_path):
        import speech_recognition as sr
        r = sr.Recognizer()
        try:
            with sr.AudioFile(wav_path) as source:
                audio = r.record(source)
            return r.recognize_google(audio, language="zh-CN")
        except sr.UnknownValueError:
            return ""
        except sr.RequestError as e:
            notify("❌ Google API 错误", str(e), "dialog-error")
            return ""


class WhisperEngine:
    """OpenAI Whisper — 本地离线，需安装 openai-whisper"""

    def __init__(self, model_name="small"):
        self.model_name = model_name
        self.model = None

    def load(self):
        if self.model is not None:
            return True
        try:
            import whisper
            self.model = whisper.load_model(self.model_name)
            return True
        except ImportError:
            notify("⚠️ Whisper 未安装", "pip install openai-whisper", "dialog-error")
            return False
        except Exception as e:
            notify("❌ Whisper 加载失败", str(e), "dialog-error")
            return False

    def transcribe(self, wav_path):
        if not self.model:
            return ""
        result = self.model.transcribe(wav_path, language="zh")
        return result["text"].strip()


# ============================================================
# 核心：录音 + 识别 + VAD
# ============================================================

def record_audio_batch(source):
    """
    录音到临时 WAV 文件，VAD 静音检测自动停止。
    返回 WAV 文件路径，或 None。
    """
    import webrtcvad

    if not source:
        source = _find_mic_source()
    if not source:
        return None
    _apply_mic_gain(source)

    vad = webrtcvad.Vad(2)
    rate = RATE
    VAD_FRAME = int(rate * 0.03) * 2  # 30ms

    cmd = ["pw-record", "--target=" + source, "--format=s16",
           "--channels=1", "--rate=" + str(rate), "-"]
    env = {**os.environ, "PIPEWIRE_DEBUG": "0", "JACK_NO_START_SERVER": "1"}
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
    except Exception as e:
        notify("❌ 录音失败", str(e), "dialog-error")
        return None

    time.sleep(0.3)

    max_rec = 12
    min_rec = 0.5
    silence_to = 0.6
    in_speech = False
    had_speech = False
    speech_end = None
    speech_frames = 0
    silence_frames = 0
    VAD_HYSTERESIS = 3
    all_audio = b""
    vad_buf = b""
    start = time.time()

    while True:
        elapsed = time.time() - start
        if elapsed >= max_rec:
            break

        try:
            raw = proc.stdout.read1(4800)
        except Exception:
            break

        if not raw:
            time.sleep(0.01)
            continue

        all_audio += raw
        vad_buf += raw

        # VAD（带去抖）
        while len(vad_buf) >= VAD_FRAME:
            frame = vad_buf[:VAD_FRAME]
            vad_buf = vad_buf[VAD_FRAME:]
            try:
                is_speech = vad.is_speech(frame, rate)
            except Exception:
                is_speech = False

            if is_speech:
                speech_frames += 1
                silence_frames = 0
                if speech_frames >= VAD_HYSTERESIS and not in_speech:
                    in_speech = True
                    had_speech = True
                    speech_end = None
            else:
                silence_frames += 1
                speech_frames = 0
                if silence_frames >= VAD_HYSTERESIS and in_speech and elapsed >= min_rec:
                    if speech_end is None:
                        speech_end = time.time()
                    elif time.time() - speech_end >= silence_to:
                        proc.terminate()
                        try:
                            proc.wait(timeout=2)
                        except Exception:
                            proc.kill()
                        break

        if speech_end and time.time() - speech_end >= silence_to:
            break

        if not in_speech and elapsed >= 3:
            break

    proc.terminate()
    try:
        proc.wait(timeout=2)
    except Exception:
        proc.kill()

    if not had_speech:
        return None
    if len(all_audio) < rate * 0.3 * 2:  # 录音太短
        return None

    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    with wave.open(tmp, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(all_audio)
    return tmp.name


def record_recognize(engine, source):
    """
    使用 pw-record 实时录音，同时：
    - Vosk 流式识别（部分结果实时显示）
    - WebRTC VAD 检测说话起止
    - 说话结束后自动停止
    
    Returns: (final_text, partial_list)
    """
    import webrtcvad

    if not source:
        source = _find_mic_source()
    if not source:
        return "", []
    _apply_mic_gain(source)

    vad = webrtcvad.Vad(3)  # 模式 3（高精度，避免环境噪音误判）
    rate = RATE
    partial_results = []
    last_partial = ""

    # 启动录音（stdout 输出 raw PCM）
    cmd = ["pw-record", "--target=" + source, "--format=s16",
           "--channels=1", "--rate=" + str(rate), "-"]
    env = {**os.environ, "PIPEWIRE_DEBUG": "0", "JACK_NO_START_SERVER": "1"}
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
    except Exception as e:
        notify("❌ 录音失败", str(e), "dialog-error")
        return "", []

    time.sleep(0.3)  # 等 pw-record 启动

    # 状态
    max_rec = 12
    min_rec = 0.5
    silence_to = 0.8
    in_speech = False
    speech_end = None
    last_notify = 0
    vosk_buffer = b""
    vad_buffer = b""
    CHUNK_MS = 30
    VAD_FRAME = int(rate * CHUNK_MS / 1000) * 2
    VOSK_CHUNK = int(rate * 0.2) * 2

    # VAD 去抖：连续 3 帧才算切换状态
    speech_frames = 0
    silence_frames = 0
    VAD_HYSTERESIS = 3

    start = time.time()

    while True:
        elapsed = time.time() - start
        if elapsed >= max_rec:
            break

        try:
            raw = proc.stdout.read1(4800)
        except Exception:
            try:
                raw = os.read(proc.stdout.fileno(), 4800)
            except Exception:
                break

        if not raw:
            time.sleep(0.01)
            continue

        vosk_buffer += raw
        vad_buffer += raw

        # Vosk 流式识别（每 200ms）
        while len(vosk_buffer) >= VOSK_CHUNK:
            chunk = vosk_buffer[:VOSK_CHUNK]
            vosk_buffer = vosk_buffer[VOSK_CHUNK:]
            partial, final = engine.feed(chunk)
            if final and final not in partial_results:
                partial_results.append(final)
                last_partial = final
            if partial and partial != last_partial:
                last_partial = partial
                now = time.time()
                if now - last_notify > 0.4:
                    last_notify = now
                    notify("🎤 语音输入中...", partial, "audio-input-microphone")

        # WebRTC VAD（带去抖）
        while len(vad_buffer) >= VAD_FRAME:
            frame = vad_buffer[:VAD_FRAME]
            vad_buffer = vad_buffer[VAD_FRAME:]
            if len(frame) != VAD_FRAME:
                continue

            try:
                is_speech = vad.is_speech(frame, rate)
            except Exception:
                is_speech = False

            if is_speech:
                speech_frames += 1
                silence_frames = 0
                if speech_frames >= VAD_HYSTERESIS and not in_speech:
                    in_speech = True
                    speech_end = None
            else:
                silence_frames += 1
                speech_frames = 0
                if silence_frames >= VAD_HYSTERESIS and in_speech and elapsed >= min_rec:
                    if speech_end is None:
                        speech_end = time.time()
                    elif time.time() - speech_end >= silence_to:
                        break

        if not in_speech and elapsed >= 3:
            break

        if speech_end and time.time() - speech_end >= silence_to:
            break

    # 停止录音
    proc.terminate()
    try:
        proc.wait(timeout=2)
    except Exception:
        proc.kill()

    # 喂剩余数据给 Vosk
    while len(vosk_buffer) > 0:
        chunk = vosk_buffer[:VOSK_CHUNK]
        vosk_buffer = vosk_buffer[VOSK_CHUNK:]
        partial, final = engine.feed(chunk)
        if final and final not in partial_results:
            partial_results.append(final)

    ft = engine.final()
    if ft and ft not in partial_results:
        partial_results.append(ft)

    text = " ".join(partial_results).strip() or last_partial.strip() or ft.strip()
    return text, partial_results


# ============================================================
# 守护进程
# ============================================================

class VoiceDaemon:
    def __init__(self):
        self.vosk_engine = VoskEngine()
        self.whisper_engine = None
        self.faster_whisper_engine = None
        self.running = False
        self._recording = False
        self._fifo_path = Path("/tmp/voice-input.fifo")
        self._pid_file = Path("/tmp/voice-input-daemon.pid")

    @staticmethod
    def _parse_hotkey(hotkey_str):
        """将 '<Ctrl>space' 转为 pynput 格式 '<ctrl>+<space>'"""
        import re
        parts = re.findall(r'<([^>]+)>', hotkey_str)
        if not parts:
            return "<ctrl>+<space>"
        modifiers = "+".join(f"<{p.lower()}>" for p in parts)
        main = re.sub(r"<[^>]+>", "", hotkey_str).strip()
        if main:
            return f"{modifiers}+<{main}>"
        return f"{modifiers}+<space>"

    def start(self):
        if self._pid_file.exists():
            pid = self._pid_file.read_text().strip()
            if pid and os.path.exists(f"/proc/{pid}"):
                print(f"已在运行 (PID={pid})")
                return
        self._pid_file.write_text(str(os.getpid()))

        config = get_config()
        engine_name = config.get("engine", "vosk")
        source = _find_mic_source()
        if source:
            _apply_mic_gain(source)

        if engine_name == "faster-whisper":
            model_name = config.get("whisper_model", "small")
            print(f"🎙️  加载 Faster-Whisper {model_name} 模型...")
            self.faster_whisper_engine = FasterWhisperEngine(model_name)
            if not self.faster_whisper_engine.load():
                return
        elif engine_name == "whisper":
            model_name = config.get("whisper_model", "small")
            print(f"🎙️  加载 Whisper {model_name} 模型...")
            self.whisper_engine = WhisperEngine(model_name)
            if not self.whisper_engine.load():
                return
        elif engine_name == "vosk":
            print("🎙️  加载 Vosk 模型...")
            if not self.vosk_engine.load():
                return

        self.running = True

        # FIFO（保留作为备用触发通道）
        self._fifo_path.unlink(missing_ok=True)
        os.mkfifo(self._fifo_path)
        threading.Thread(target=self._fifo_loop, daemon=True).start()

        # pynput 全局热键监听
        hotkey_str = self._parse_hotkey(config.get("hotkey", "<Ctrl>space"))
        try:
            from pynput import keyboard
            self._hotkey_listener = keyboard.GlobalHotKeys({hotkey_str: self._on_hotkey_press})
            self._hotkey_listener.start()
        except Exception as e:
            notify("⚠️ 无法注册全局热键", str(e)[:80], "dialog-error")
            print(f"⚠️ 全局热键失败: {e}")

        notify("🎙️ 语音输入已就绪", f"按 {config.get('hotkey', 'Ctrl+Space')} 说话")
        print(f"✅ 就绪！按 {config.get('hotkey', 'Ctrl+Space')}")
        print(f"   热键解析为: {hotkey_str}")

        try:
            self._hotkey_listener.join()
        except KeyboardInterrupt:
            pass
        except Exception:
            while self.running:
                time.sleep(1)
        finally:
            self.running = False
            self._fifo_path.unlink(missing_ok=True)
            self._pid_file.unlink(missing_ok=True)

    def _fifo_loop(self):
        while self.running:
            try:
                with open(self._fifo_path, "r") as f:
                    cmd = f.read().strip()
                    if cmd == "record":
                        threading.Thread(target=self._on_record, daemon=True).start()
            except Exception:
                time.sleep(0.1)

    def _on_hotkey_press(self):
        if self._recording:
            return
        threading.Thread(target=self._on_record, daemon=True).start()

    def _on_record(self):
        if self._recording:
            return
        self._recording = True
        try:
            self._do_record()
        finally:
            self._recording = False

    def _do_record(self):
        config = get_config()
        engine_name = config.get("engine", "vosk")
        do_beep = config.get("beep", True)
        do_input = config.get("auto_input", True)
        if do_beep:
            beep(880)
        source = _find_mic_source()
        if not source:
            notify("❌ 未找到麦克风", "", "dialog-error")
            return

        notify("🎤 请说话...", "说完自动识别", "audio-input-microphone")

        text = ""
        if engine_name == "google":
            wav_path = record_audio_batch(source)
            if do_beep:
                beep(660)
            if not wav_path:
                notify("⚠️ 未检测到语音", "请再试一次", "dialog-warning")
                return
            engine = GoogleEngine()
            text = engine.transcribe(wav_path)
            os.unlink(wav_path)
        elif engine_name == "faster-whisper":
            wav_path = record_audio_batch(source)
            if do_beep:
                beep(660)
            if not wav_path:
                notify("⚠️ 未检测到语音", "请再试一次", "dialog-warning")
                return
            if self.faster_whisper_engine is None:
                model = config.get("whisper_model", "small")
                self.faster_whisper_engine = FasterWhisperEngine(model)
                if not self.faster_whisper_engine.load():
                    return
            text = self.faster_whisper_engine.transcribe(wav_path)
            os.unlink(wav_path)
        elif engine_name == "whisper":
            wav_path = record_audio_batch(source)
            if do_beep:
                beep(660)
            if not wav_path:
                notify("⚠️ 未检测到语音", "请再试一次", "dialog-warning")
                return
            if self.whisper_engine is None:
                model = config.get("whisper_model", "small")
                self.whisper_engine = WhisperEngine(model)
                if not self.whisper_engine.load():
                    return
            text = self.whisper_engine.transcribe(wav_path)
            os.unlink(wav_path)
        else:
            # vosk 流式识别
            self.vosk_engine.reset()
            text, _ = record_recognize(self.vosk_engine, source)
            if do_beep:
                beep(660)

        if text:
            if do_input:
                paste_text(text)
            notify("✅ 已输入", text[:60], "dialog-positive")
            print(f"📝 {text}")
        else:
            notify("⚠️ 未识别到语音", "请再试一次", "dialog-warning")


# ============================================================
# 测试
# ============================================================

def test_mic():
    source = _find_mic_source()
    print("🎤 麦克风测试")
    print("=" * 40)
    print(f"   音频源: {source or '❌ 未找到'}")
    if not source:
        return

    import webrtcvad
    vad = webrtcvad.Vad(2)
    rate = RATE
    VAD_FRAME = int(rate * 0.03) * 2  # 30ms

    cmd = ["pw-record", "--target=" + source, "--format=s16",
           "--channels=1", "--rate=" + str(rate), "-"]
    env = {**os.environ, "PIPEWIRE_DEBUG": "0", "JACK_NO_START_SERVER": "1"}
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
    time.sleep(0.3)

    print("   录音中，请说话...(按 Ctrl+C 停止)")
    print()
    print(f"   {'时间':>6} {'RMS':>6} {'VAD':>4}  音量条")
    print(f"   {'-'*50}")
    
    buf = b""
    try:
        for _ in range(150):
            data = proc.stdout.read1(3200)
            if len(data) < 2:
                break
            buf += data
            # RMS
            vals = struct.unpack("<" + str(len(data)//2) + "h", data[:len(data)//2*2])
            rms = int((sum(v*v for v in vals) / len(vals)) ** 0.5)
            # VAD
            is_speech = False
            while len(buf) >= VAD_FRAME:
                frame = buf[:VAD_FRAME]
                buf = buf[VAD_FRAME:]
                try:
                    if vad.is_speech(frame, rate):
                        is_speech = True
                except:
                    pass
            bars = min(int(rms / 100), 40)
            bar = "█" * bars + "░" * (40 - bars)
            vad_mark = "🗣️" if is_speech else "  "
            t = time.time()
            print(f"\r   {t%100:>6.1f} {rms:>6d} {vad_mark:>4} |{bar}| ", end="", flush=True)
            time.sleep(0.02)
    except KeyboardInterrupt:
        pass

    proc.terminate()
    proc.wait()
    print()


# ============================================================
# 快捷键 / 服务管理
# ============================================================

def install_hotkey(key="<Ctrl>space"):
    script = os.path.abspath(__file__)
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


def install_service():
    d = Path.home() / ".config" / "systemd" / "user"
    d.mkdir(parents=True, exist_ok=True)
    s = os.path.abspath(__file__)
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


def interactive_config():
    """交互式配置引擎、热键、提示音等"""
    config = get_config()
    print("⚙️  语音输入配置")
    print("=" * 40)

    # 引擎选择
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

    # Whisper 模型
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

    # 自动输入
    auto = config.get("auto_input", True)
    print(f"\n自动输入到焦点窗口: {'开启' if auto else '关闭'}")
    choice = input("开启自动输入? [Y/n，回车跳过]: ").strip().lower()
    if choice == "n":
        config["auto_input"] = False
        print("  ✅ 已关闭自动输入")
    elif choice == "y":
        config["auto_input"] = True
        print("  ✅ 已开启自动输入")

    # 提示音
    bp = config.get("beep", True)
    print(f"\n提示音: {'开启' if bp else '关闭'}")
    choice = input("开启提示音? [Y/n，回车跳过]: ").strip().lower()
    if choice == "n":
        config["beep"] = False
        print("  ✅ 已关闭提示音")
    elif choice == "y":
        config["beep"] = True
        print("  ✅ 已开启提示音")

    # 麦克风增益
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

    # 热键
    hotkey = config.get("hotkey", "<Ctrl>space")
    print(f"\n守护进程热键: {hotkey}")
    print("  格式如: <Ctrl>space, <Alt>space, <Super>space")
    choice = input("设置热键 [回车跳过]: ").strip()
    if choice:
        config["hotkey"] = choice
        print(f"  ✅ 已设为 {choice}")

    # 保存
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2, ensure_ascii=False))
    print(f"\n✅ 配置已保存到 {CONFIG_FILE}")
    print(f"   当前引擎: {engine_names.get(config['engine'], config['engine'])}")
    print(f"   自动输入: {'开启' if config['auto_input'] else '关闭'}")
    print(f"   提示音: {'开启' if config['beep'] else '关闭'}")


def oneshot():
    config = get_config()
    cfg_engine = config.get("engine", "vosk")
    do_beep = config.get("beep", True)
    do_input = config.get("auto_input", True)

    # Google / Whisper = 批量引擎
    if cfg_engine == "google":
        source = _find_mic_source()
        if not source:
            print("❌ 未找到麦克风")
            return
        if do_beep:
            beep(880)
        print("🎤 请说话...")
        notify("🎤 请说话...", "说完自动识别", "audio-input-microphone")
        wav_path = record_audio_batch(source)
        if do_beep:
            beep(660)
        if not wav_path:
            print("⚠️ 未检测到语音")
            return
        print("🔍 识别中...")
        engine = GoogleEngine()
        text = engine.transcribe(wav_path)
        os.unlink(wav_path)
        if text:
            print(f"📝 {text}")
            if do_input:
                paste_text(text)
                notify("✅ 已输入", text[:60], "dialog-positive")
        else:
            notify("⚠️ 未识别到语音", "请再试一次", "dialog-warning")
        return

    if cfg_engine == "faster-whisper":
        source = _find_mic_source()
        if not source:
            print("❌ 未找到麦克风")
            return
        model = config.get("whisper_model", "small")
        engine = FasterWhisperEngine(model)
        print(f"🔍 加载 Faster-Whisper {model} 模型...")
        if not engine.load():
            return
        if do_beep:
            beep(880)
        print("🎤 请说话...")
        notify("🎤 请说话...", "说完自动识别", "audio-input-microphone")
        wav_path = record_audio_batch(source)
        if do_beep:
            beep(660)
        if not wav_path:
            print("⚠️ 未检测到语音")
            return
        print("🔍 识别中...")
        text = engine.transcribe(wav_path)
        os.unlink(wav_path)
        if text:
            print(f"📝 {text}")
            if do_input:
                paste_text(text)
            notify("✅ 已输入", text[:60], "dialog-positive")
        else:
            notify("⚠️ 未识别到语音", "请再试一次", "dialog-warning")
        return

    if cfg_engine == "whisper":
        source = _find_mic_source()
        if not source:
            print("❌ 未找到麦克风")
            return
        model = config.get("whisper_model", "small")
        engine = WhisperEngine(model)
        print("🔍 加载 Whisper 模型...")
        if not engine.load():
            return
        if do_beep:
            beep(880)
        print("🎤 请说话...")
        notify("🎤 请说话...", "说完自动识别", "audio-input-microphone")
        wav_path = record_audio_batch(source)
        if do_beep:
            beep(660)
        if not wav_path:
            print("⚠️ 未检测到语音")
            return
        print("🔍 识别中...")
        text = engine.transcribe(wav_path)
        os.unlink(wav_path)
        if text:
            print(f"📝 {text}")
            if do_input:
                paste_text(text)
                notify("✅ 已输入", text[:60], "dialog-positive")
        else:
            notify("⚠️ 未识别到语音", "请再试一次", "dialog-warning")
        return

    # 默认 Vosk（流式）
    engine = VoskEngine()
    print("🎙️  加载模型...")
    if not engine.load():
        return
    source = _find_mic_source()
    if not source:
        print("❌ 未找到麦克风")
        return
    if do_beep:
        beep(880)
    print("🎤 请说话...")
    text, _ = record_recognize(engine, source)
    if do_beep:
        beep(660)
    if text:
        if do_input:
            paste_text(text)
        print(f"📝 {text}")
    else:
        print("⚠️ 未识别到语音")


# ============================================================
# 主入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="🎙️ 实时语音输入")
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument("--oneshot", action="store_true")
    parser.add_argument("--trigger", action="store_true")
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--install-hotkey", action="store_true")
    parser.add_argument("--remove-hotkey", action="store_true")
    parser.add_argument("--config", action="store_true", help="交互式配置")
    parser.add_argument("--key", type=str, default="<Ctrl>space")
    parser.add_argument("--install-service", action="store_true")
    parser.add_argument("--remove-service", action="store_true")
    parser.add_argument("--mic-gain", type=int, default=None,
                       help="麦克风增益 1-100 (默认20，太大=削波)")

    args = parser.parse_args()
    if len(sys.argv) == 1:
        VoiceDaemon().start()
        return

    if args.mic_gain is not None:
        g = max(1, min(100, args.mic_gain))
        src = _find_mic_source()
        if src:
            subprocess.run(["pactl", "set-source-volume", src, f"{g}%"],
                         capture_output=True, timeout=3)
            config = get_config()
            config["mic_gain"] = g
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(json.dumps(config, indent=2, ensure_ascii=False))
            print(f"✅ 麦克风增益设为 {g}%")
        else:
            print("❌ 未找到麦克风")

    elif args.test:
        test_mic()
    elif args.config:
        interactive_config()
    elif args.install_hotkey:
        install_hotkey(args.key)
    elif args.remove_hotkey:
        remove_hotkey()
    elif args.install_service:
        install_service()
    elif args.remove_service:
        remove_service()
    elif args.daemon:
        VoiceDaemon().start()
    elif args.oneshot:
        oneshot()
    elif args.trigger:
        fifo = Path("/tmp/voice-input.fifo")
        if not fifo.exists():
            subprocess.Popen([sys.executable, os.path.abspath(__file__)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(1)
        if fifo.exists():
            try:
                fd = os.open(str(fifo), os.O_WRONLY | os.O_NONBLOCK)
                os.write(fd, b"record\n")
                os.close(fd)
            except Exception:
                oneshot()
        else:
            oneshot()


if __name__ == "__main__":
    main()
