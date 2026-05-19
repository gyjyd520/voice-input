"""Audio: mic detection, recording with VAD, beep tones."""

import os
import struct
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path

import numpy as np

from voice_input.config import RATE, get_config, CONFIG_DIR, CONFIG_FILE
from voice_input.notify import notify

_mic_source_cache = None


def _find_mic_source():
    """Find best mic: Bluetooth > USB > built-in. Skip silent/disconnected."""
    global _mic_source_cache
    if _mic_source_cache is not None:
        return _mic_source_cache

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

        # Get Bluetooth connection status
        bluez_disconnected = set()
        try:
            r2 = subprocess.run(["pactl", "list", "cards"],
                              capture_output=True, text=True, timeout=5)
            import re
            for card in r2.stdout.split("\n\n"):
                if 'api.bluez5.connection = "disconnected"' in card:
                    m = re.search(r'bluez_card\.([^\s]+)', card)
                    if m:
                        bluez_disconnected.add(m.group(1))
        except Exception:
            pass

        # Priority sort
        def _priority(m):
            if "bluez_input" in m:
                return 0
            if "usb" in m.lower():
                return 1
            return 2

        mics.sort(key=_priority)

        for m in mics:
            # Skip disconnected Bluetooth
            if "bluez_input" in m:
                addr = m.replace("bluez_input.", "").rsplit(".", 1)[0]
                if addr in bluez_disconnected:
                    continue

            # Level check for Bluetooth and USB
            if "bluez_input" in m or "usb" in m.lower():
                rms = _test_mic(m)
                if rms < 30:
                    continue  # Disconnected or completely silent

            _mic_source_cache = m
            return m

        # None available, pick first
        _mic_source_cache = mics[0]
        return mics[0]
    except Exception:
        pass
    return None


def _test_mic(m):
    """Quick test if mic has real audio signal. Returns RMS or -1."""
    try:
        try:
            p = subprocess.run(
                ["pw-record", "--target=" + m, "--format=s16",
                 "--channels=1", "--rate=16000", "-"],
                capture_output=True, timeout=1.5, env={**os.environ,
                "PIPEWIRE_DEBUG": "0", "JACK_NO_START_SERVER": "1"})
            raw = p.stdout
        except subprocess.TimeoutExpired as e:
            # pw-record streams forever; capture what we got before timeout
            raw = e.stdout or b""
        if len(raw) > 3200:
            vals = struct.unpack("<" + str(len(raw)//2) + "h",
                                raw[:len(raw)//2*2])
            rms = int((sum(v*v for v in vals) / len(vals)) ** 0.5)
            return rms
    except Exception:
        pass
    return -1


def _apply_mic_gain(source):
    """Apply configured mic_gain to audio source."""
    config = get_config()
    gain = config.get("mic_gain", 20)
    try:
        subprocess.run(["pactl", "set-source-volume", source, f"{gain}%"],
                      capture_output=True, timeout=3)
    except Exception:
        pass


def _calc_rms(frame):
    """Calculate RMS level from raw PCM frame."""
    count = len(frame) // 2
    if count == 0:
        return 0
    vals = struct.unpack("<" + str(count) + "h", frame[:count*2])
    return int((sum(v*v for v in vals) / count) ** 0.5)


def beep(freq=800, dur=0.06):
    """Play short beep via pw-play."""
    try:
        n = int(RATE * dur)
        t = np.linspace(0, dur, n, endpoint=False)
        fade = np.ones(n)
        fade[:n//10] = np.linspace(0, 1, n//10)
        fade[-n//10:] = np.linspace(1, 0, n//10)
        s = (np.sin(2*np.pi*freq*t) * 0.18 * fade).astype(np.float32)
        data = (s*32767).astype(np.int16).tobytes()

        # Generate WAV header
        import io
        buf = io.BytesIO()
        buf.write(b'RIFF')
        buf.write((36 + len(data)).to_bytes(4, 'little'))
        buf.write(b'WAVE')
        buf.write(b'fmt ')
        buf.write((16).to_bytes(4, 'little'))
        buf.write((1).to_bytes(2, 'little'))   # PCM
        buf.write((1).to_bytes(2, 'little'))   # mono
        buf.write(RATE.to_bytes(4, 'little'))
        buf.write((RATE * 2).to_bytes(4, 'little'))
        buf.write((2).to_bytes(2, 'little'))
        buf.write((16).to_bytes(2, 'little'))
        buf.write(b'data')
        buf.write(len(data).to_bytes(4, 'little'))
        buf.write(data)
        wav_data = buf.getvalue()

        subprocess.run(["pw-play", "-"], input=wav_data, capture_output=True, timeout=2)
    except Exception:
        pass


def record_audio_batch(source, stop_fn=None, on_level=None):
    """
    Record to temp WAV, VAD silence auto-stop.
    stop_fn: optional callback, return True to stop immediately.
    on_level: optional callback(rms) called every ~50ms with audio level.
    Returns WAV path or None.
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
    silence_to = 0.7
    in_speech = False
    had_speech = False
    speech_end = None
    speech_frames = 0
    silence_frames = 0
    VAD_HYSTERESIS = 3
    all_audio = b""
    vad_buf = b""
    start = time.time()
    last_level_time = 0

    while True:
        elapsed = time.time() - start
        if elapsed >= max_rec:
            break
        if stop_fn is not None and elapsed >= min_rec and stop_fn():
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

        # VAD (with hysteresis + RMS noise gate)
        while len(vad_buf) >= VAD_FRAME:
            frame = vad_buf[:VAD_FRAME]
            vad_buf = vad_buf[VAD_FRAME:]

            rms = _calc_rms(frame)
            NOISE_GATE = 500

            # Push level to callback every ~50ms
            now = time.time()
            if on_level and (now - last_level_time) > 0.05:
                on_level(rms)
                last_level_time = now

            try:
                is_speech = vad.is_speech(frame, rate) and rms >= NOISE_GATE
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

        if not in_speech and elapsed >= 5:
            break

    proc.terminate()
    try:
        proc.wait(timeout=2)
    except Exception:
        proc.kill()

    if not had_speech:
        return None
    if len(all_audio) < rate * 0.3 * 2:  # Too short
        return None

    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    with wave.open(tmp, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(all_audio)
    return tmp.name


def record_recognize(engine, source, on_partial=None, on_level=None):
    """
    Stream recording with Vosk real-time recognition + WebRTC VAD.
    on_partial: callback(text) for streaming partial results.
    on_level: callback(rms) for audio level meter.

    Returns: (final_text, partial_list)
    """
    import webrtcvad

    if not source:
        source = _find_mic_source()
    if not source:
        return "", []
    _apply_mic_gain(source)

    vad = webrtcvad.Vad(3)
    rate = RATE
    partial_results = []
    last_partial = ""

    cmd = ["pw-record", "--target=" + source, "--format=s16",
           "--channels=1", "--rate=" + str(rate), "-"]
    env = {**os.environ, "PIPEWIRE_DEBUG": "0", "JACK_NO_START_SERVER": "1"}
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
    except Exception as e:
        notify("❌ 录音失败", str(e), "dialog-error")
        return "", []

    time.sleep(0.3)

    max_rec = 12
    min_rec = 0.5
    silence_to = 0.8
    in_speech = False
    speech_end = None
    vosk_buffer = b""
    vad_buffer = b""
    CHUNK_MS = 30
    VAD_FRAME = int(rate * CHUNK_MS / 1000) * 2
    VOSK_CHUNK = int(rate * 0.2) * 2

    speech_frames = 0
    silence_frames = 0
    VAD_HYSTERESIS = 3
    last_level_time = 0

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

        # Vosk streaming recognition (every 200ms)
        while len(vosk_buffer) >= VOSK_CHUNK:
            chunk = vosk_buffer[:VOSK_CHUNK]
            vosk_buffer = vosk_buffer[VOSK_CHUNK:]
            partial, final = engine.feed(chunk)
            if final and final not in partial_results:
                partial_results.append(final)
                last_partial = final
            if partial and partial != last_partial:
                last_partial = partial
                if on_partial:
                    on_partial(partial)

        # WebRTC VAD (with hysteresis + RMS noise gate)
        while len(vad_buffer) >= VAD_FRAME:
            frame = vad_buffer[:VAD_FRAME]
            vad_buffer = vad_buffer[VAD_FRAME:]
            if len(frame) != VAD_FRAME:
                continue

            rms = _calc_rms(frame)

            now = time.time()
            if on_level and (now - last_level_time) > 0.05:
                on_level(rms)
                last_level_time = now

            try:
                is_speech = vad.is_speech(frame, rate) and rms >= 500
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

        if not in_speech and elapsed >= 5:
            break

        if speech_end and time.time() - speech_end >= silence_to:
            break

    proc.terminate()
    try:
        proc.wait(timeout=2)
    except Exception:
        proc.kill()

    # Feed remaining data to Vosk
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
