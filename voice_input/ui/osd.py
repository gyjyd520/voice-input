"""GTK3 OSD (On-Screen Display) overlay for recording feedback."""

import math
import queue
import threading

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gtk, Gdk, GLib


class LevelMeter(Gtk.LevelBar):
    """Audio level meter using GTK LevelBar (avoids cairo marshaling issues)."""

    _HEIGHT = 28
    _ATTACK = 0.4
    _RELEASE = 0.15
    _PULSE_INTERVAL = 33  # ms

    def __init__(self):
        super().__init__()
        self.set_size_request(-1, self._HEIGHT)
        self.set_min_value(0.0)
        self.set_max_value(1.0)
        self._target = 0.0
        self._smoothed = 0.0
        self._processing = False
        self._pulse_t = 0.0
        self._pulse_id = None

    def set_level(self, rms):
        self._target = min(1.0, (rms / 4000) ** 0.7)

    def set_processing(self, processing):
        self._processing = processing
        if processing:
            if not self._pulse_id:
                self._pulse_t = 0.0
                self._pulse_id = GLib.timeout_add(self._PULSE_INTERVAL, self._pulse_tick)
        else:
            if self._pulse_id:
                GLib.source_remove(self._pulse_id)
                self._pulse_id = None
            self._target = 0.0

    def _pulse_tick(self):
        if self._processing:
            self._pulse_t += 0.05
            level = 0.55 + 0.15 * math.sin(self._pulse_t * 2.0)
            self.set_value(level)
            return True
        else:
            factor = self._ATTACK if self._target > self._smoothed else self._RELEASE
            self._smoothed += (self._target - self._smoothed) * factor
            self.set_value(self._smoothed)
            return self._smoothed > 0.01  # Stop timer when near zero

    def stop(self):
        if self._pulse_id:
            GLib.source_remove(self._pulse_id)
            self._pulse_id = None


class OsdWindow:
    """Floating OSD overlay for voice recording feedback.

    Thread-safe: all GTK widget updates go through queue.Queue
    and are processed on the GTK main thread via GLib.timeout_add.
    """

    def __init__(self):
        self._msg_queue = queue.Queue()
        self._result_event = threading.Event()
        self._user_action = None
        self._level = 0
        self._phase = "hidden"  # hidden → recording → processing → review → hidden

        # Create GTK window (must be called from GTK thread)
        self._create_window()

    def _create_window(self):
        self.window = Gtk.Window(type=Gtk.WindowType.POPUP)
        self.window.set_decorated(False)
        self.window.set_keep_above(True)
        self.window.set_accept_focus(False)
        self.window.set_skip_taskbar_hint(True)
        self.window.set_skip_pager_hint(True)
        self.window.set_app_paintable(True)
        self.window.set_size_request(400, -1)
        self.window.connect("destroy", self._on_destroy)

        # Override theme backgrounds directly (bypass CSS cascade which Yaru overrides)
        dark_bg = Gdk.RGBA()
        dark_bg.parse("#1e1e24")
        self.window.override_background_color(Gtk.StateFlags.NORMAL, dark_bg)

        # CSS for text, buttons, levelbar (these work reliably)
        css = Gtk.CssProvider()
        css.load_from_data(b"""
            label {
                color: rgba(255, 255, 255, 0.95);
                font-family: "Noto Sans CJK SC", "Noto Sans", sans-serif;
            }
            .status-label {
                font-size: 13px;
                font-weight: 600;
            }
            .text-label {
                font-size: 16px;
                color: rgba(255, 255, 255, 1.0);
            }
            .error-label {
                font-size: 14px;
                color: rgba(255, 120, 120, 1.0);
            }
            button {
                font-family: "Noto Sans CJK SC", "Noto Sans", sans-serif;
                font-size: 13px;
                font-weight: 500;
                color: rgba(255, 255, 255, 0.90);
                background: rgba(255, 255, 255, 0.08);
                border: none;
                border-radius: 8px;
                padding: 8px 20px;
            }
            button:hover {
                background: rgba(255, 255, 255, 0.16);
            }
            button.confirm {
                background: rgba(52, 120, 246, 0.85);
                color: white;
            }
            button.confirm:hover {
                background: rgba(52, 120, 246, 1.0);
            }
            button.cancel-btn {
                background: transparent;
                border: none;
                padding: 4px 8px;
                font-size: 16px;
                color: rgba(255, 255, 255, 0.4);
            }
            button.cancel-btn:hover {
                color: rgba(255, 255, 255, 0.8);
                background: rgba(255, 255, 255, 0.06);
            }
            levelbar block.filled {
                background: rgba(82, 180, 255, 0.90);
                border-radius: 2px;
            }
            levelbar block.empty {
                background: rgba(255, 255, 255, 0.08);
                border-radius: 2px;
            }
        """)
        style_ctx = self.window.get_style_context()
        style_ctx.add_provider(css, Gtk.STYLE_PROVIDER_PRIORITY_USER)

        # Root vertical box
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.window.add(root)

        # Blue accent bar at top (3px, direct color — no CSS needed)
        accent = Gtk.EventBox()
        blue_accent = Gdk.RGBA()
        blue_accent.parse("#5294ff")
        accent.override_background_color(Gtk.StateFlags.NORMAL, blue_accent)
        accent.set_size_request(-1, 3)
        root.pack_start(accent, False, False, 0)

        # Main layout
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.set_margin_top(12)
        outer.set_margin_bottom(12)
        outer.set_margin_start(18)
        outer.set_margin_end(18)
        root.pack_start(outer, False, False, 0)

        # Header: status + cancel
        header = Gtk.Box(spacing=8)
        self.status_label = Gtk.Label(label="")
        self.status_label.get_style_context().add_class("status-label")
        self.status_label.set_halign(Gtk.Align.START)
        header.pack_start(self.status_label, True, True, 0)

        self.cancel_btn = Gtk.Button(label="✕")
        self.cancel_btn.get_style_context().add_class("cancel-btn")
        self.cancel_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.cancel_btn.connect("clicked", self._on_cancel)
        header.pack_end(self.cancel_btn, False, False, 0)

        outer.pack_start(header, False, False, 0)

        # Level meter (GTK LevelBar, no cairo needed)
        self.level_meter = LevelMeter()
        self.level_meter.set_margin_top(10)
        self.level_meter.set_margin_bottom(8)
        outer.pack_start(self.level_meter, False, False, 0)

        # Text display
        self.text_label = Gtk.Label(label="")
        self.text_label.get_style_context().add_class("text-label")
        self.text_label.set_line_wrap(True)
        self.text_label.set_max_width_chars(35)
        self.text_label.set_xalign(0)
        self.text_label.set_margin_bottom(10)
        outer.pack_start(self.text_label, False, False, 0)

        # Action buttons (hidden initially)
        self.action_box = Gtk.Box(spacing=8)
        self.action_box.set_homogeneous(True)

        self.discard_btn = Gtk.Button(label="取消 ✕")
        self.discard_btn.connect("clicked", self._on_discard)
        self.action_box.pack_start(self.discard_btn, True, True, 0)

        self.edit_btn = Gtk.Button(label="编辑")
        self.edit_btn.connect("clicked", self._on_edit)
        self.action_box.pack_start(self.edit_btn, True, True, 0)

        self.confirm_btn = Gtk.Button(label="确认 ✓")
        self.confirm_btn.get_style_context().add_class("confirm")
        self.confirm_btn.connect("clicked", self._on_confirm)
        self.action_box.pack_start(self.confirm_btn, True, True, 0)

        outer.pack_start(self.action_box, False, False, 0)

        # Message poller (20Hz)
        self._poll_id = GLib.timeout_add(50, self._poll_messages)

    def _position_window(self):
        """Position at bottom-center of current monitor."""
        display = Gdk.Display.get_default()
        if not display:
            return

        # Try to position near cursor's monitor
        try:
            seat = display.get_default_seat()
            ptr = seat.get_pointer()
            screen, x, y = ptr.get_position()
            monitor = display.get_monitor_at_point(x, y)
        except Exception:
            monitor = display.get_primary_monitor()

        if not monitor:
            return

        geom = monitor.get_geometry()
        scale = monitor.get_scale_factor()

        win_w = 400
        self.window.realize()
        _, pref_h = self.window.get_preferred_height()
        win_h = max(pref_h, 100)

        pos_x = geom.x + (geom.width / scale - win_w) // 2
        pos_y = geom.y + geom.height / scale - win_h - 80

        try:
            self.window.move(int(pos_x), int(pos_y))
        except Exception:
            self.window.set_position(Gtk.WindowPosition.CENTER)

    # ====== Public API (thread-safe, callable from any thread) ======

    def show_recording(self):
        self._msg_queue.put(('show_recording', None))

    def show_processing(self):
        self._msg_queue.put(('show_processing', None))

    def show_refining(self):
        self._msg_queue.put(('show_refining', None))

    def show_result(self, text, timeout=30):
        """Show recognized text and wait for user action.
        Returns 'confirm', 'edit', 'discard', or 'timeout'."""
        self._result_event.clear()
        self._user_action = None
        self._msg_queue.put(('show_result', text))
        self._result_event.wait(timeout=timeout)
        return self._user_action or 'timeout'

    def show_error(self, msg):
        self._msg_queue.put(('show_error', msg))

    def update_level(self, rms):
        self._msg_queue.put(('level', rms))

    def update_text(self, text):
        self._msg_queue.put(('text', text))

    def hide(self):
        self._msg_queue.put(('hide', None))

    # ====== Internal: message processing on GTK thread ======

    def _poll_messages(self):
        """Process queued messages on the GTK thread."""
        try:
            while True:
                msg = self._msg_queue.get_nowait()
                if isinstance(msg, tuple):
                    cmd, data = msg
                    handler = getattr(self, f'_handle_{cmd}', None)
                    if handler:
                        handler(data)
        except queue.Empty:
            pass
        return True  # keep timer alive

    def _handle_show_recording(self, _):
        self._phase = "recording"
        self.status_label.set_text("● 正在聆听...")
        self.status_label.get_style_context().remove_class("error-label")
        self.text_label.set_text("")
        self.text_label.get_style_context().remove_class("error-label")
        self.action_box.hide()
        self.cancel_btn.show()
        self.level_meter.set_processing(False)
        self._position_window()
        self.window.show_all()
        self.action_box.hide()

    def _handle_show_processing(self, _):
        self._phase = "processing"
        self.status_label.set_text("⏳ 处理中...")
        self.text_label.set_text("")
        self.level_meter.set_processing(True)
        self.cancel_btn.hide()

    def _handle_show_refining(self, _):
        self._phase = "refining"
        self.status_label.set_text("🔍 正在优化...")
        self.level_meter.set_processing(True)
        self.cancel_btn.hide()
        self.action_box.hide()

    def _handle_show_result(self, text):
        self._phase = "review"
        self.status_label.set_text("✓ 识别完成")
        self.status_label.get_style_context().remove_class("error-label")
        self.text_label.set_text(text)
        self.text_label.get_style_context().remove_class("error-label")
        self.level_meter.set_processing(False)
        self.level_meter.set_level(0)
        self.cancel_btn.hide()
        self.action_box.show_all()

    def _handle_show_error(self, msg):
        self._phase = "error"
        self.status_label.set_text("⚠")
        self.text_label.set_text(msg)
        self.text_label.get_style_context().add_class("error-label")
        self.level_meter.set_processing(False)
        self.level_meter.set_level(0)
        self.cancel_btn.hide()
        self.action_box.hide()
        self._position_window()
        self.window.show_all()
        self.action_box.hide()
        # Auto-hide error after 2s
        GLib.timeout_add(2000, lambda: self._handle_hide(None) or False)

    def _handle_level(self, rms):
        self.level_meter.set_level(rms)
        self._level = rms

    def _handle_text(self, text):
        if self._phase == "recording":
            self.text_label.set_text(text)

    def _handle_hide(self, _):
        self._phase = "hidden"
        self.window.hide()
        self._result_event.set()

    # ====== Button callbacks (run on GTK thread) ======

    def _on_cancel(self, btn):
        self._user_action = "discard"
        self._result_event.set()
        self._handle_hide(None)

    def _on_confirm(self, btn):
        self._user_action = "confirm"
        self._result_event.set()

    def _on_edit(self, btn):
        self._user_action = "edit"
        self._result_event.set()

    def _on_discard(self, btn):
        self._user_action = "discard"
        self._result_event.set()

    def _on_destroy(self, widget):
        self.level_meter.stop()
        if self._poll_id:
            GLib.source_remove(self._poll_id)
