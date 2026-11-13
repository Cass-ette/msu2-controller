"""Live content sources and debounced MSU2 button input."""
from __future__ import annotations

import sys
import time

import psutil
from PIL import Image, ImageGrab, ImageOps
from platform_support import screen_capture_access


class ContentError(RuntimeError):
    """A content-source error that should not disconnect a healthy display."""


class ScreenCapturePermissionError(ContentError):
    """Allow the GUI to offer permission help instead of treating this as USB failure."""


class SystemMonitor:
    def __init__(self):
        self.started = False
        self.last_time = None
        self.last_network = None

    def sample(self):
        # All calls come from the same worker, as cpu_percent keeps thread-local state.
        if not self.started:
            psutil.cpu_percent(interval=None)
            self.started = True
            cpu = psutil.cpu_percent(interval=0.05)
        else:
            cpu = psutil.cpu_percent(interval=None)
        memory = psutil.virtual_memory()
        network = psutil.net_io_counters()
        now = time.monotonic()
        up = down = None
        if (network is not None and self.last_network is not None
                and self.last_time is not None and now > self.last_time):
            elapsed = now - self.last_time
            up = max(0, network.bytes_sent - self.last_network.bytes_sent) / elapsed
            down = max(0, network.bytes_recv - self.last_network.bytes_recv) / elapsed
        self.last_time, self.last_network = now, network
        return {"cpu": cpu, "memory": memory.percent,
                "memory_used": memory.used, "memory_total": memory.total,
                "upload": up, "download": down}


def format_rate(value):
    if value is None:
        return "--"
    for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
        if value < 1024 or unit == "GB/s":
            return f"{value:.1f} {unit}" if value < 100 else f"{value:.0f} {unit}"
        value /= 1024


def capture_desktop(size, scope="主屏幕", fit="完整显示", background="#101824"):
    try:
        if sys.platform == "win32":
            source = ImageGrab.grab(all_screens=(scope == "整个桌面"))
        elif sys.platform == "darwin":
            if scope != "主屏幕":
                raise ContentError("Mac 版投屏目前支持主屏幕，请选择主屏幕。")
            if screen_capture_access().get('granted') is False:
                raise ScreenCapturePermissionError("投屏需要屏幕录制权限。")
            source = ImageGrab.grab()
        else:
            source = ImageGrab.grab()
        source = source.convert("RGB")
        if fit == "居中裁剪":
            return ImageOps.fit(source, size, method=Image.Resampling.LANCZOS)
        content = ImageOps.contain(source, size, method=Image.Resampling.LANCZOS)
        frame = Image.new("RGB", size, background)
        frame.paste(content, ((size[0] - content.width) // 2,
                              (size[1] - content.height) // 2))
        return frame
    except ContentError:
        raise
    except Exception as exc:
        if sys.platform == "darwin" and screen_capture_access().get('granted') is False:
            raise ScreenCapturePermissionError("投屏需要屏幕录制权限。") from exc
        raise ContentError(f"无法截取桌面：{exc}") from exc


class ButtonDebouncer:
    """Trigger once on a stable release, with ADC hysteresis and debounce."""
    def __init__(self, baseline, debounce=0.03):
        self.baseline = float(baseline)
        self.debounce = debounce
        self.pressed = False
        self.candidate = False
        self.candidate_since = None

    def update(self, value, now=None):
        now = time.monotonic() if now is None else now
        if not self.pressed and value > self.baseline:
            # Also recover if the button was held during connection/calibration.
            self.baseline = float(value)
        drop = 70 if self.pressed else 125
        wanted = value < max(0, self.baseline - drop)
        if wanted != self.candidate or self.candidate_since is None:
            self.candidate = wanted
            self.candidate_since = now
        if wanted != self.pressed and now - self.candidate_since >= self.debounce:
            was_pressed = self.pressed
            self.pressed = wanted
            return was_pressed and not wanted
        return False
