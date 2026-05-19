"""iFlytek (讯飞) streaming voice dictation engine via WebSocket API."""

import base64
import hashlib
import hmac
import json
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from urllib.parse import quote

import websocket

from voice_input.config import RATE
from voice_input.engines.base import BaseEngine

logger = logging.getLogger("voice-input.iflytek")

# Ensure errors are visible even without logging config
def _log_error(msg, *args):
    logger.error(msg, *args)
    print(f"[iFlytek] {msg % args}", file=sys.stderr)

# ── iFlytek API constants ──

IFLYTEK_HOST = "iat-api.xfyun.cn"
IFLYTEK_PATH = "/v2/iat"
IFLYTEK_URL = f"wss://{IFLYTEK_HOST}{IFLYTEK_PATH}"

# 40ms at 16kHz mono 16-bit = 16000 * 2 * 0.04
CHUNK_BYTES = 1280
CHUNK_INTERVAL = 0.04


# English day/month abbreviations for locale-independent date formatting.
# strftime(%a/%b) produces Chinese characters under zh_CN.UTF-8, which
# breaks iFlytek's HMAC signature verification.
_EN_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_EN_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _build_auth_url(api_key, api_secret):
    """Build the authenticated WebSocket URL with HMAC-SHA256 signature."""
    now = datetime.now(timezone.utc)
    date = "%s, %02d %s %d %02d:%02d:%02d GMT" % (
        _EN_WEEKDAYS[now.weekday()],
        now.day,
        _EN_MONTHS[now.month - 1],
        now.year,
        now.hour,
        now.minute,
        now.second,
    )

    signature_origin = f"host: {IFLYTEK_HOST}\ndate: {date}\nGET {IFLYTEK_PATH} HTTP/1.1"
    signature_sha = hmac.new(
        api_secret.encode(),
        signature_origin.encode(),
        hashlib.sha256,
    ).digest()
    signature = base64.b64encode(signature_sha).decode()

    authorization_origin = (
        f'api_key="{api_key}", algorithm="hmac-sha256", '
        f'headers="host date request-line", signature="{signature}"'
    )
    authorization = base64.b64encode(authorization_origin.encode()).decode()

    return (
        f"{IFLYTEK_URL}?authorization={quote(authorization)}"
        f"&date={quote(date)}&host={IFLYTEK_HOST}"
    )


class IflytekEngine(BaseEngine):
    """iFlytek streaming voice dictation engine.

    Uses the iFlytek WebSocket API for real-time speech recognition
    with dynamic text correction (wpgs).
    """

    def __init__(self):
        self._result_lock = threading.Lock()
        self._result_cache = {}  # sn -> (type, text) where type is 'rpl' or 'apd'
        self._has_ended = False
        self._debug = False

    # ── Public API ──

    def recognize_stream(self, source, on_partial=None, on_level=None, stop_fn=None):
        """Record from audio source and stream to iFlytek for real-time recognition.

        Args:
            source: PulseAudio source name
            on_partial: callback(text) called with streaming partial results
            on_level: callback(rms) called with audio level (~50ms intervals)
            stop_fn: callback() -> bool, return True to stop recording

        Returns:
            Final recognized text, or empty string on failure.
        """
        from voice_input.config import get_config

        cfg = get_config()
        app_id = cfg.get("iflytek_app_id", "")
        api_key = cfg.get("iflytek_api_key", "")
        api_secret = cfg.get("iflytek_api_secret", "")

        if not app_id or not api_key or not api_secret:
            _log_error("iFlytek credentials not configured")
            return ""

        self._result_cache.clear()
        self._has_ended = False

        # ── Start recording BEFORE WebSocket so audio is ready ──
        # Apply mic gain
        try:
            subprocess.run(["pactl", "set-source-volume", source, f"{cfg.get('mic_gain', 20)}%"],
                          capture_output=True, timeout=3)
        except Exception:
            pass

        cmd = [
            "pw-record", "--target=" + source, "--format=s16",
            "--channels=1", "--rate=" + str(RATE), "-",
        ]
        env = {**os.environ, "PIPEWIRE_DEBUG": "0", "JACK_NO_START_SERVER": "1"}
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
        except Exception as e:
            _log_error("Failed to start recording: %s", e)
            return ""

        # Pre-buffer a small amount of audio while connecting — just enough
        # to ensure the server has audio immediately after the first frame.
        pre_buffer = []
        pre_start = time.time()
        while time.time() - pre_start < 3.0:
            raw = proc.stdout.read(CHUNK_BYTES)
            if raw:
                pre_buffer.append(raw)
                if len(pre_buffer) >= 5:  # 200ms — enough to keep session alive
                    break
            else:
                time.sleep(0.005)

        if self._debug:
            print(f"[audio] pre_buffer={len(pre_buffer)} chunks", file=sys.stderr)

        # Build auth URL
        url = _build_auth_url(api_key, api_secret)
        logger.info("Connecting to iFlytek...")

        # Events for cross-thread coordination
        ws_opened = threading.Event()
        ws_error = threading.Event()
        ws_error_msg = [None]

        result_queue = []

        def on_open(ws):
            logger.info("WebSocket opened")
            ws_opened.set()

        def on_message(ws, message):
            try:
                data = json.loads(message)
                code = data.get("code", -1)

                if self._debug:
                    status = data.get("data", {}).get("status", "?")
                    has_result = "result" in (data.get("data", {}) or {})
                    msg_preview = message if len(message) < 300 else message[:200] + "..."
                    print(f"\n[iFlytek MSG] code={code} status={status} has_result={has_result} body={msg_preview}", file=sys.stderr)

                if code != 0:
                    _log_error("iFlytek error %d: %s", code, data.get("message", ""))
                    ws_error_msg[0] = data.get("message", f"Error {code}")
                    ws_error.set()
                    return

                result = data.get("data", {}).get("result")
                if result:
                    text = self._process_result(result)
                    result_queue.append(text)
                    if on_partial:
                        on_partial(text)

                # Server signals end of recognition
                if data.get("data", {}).get("status") == 2:
                    logger.info("Server ended recognition")
                    self._has_ended = True
            except Exception:
                logger.exception("Error parsing iFlytek response")

        def on_error(ws, error):
            _log_error("WebSocket error: %s", error)
            ws_error_msg[0] = str(error)
            ws_error.set()

        def on_close(ws, close_status, close_msg):
            logger.info("WebSocket closed: %s %s", close_status, close_msg)

        ws = websocket.WebSocketApp(
            url,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )

        # Run WebSocket with proxy explicitly disabled — the library falls
        # back to https_proxy/http_proxy env vars when http_proxy_host is None.
        ws_thread = threading.Thread(
            target=lambda: ws.run_forever(
                sslopt={"cert_reqs": 0},
                http_proxy_host=None,
                http_proxy_port=None,
                http_no_proxy=["*"],
            ),
            daemon=True,
        )
        ws_thread.start()

        # Wait for WebSocket to open (longer timeout for slow/proxied connections)
        if not ws_opened.wait(timeout=10):
            _log_error("WebSocket connection timeout after 10s")
            ws.close()
            ws_thread.join(timeout=3)
            proc.terminate()
            return ""

        if ws_error.is_set():
            _log_error("WebSocket error before recording: %s", ws_error_msg[0])
            proc.terminate()
            return ""

        # Send first frame with parameters
        first_frame = {
            "common": {"app_id": app_id},
            "business": {
                "language": cfg.get("iflytek_language", "zh_cn"),
                "domain": "iat",
                "accent": cfg.get("iflytek_accent", "mandarin"),
                "vad_eos": cfg.get("iflytek_vad_eos", 3000),
                "dwa": "wpgs",
                "ptt": cfg.get("iflytek_ptt", 1),
                "nunum": cfg.get("iflytek_nunum", 1),
            },
            "data": {
                "status": 0,
                "format": "audio/L16;rate=16000",
                "encoding": "raw",
                "audio": "",
            },
        }
        ws.send(json.dumps(first_frame))

        start = time.time()
        last_level_time = 0
        text = ""
        chunk_count = 0
        sent_count = 0

        # Flush pre-buffered audio — beep is long over, no skip needed
        for raw in pre_buffer:
            chunk_count += 1
            sent_count += 1
            audio_b64 = base64.b64encode(raw).decode()
            frame = {
                "data": {
                    "status": 1,
                    "format": "audio/L16;rate=16000",
                    "encoding": "raw",
                    "audio": audio_b64,
                }
            }
            try:
                ws.send(json.dumps(frame))
            except Exception:
                break

        try:
            while not self._has_ended and (time.time() - start) < 60:
                # Check manual stop
                if stop_fn and stop_fn():
                    logger.info("Manual stop requested")
                    break

                # Check for errors
                if ws_error.is_set():
                    _log_error("WebSocket error: %s", ws_error_msg[0])
                    break

                raw = proc.stdout.read(CHUNK_BYTES)
                if not raw:
                    time.sleep(0.01)
                    continue

                chunk_count += 1
                # Skip first 5 chunks (~200ms) to avoid capturing beep tail
                if chunk_count <= 5:
                    continue

                # Audio level (RMS)
                rms = self._calc_rms(raw)
                if on_level and (time.time() - last_level_time) > 0.05:
                    on_level(rms)
                    last_level_time = time.time()

                # Send base64-encoded chunk
                sent_count += 1
                audio_b64 = base64.b64encode(raw).decode()
                frame = {
                    "data": {
                        "status": 1,
                        "format": "audio/L16;rate=16000",
                        "encoding": "raw",
                        "audio": audio_b64,
                    }
                }
                try:
                    ws.send(json.dumps(frame))
                except Exception:
                    logger.exception("Failed to send audio frame")
                    break

                # Swap queue to avoid race with WebSocket thread
                if result_queue:
                    snapshot = result_queue
                    result_queue = []
                    text = snapshot[-1]

        finally:
            # Log audio stats before cleanup
            if self._debug:
                audio_ms = (chunk_count * CHUNK_BYTES) // (16000 * 2) * 1000
                print(f"\n[audio] total_chunks={chunk_count} skipped=5 sent={sent_count} "
                      f"audio_ms={audio_ms} duration_s={time.time()-start:.1f}", file=sys.stderr)

            proc.terminate()
            try:
                proc.wait(timeout=2)
            except Exception:
                proc.kill()

            # Send end frame
            try:
                end_frame = {
                    "data": {
                        "status": 2,
                        "format": "audio/L16;rate=16000",
                        "encoding": "raw",
                        "audio": "",
                    }
                }
                ws.send(json.dumps(end_frame))
            except Exception:
                pass

            # Wait briefly for final result
            time.sleep(0.5)

            # Swap queue to avoid race with WebSocket thread
            if result_queue:
                snapshot = result_queue
                result_queue = []
                text = snapshot[-1]

            ws.close()
            ws_thread.join(timeout=3)

        # Clean up: collapse whitespace, strip noise chars from edges
        text = text.strip() if text else ""
        if text:
            import re
            # Strip leading/trailing non-word noise (bare consonants like "v")
            # that come from audio artifacts (beep, buzz, click).
            text = re.sub(r'^[\s*v]+', '', text)
            text = re.sub(r'[\s*v]+$', '', text)
            text = text.strip()

        logger.info("Final text: %r", text)
        return text

    # ── Result processing (dynamic correction) ──

    def _process_result(self, result):
        """Process a single iFlytek result with dynamic correction (wpgs).

        In wpgs mode:
        - pgs=apd: text is a delta (new text to append)
        - pgs=rpl: text is full replacement for range rg — supersedes all
          intermediate results between rg[0] and sn

        Returns the full accumulated text after applying this result.
        """
        ws_list = result.get("ws", [])
        text = ""
        for w in ws_list:
            for cw in w.get("cw", []):
                text += cw.get("w", "")

        sn = result.get("sn", 0)
        pgs = result.get("pgs", "")
        rg = result.get("rg", [sn, sn])
        ls = result.get("ls", False)

        # When pgs is empty (non-wpgs mode or initial result), the text is
        # the full accumulated text — treat as rpl to avoid concatenation dupes.
        if not pgs or pgs not in ("rpl", "apd"):
            pgs = "rpl"

        if self._debug:
            print(f"[wpgs] sn={sn} pgs={pgs} rg={rg} ls={ls} text={text!r}", file=sys.stderr)

        with self._result_lock:
            for i in range(rg[0], sn):
                self._result_cache.pop(i, None)
            self._result_cache[sn] = (pgs, text)

            accumulated = self._get_accumulated()
            if self._debug:
                print(f"[wpgs] cache={dict(self._result_cache)!r} accumulated={accumulated!r}", file=sys.stderr)
            return accumulated

    def _get_accumulated(self):
        """Build accumulated text from cached results.

        In wpgs mode, results are stored as (pgs, text):
        - rpl with rg[0]==0: full text from the beginning — it IS the base.
          Later rpl entries in [0, sn) are removed by _process_result, so
          only the latest rpl that starts at 0 survives.
        - rpl with rg[0]>0: replaces only a suffix range. Text before rg[0]
          is preserved from earlier (lower-sn) entries.
        - apd: delta to append after everything.

        Strategy: iterate in sn order. rpl entries reset the result, apd
        entries append. This works because _process_result already removed
        entries in the replaced range [rg[0], sn), so the remaining rpls
        represent non-overlapping segments.
        """
        items = sorted(self._result_cache.items(), key=lambda x: x[0])
        if not items:
            return ""

        result = ""
        for _sn, (_pgs, text) in items:
            if _pgs == "rpl":
                # rpl is full text for its segment. If it starts at 0, it's
                # the base; if not, previous segments + this one form the text.
                # Since _process_result already cleared the replaced range,
                # each surviving rpl represents a non-overlapping segment.
                # Simply concatenating handles both cases correctly.
                result += text
            else:
                # apd — delta, always append
                result += text
        return result

    # ── Helpers ──

    @staticmethod
    def _calc_rms(frame):
        """Calculate RMS level from raw PCM frame."""
        import struct
        count = len(frame) // 2
        if count == 0:
            return 0
        vals = struct.unpack("<" + str(count) + "h", frame[:count * 2])
        return int((sum(v * v for v in vals) / count) ** 0.5)

    # ── BaseEngine interface (for batch mode fallback) ──

    def transcribe(self, wav_path):
        """Not implemented — use recognize_stream() for streaming."""
        raise NotImplementedError("iFlytek engine only supports streaming mode")
