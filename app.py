"""MSU2 controller with hardware buttons, system status and desktop mirroring."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from queue import Empty, Queue
from statistics import median
import logging
from logging.handlers import RotatingFileHandler
import sys
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import colorchooser, filedialog, messagebox, ttk

from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageTk

from msu2 import Display, find_ports, test_image
from features import ButtonDebouncer, ContentError, SystemMonitor, capture_desktop, format_rate
from offline_backup import safe_burn
from notifications import NotificationInbox
from power_monitor import PowerMonitor
from power_graphics import render_power
from app_paths import data_directory, log_directory

MODES = ("文字", "时钟", "电脑状态", "功率与电池", "图片", "投屏", "测试色条")
DYNAMIC_MODES = ("时钟", "电脑状态", "功率与电池", "投屏")


def configure_diagnostics():
    logger = logging.getLogger('msu2')
    if any(isinstance(handler, RotatingFileHandler) for handler in logger.handlers):
        return
    try:
        directory = log_directory()
        directory.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(directory / 'connection.log', maxBytes=1024*1024,
                                      backupCount=2, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        logger.addHandler(handler)
    except OSError:
        # Failure to create an optional diagnostic file must not prevent startup.
        pass


@lru_cache(maxsize=32)
def font(size):
    candidates = [
        ("C:/Windows/Fonts/msyh.ttc", 0),
        ("/System/Library/Fonts/PingFang.ttc", None),
    ]
    if sys.platform == "darwin":
        # Recent macOS versions keep PingFang in the system font assets.
        candidates.extend((str(path), None) for path in sorted(
            Path("/System/Library/AssetsV2").glob(
                "com_apple_MobileAsset_Font*/*.asset/AssetData/PingFang.ttc")))
    candidates.extend([
        ("/System/Library/Fonts/STHeiti Light.ttc", 1),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
    ])
    for path, index in candidates:
        if Path(path).is_file():
            if index is not None:
                return ImageFont.truetype(path, size, index=index)
            # Collection order varies; select the simplified Chinese regular face.
            for face_index in range(32):
                try:
                    face = ImageFont.truetype(path, size, index=face_index)
                except OSError:
                    break
                if face.getname() == ("PingFang SC", "Regular"):
                    return face
    return ImageFont.load_default(size=size)


def fitted_font(text, size, width):
    while size > 8:
        result = font(size)
        if result.getlength(text) <= width:
            return result
        size -= 1
    return font(8)


def render(size, mode, title, subtitle, foreground, background, image_path, metrics=None,
           clock_background=None, picture=None):
    width, height = size
    if mode == "电脑状态":
        return render_status(size, metrics, foreground, background)
    if mode == '功率与电池':
        from power_monitor import PowerSample
        return render_power(size, metrics or PowerSample(time.monotonic()), font)
    if mode == "图片":
        if picture is not None:
            return picture.copy() if picture.size == size else picture.resize(size,Image.Resampling.LANCZOS)
        if not image_path:
            raise ValueError("请先选择图片")
        with Image.open(image_path) as source:
            return ImageOps.fit(ImageOps.exif_transpose(source).convert("RGB"),
                                size, method=Image.Resampling.LANCZOS)
    if mode == "测试色条":
        return test_image(size)
    image = (ImageOps.fit(clock_background.convert('RGB'), size,
                         method=Image.Resampling.LANCZOS)
             if mode == '时钟' and clock_background is not None
             else Image.new("RGB", size, background))
    draw = ImageDraw.Draw(image)
    if mode == "时钟":
        now = datetime.now()
        title = now.strftime("%H:%M:%S")
        subtitle = now.strftime("%Y-%m-%d  %a")
    title = title[:80]
    subtitle = subtitle[:100]
    main_font = fitted_font(title, max(12, height // 3), width - 12)
    small_font = fitted_font(subtitle, max(9, height // 7), width - 12)
    draw.text((width // 2, height * 0.36), title, anchor="mm",
              fill=foreground, font=main_font)
    draw.text((width // 2, height * 0.74), subtitle, anchor="mm",
              fill="#bac5d3", font=small_font)
    draw.rectangle((0, height - 3, width - 1, height - 1), fill=foreground)
    return image


def render_status(size, metrics, foreground, background):
    width, height = size
    image = Image.new("RGB", size, background)
    draw = ImageDraw.Draw(image)
    scale = height / 80
    padding = max(4, width // 27)
    face = font(max(8, int(11 * scale)))
    small = font(max(8, int(10 * scale)))
    metrics = metrics or {}
    for label, key, y, color in (
            ("CPU", "cpu", 5, foreground),
            ("内存", "memory", 27, "#60a5fa")):
        value = metrics.get(key)
        y = int(y * scale)
        draw.text((padding, y), label, anchor="lt", font=face, fill="white")
        text = "--" if value is None else f"{value:.0f}%"
        draw.text((width - padding, y), text, anchor="rt", font=face, fill=color)
        top = y + int(15 * scale)
        bottom = top + max(2, int(3 * scale))
        draw.rectangle((padding, top, width - padding - 1, bottom), fill="#263244")
        if value is not None and value > 0:
            right = padding + max(1, int((width - 2 * padding) * min(100, value) / 100)) - 1
            draw.rectangle((padding, top, right, bottom), fill=color)
    for label, key, y in (("上行", "upload", 51), ("下行", "download", 66)):
        draw.text((padding, int(y * scale)), label, anchor="lt", font=small, fill="#bac5d3")
        draw.text((width - padding, int(y * scale)), format_rate(metrics.get(key)),
                  anchor="rt", font=small, fill="white")
    return image


def notification_lines(text, face, width, limit):
    """Wrap CJK and Latin text by rendered width, with visible truncation."""
    lines, line = [], ''
    for char in text.replace('\t', ' '):
        if char == '\n' or face.getlength(line+char) > width:
            lines.append(line)
            line = '' if char == '\n' else char
            if len(lines) >= limit:
                while lines[-1] and face.getlength(lines[-1]+'…') > width:
                    lines[-1] = lines[-1][:-1]
                lines[-1] += '…'
                return lines
        else:
            line += char
    if line or not lines:
        lines.append(line)
    return lines


def render_notification(size, notification):
    width, height = size
    scale = height/80
    accent = {'info':'#60a5fa', 'success':'#6ee7b7',
              'warning':'#fbbf24', 'error':'#fb7185'}[notification.kind]
    image = Image.new('RGB', size, '#101824')
    draw = ImageDraw.Draw(image)
    padding = max(4, width//27)
    for text, pixels, y, color, lines in (
            (notification.source, 9, 4, accent, 1),
            (notification.title, 16, 19, 'white', 1),
            (notification.message, 11, 42, '#bac5d3', 2)):
        face = font(max(8, int(pixels*scale)))
        for index, line in enumerate(notification_lines(text, face, width-2*padding, lines)):
            draw.text((padding, int((y+index*14)*scale)), line, anchor='lt', font=face, fill=color)
    draw.rectangle((0, height-3, width-1, height-1), fill=accent)
    return image


class App:
    def __init__(self, root):
        configure_diagnostics()
        self.root = root
        self.display = None
        self.size = (160, 80)
        self.busy = False
        self.closing = False
        self.close_requested = False
        self.modal = False
        self.generation = 0
        self.pending_frame = False
        self.next_frame_at = 0
        self.next_button_at = 0
        self.button_detector = None
        self.seen_recovery_count = 0
        self.frame_cache = {}
        self.animation_editor = None
        self.asset_editor = None
        self.notification_inbox = NotificationInbox()
        self.notification_service = None
        self.active_notification = None
        self.notification_until = None
        self.notification_frame = None
        self.monitor = SystemMonitor()
        self.power_monitor = PowerMonitor()
        self.power_window = None
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.results = Queue()
        self.progress_updates = Queue()
        self.image_path = ""
        self.edited_image = None
        self.image_framing = None
        self.clock_framing = None
        self.image_editor = None
        self.clock_background = None
        self.foreground = "#6ee7b7"
        self.background = "#101824"
        self.backup_directory = data_directory() / 'backups'
        root.title("MSU2 自定义小屏")
        root.minsize(760, 620)
        families = set(tkfont.families(root))
        for family in ('Microsoft YaHei UI', 'Microsoft YaHei', 'PingFang SC',
                       'Heiti SC', 'Source Han Sans SC', 'Noto Sans CJK SC'):
            if family in families:
                for name in ('TkDefaultFont', 'TkTextFont', 'TkMenuFont', 'TkHeadingFont'):
                    tkfont.nametofont(name).configure(family=family)
                break
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.status = tk.StringVar(value="未连接。请先退出原厂助手。")
        self.port = tk.StringVar()
        self.mode = tk.StringVar(value="文字")
        self.title = tk.StringVar(value="你好，MSU2")
        self.subtitle = tk.StringVar(value="我的第一块自定义小屏")
        self.live = tk.BooleanVar(value=True)
        self.upside_down = tk.BooleanVar(value=False)
        self.hardware_button = tk.BooleanVar(value=True)
        self.screen_scope = tk.StringVar(value="主屏幕")
        self.screen_fit = tk.StringVar(value="完整显示")
        self.screen_fps = tk.StringVar(value="5")
        self.api_enabled = tk.BooleanVar(value=False)

        body = ttk.Frame(root, padding=20)
        body.pack(fill="both", expand=True)
        row = ttk.Frame(body)
        row.pack(fill="x")
        ttk.Label(row, text="串口").pack(side="left", padx=(0, 8))
        self.ports = ttk.Combobox(row, textvariable=self.port, state="readonly", width=32)
        self.ports.pack(side="left")
        ttk.Button(row, text="刷新", command=self.refresh).pack(side="left", padx=8)
        self.connection = ttk.Button(row, text="连接屏幕", command=self.toggle_connection)
        self.connection.pack(side="right")
        ttk.Label(body, textvariable=self.status, wraplength=650).pack(anchor="w", pady=(10, 16))
        self.preview = ttk.Label(body, anchor="center")
        self.preview.pack(fill="x", pady=(0, 12))
        ttk.Label(body, text="APP 预览保持正向；上下倒转只改变小屏方向。分辨率连接后自动读取。").pack(anchor="w")

        controls = ttk.Frame(body)
        controls.pack(fill="x", pady=(16, 0))
        ttk.Label(controls, text="显示内容").grid(row=0, column=0, sticky="w", pady=6)
        modes = ttk.Combobox(controls, textvariable=self.mode,
                            values=MODES,
                            state="readonly", width=18)
        modes.grid(row=0, column=1, sticky="w", padx=10)
        modes.bind("<<ComboboxSelected>>", lambda event: self.mode_changed())
        ttk.Button(controls, text="选择图片", command=self.choose_image).grid(row=0, column=2)
        ttk.Label(controls, text="主文字").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Entry(controls, textvariable=self.title, width=45).grid(row=1, column=1, columnspan=2, sticky="ew", padx=10)
        ttk.Label(controls, text="副文字").grid(row=2, column=0, sticky="w", pady=6)
        ttk.Entry(controls, textvariable=self.subtitle, width=45).grid(row=2, column=1, columnspan=2, sticky="ew", padx=10)
        ttk.Checkbutton(controls, text="实体按键切换",
                        variable=self.hardware_button).grid(row=3, column=0, sticky="w", pady=6)
        ttk.Checkbutton(controls, text="上下倒转（180°）",
                        variable=self.upside_down).grid(
                            row=3, column=1, columnspan=2, sticky="w", padx=10, pady=6)
        controls.columnconfigure(1, weight=1)
        ttk.Button(controls, text='时钟背景…', command=self.choose_clock_background).grid(row=4, column=0, sticky='w', pady=6)
        ttk.Button(controls, text='清除时钟背景', command=self.clear_clock_background).grid(row=4, column=1, sticky='w', padx=10)
        ttk.Button(controls, text='功率详情…', command=self.open_power_window).grid(row=4, column=2)
        self.screen_options = ttk.Frame(controls)
        self.screen_options.grid(row=5, column=0, columnspan=3, sticky="w", pady=6)
        for label, variable, values, width in (
                ("投屏范围", self.screen_scope,
                 ("主屏幕",) if sys.platform == "darwin" else ("主屏幕", "整个桌面"), 10),
                ("缩放", self.screen_fit, ("完整显示", "居中裁剪"), 10),
                ("帧率", self.screen_fps, ("2", "5", "10"), 4)):
            ttk.Label(self.screen_options, text=label).pack(side="left", padx=(0, 5))
            control = ttk.Combobox(self.screen_options, textvariable=variable,
                                 values=values, state="readonly", width=width)
            control.pack(side="left", padx=(0, 10))
            control.bind("<<ComboboxSelected>>", lambda event: self.content_changed())
        self.screen_options.grid_remove()
        ttk.Button(controls,text='调整图片取景…',command=self.adjust_image).grid(row=6,column=0,sticky='w',pady=6)
        ttk.Button(controls,text='调整时钟底图…',command=self.adjust_clock_background).grid(row=6,column=1,sticky='w',padx=10,pady=6)
        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(16, 0))
        ttk.Button(buttons, text="文字颜色", command=lambda: self.choose_color("foreground")).pack(side="left")
        ttk.Button(buttons, text="背景颜色", command=lambda: self.choose_color("background")).pack(side="left", padx=8)
        ttk.Checkbutton(buttons, text="实时更新", variable=self.live).pack(side="left", padx=8)
        ttk.Button(buttons, text="显示到屏幕", command=self.send).pack(side="right")
        offline = ttk.Frame(body)
        offline.pack(fill="x", pady=(12, 0))
        ttk.Button(offline, text="烧录离线画面…", command=self.burn_offline).pack(side="left")
        ttk.Button(offline, text="离线动画…", command=self.open_animation_editor).pack(side="left", padx=8)
        ttk.Button(offline, text="屏内素材…", command=self.open_asset_editor).pack(side="left", padx=8)
        ttk.Checkbutton(offline, text='接收外部通知', variable=self.api_enabled,
                        command=self.toggle_notification_api).pack(side='right')
        ttk.Label(body, text="离线画面 / 动画会替换原待机动画；素材写入前自动备份原图。",
                  wraplength=650).pack(anchor='w', pady=6)
        for variable in (self.title, self.subtitle):
            variable.trace_add("write", lambda *args: self.content_changed())
        self.refresh()
        self.update_preview()
        root.after(10, self.poll)
        root.after(20, self.tick)

    def refresh(self):
        ports = [item.device for item in find_ports()]
        self.ports["values"] = ports
        if self.port.get() not in ports:
            self.port.set(ports[0] if ports else "")

    def image(self):
        mode = self.mode.get()
        if mode in DYNAMIC_MODES:
            cached = self.frame_cache.get((mode, self.size))
            if cached is not None:
                return cached.copy()
            return render(self.size, "文字", mode, "正在获取画面…",
                          self.foreground, self.background, "")
        return render(self.size, mode, self.title.get(), self.subtitle.get(),
                      self.foreground, self.background, self.image_path,
                      clock_background=self.clock_background,picture=self.edited_image)

    def orient_for_screen(self, image, upside_down=None):
        if upside_down is None:
            upside_down = self.upside_down.get()
        if upside_down:
            return image.transpose(Image.Transpose.ROTATE_180)
        return image

    def update_preview(self, image=None):
        try:
            if image is None:
                image = (self.notification_frame if self.active_notification is not None
                         and self.notification_frame is not None else self.image())
            available_height = max(80, min(320, self.root.winfo_screenheight() - 450))
            scale = max(1, min(640 // image.width, available_height // image.height))
            preview = image.resize((image.width * scale, image.height * scale),
                                   Image.Resampling.NEAREST)
            self.photo = ImageTk.PhotoImage(preview)
            self.preview.configure(image=self.photo)
            return image
        except (OSError, ValueError) as exc:
            self.status.set(str(exc))
            return None

    def run(self, operation, done, failed=None):
        if self.busy or self.closing or self.close_requested or self.modal:
            return
        self.busy = True

        def worker():
            try:
                self.results.put((done, operation(), None, failed))
            except Exception as exc:
                self.results.put((done, None, exc, failed))
        self.executor.submit(worker)
        return True

    def poll(self):
        self.notification_inbox.set_connected(self.display is not None and not self.close_requested)
        while True:
            try:
                self.status.set(self.progress_updates.get_nowait())
            except Empty:
                break
        try:
            done, value, error, failed = self.results.get_nowait()
            self.busy = False
            if error:
                logging.getLogger('msu2').warning('Operation failed: %s', error)
                self.status.set(f"操作失败：{error}")
                self.pending_frame = False
                if isinstance(error, ContentError):
                    self.live.set(False)
                else:
                    if self.display:
                        self.display.close()
                    self.display = None
                    self.button_detector = None
                    self.connection.configure(text="连接屏幕")
                if failed:
                    failed(error)
            else:
                recovered = self.sync_recovery()
                done(value)
                if recovered:
                    self.status.set('设备会话已自动恢复，正在重新发送当前画面…')
        except Empty:
            pass
        if not self.closing:
            self.root.after(10, self.poll)

    def sync_recovery(self):
        count = getattr(self.display, 'recovery_count', 0)
        if not self.display or count == self.seen_recovery_count:
            return False
        self.seen_recovery_count = count
        # Do not turn a held button's pre-reset state into a phantom release.
        if self.button_detector is not None:
            self.button_detector = ButtonDebouncer(self.button_detector.baseline)
        self.next_button_at = time.monotonic() + .03
        # A heartbeat can recover an otherwise static screen. Restore its image
        # as well, rather than leaving the firmware's idle animation on screen.
        self.pending_frame = True
        return True

    def toggle_connection(self):
        if self.busy or self.close_requested:
            return
        if self.display:
            self.display.close()
            self.display = None
            self.button_detector = None
            self.pending_frame = False
            self.generation += 1
            self.connection.configure(text="连接屏幕")
            self.status.set("已断开连接")
            return
        port = self.port.get()
        if not port:
            if sys.platform == "darwin":
                self.status.set("未发现小屏串口：请解锁 Mac、允许 USB 配件连接，检查数据线后刷新；可运行 Diagnose.command。")
            else:
                self.status.set("未发现匹配的小屏 USB 串口，请插入设备后刷新")
            return
        device = Display(port)
        self.status.set(f"正在连接 {port}…")

        def connect_device():
            try:
                info = device.connect()
                samples = []
                for _ in range(5):
                    samples.append(device.read_adc(9))
                    time.sleep(0.01)
                return info, median(samples)
            except Exception:
                device.close()
                raise

        def connected(result):
            info, baseline = result
            self.display = device
            self.size = (info.width, info.height)
            self.button_detector = ButtonDebouncer(baseline)
            self.seen_recovery_count = getattr(device, 'recovery_count', 0)
            self.next_button_at = 0
            self.generation += 1
            self.pending_frame = True
            self.status.set(f"已连接 {info.port} · {info.width} × {info.height} · 按键已启用")
            self.connection.configure(text="断开连接")
            self.update_preview()
        self.run(connect_device, connected)

    def content_changed(self):
        self.generation += 1
        self.update_preview()
        if self.mode.get() in DYNAMIC_MODES:
            self.pending_frame = True
            self.next_frame_at = 0

    def mode_changed(self):
        self.dismiss_notification()
        self.generation += 1
        self.next_frame_at = 0
        self.pending_frame = bool(self.display) or self.mode.get() in DYNAMIC_MODES
        if self.mode.get() == "投屏":
            self.screen_options.grid()
        else:
            self.screen_options.grid_remove()
        self.update_preview()

    def next_mode(self):
        modes = [mode for mode in MODES if mode != "图片" or self.image_path]
        current = self.mode.get()
        index = modes.index(current) if current in modes else -1
        self.mode.set(modes[(index + 1) % len(modes)])
        self.mode_changed()
        self.status.set(f"实体按键切换：{self.mode.get()}")

    def button_polled(self, sample):
        value, sampled_at = sample
        self.next_button_at = sampled_at + 0.03
        if self.button_detector and self.button_detector.update(value, sampled_at):
            self.next_mode()

    def frame_request(self):
        return {"size": self.size, "mode": self.mode.get(),
                "title": self.title.get(), "subtitle": self.subtitle.get(),
                "foreground": self.foreground, "background": self.background,
                "image_path": self.image_path, "scope": self.screen_scope.get(),
                'clock_background': self.clock_background.copy() if self.clock_background is not None else None,
                'picture': self.edited_image.copy() if self.edited_image is not None else None,
                "fit": self.screen_fit.get(), "fps": int(self.screen_fps.get()),
                "upside_down": self.upside_down.get(), "generation": self.generation}

    def render_request(self, request):
        try:
            if request["mode"] == "投屏":
                return capture_desktop(request["size"], request["scope"],
                                       request["fit"], request["background"])
            metrics = None
            if request["mode"] == "电脑状态":
                metrics = self.monitor.sample()
            elif request['mode'] == '功率与电池':
                metrics = self.power_monitor.snapshot()
            return render(request["size"], request["mode"], request["title"],
                          request["subtitle"], request["foreground"],
                          request["background"], request["image_path"], metrics,
                          request['clock_background'],request['picture'])
        except ContentError:
            raise
        except Exception as exc:
            raise ContentError(f"内容生成失败：{exc}") from exc

    def request_frame(self, send_to_device):
        if self.busy or self.closing or self.close_requested or self.modal:
            return
        request = self.frame_request()
        device = self.display if send_to_device else None
        self.pending_frame = False
        interval = 1 / request["fps"] if request["mode"] == "投屏" else 1.0
        self.next_frame_at = time.monotonic() + interval

        def operation():
            frame = self.render_request(request)
            # A mode change while capturing makes that captured frame obsolete.
            if request["generation"] != self.generation or self.close_requested:
                return None
            if device:
                device.live_operation(lambda: device.show(
                    self.orient_for_screen(frame, request["upside_down"])))
            return frame

        def completed(frame):
            if frame is None or request["generation"] != self.generation:
                return
            if request["mode"] in DYNAMIC_MODES:
                self.frame_cache[(request["mode"], request["size"])] = frame.copy()
            self.update_preview(frame)
            if device:
                if request["mode"] in DYNAMIC_MODES and self.live.get():
                    self.status.set(f"{request['mode']}实时更新中 · {self.size[0]} × {self.size[1]}")
                else:
                    self.status.set(f"画面已发送并确认通信 · {self.size[0]} × {self.size[1]}")

        self.run(operation, completed)

    def send(self):
        self.dismiss_notification()
        if not self.display:
            self.status.set("请先连接屏幕")
            return
        if self.busy:
            self.pending_frame = True
            return
        self.request_frame(send_to_device=True)

    def tick(self):
        if not self.closing and not self.close_requested and not self.modal and not self.busy:
            now = time.monotonic()
            editor_open = (self.animation_editor is not None or self.asset_editor is not None
                           or self.image_editor is not None)
            if (self.display and now - getattr(self.display, 'last_activity', now) >= 2):
                device = self.display
                self.run(lambda: device.live_operation(device.ping), lambda _: None)
            elif editor_open:
                pass
            elif (self.display and self.hardware_button.get()
                    and self.button_detector is not None and now >= self.next_button_at):
                device = self.display
                self.next_button_at = now + 0.03
                self.run(lambda: (device.live_operation(lambda: device.read_adc(9)), time.monotonic()), self.button_polled)
            elif self.advance_notifications(now):
                pass
            elif self.pending_frame:
                self.request_frame(send_to_device=bool(self.display))
            elif (self.mode.get() in DYNAMIC_MODES and self.live.get()
                  and now >= self.next_frame_at):
                self.request_frame(send_to_device=bool(self.display))
        if not self.closing:
            self.root.after(20, self.tick)

    def open_power_window(self):
        if self.close_requested:
            return
        if self.power_window is not None:
            self.power_window.window.lift()
            return
        from power_ui import PowerWindow
        self.power_window = PowerWindow(self)

    def open_animation_editor(self):
        if self.busy or self.close_requested:
            return
        if self.animation_editor is not None:
            self.animation_editor.window.lift()
            return
        self.dismiss_notification()
        from animation_ui import AnimationEditor
        self.animation_editor = AnimationEditor(self)

    def open_asset_editor(self):
        if self.busy or self.close_requested:
            return
        if self.asset_editor is not None:
            self.asset_editor.window.lift()
            return
        self.dismiss_notification()
        from assets_ui import AssetEditor
        self.asset_editor = AssetEditor(self)

    def burn_offline(self):
        if self.busy or self.close_requested:
            return
        self.dismiss_notification()
        if not self.display:
            self.status.set("请先连接屏幕")
            return
        if (self.mode.get() in DYNAMIC_MODES
                and (self.mode.get(), self.size) not in self.frame_cache):
            self.pending_frame = True
            self.status.set("请等首帧画面显示后再烧录")
            return
        image = self.update_preview()
        if image is None:
            return
        image = self.orient_for_screen(image)
        self.modal = True
        try:
            confirmed = messagebox.askyesno(
                "烧录离线画面",
                "将当前画面按小屏方向保存，覆盖原来的 36 帧待机动画。\n"
                "时钟、电脑状态和投屏只保存当前一帧，离线不会实时更新。\n\n"
                "会先备份原动画，再烧录并完整回读校验，可能需要较长时间。\n烧录期间请勿拔线。是否继续？",
                parent=self.root)
        finally:
            self.modal = False
        if not confirmed or self.close_requested or self.display is None:
            return
        device = self.display
        self.pending_frame = False
        self.status.set("准备烧录离线画面，请勿拔线…")

        def completed(result):
            self.next_frame_at = time.monotonic() + 2
            self.status.set("离线画面已烧录并完整校验，原动画备份已保存。请接电源测试。")

        self.run(lambda: safe_burn(device, (image,) * 36, self.backup_directory,
            progress=self.progress_updates.put, cancelled=lambda: self.close_requested), completed)

    def toggle_notification_api(self):
        if self.api_enabled.get():
            if self.notification_service is not None:
                return
            try:
                from notification_api import NotificationServer
                self.notification_service = NotificationServer(self.notification_inbox).start()
                self.notification_inbox.set_connected(self.display is not None)
                self.status.set('外部通知已启用，等待本机应用发送消息。')
            except OSError as exc:
                self.api_enabled.set(False)
                self.status.set(f'无法启用外部通知：{exc}')
        else:
            if self.notification_service is not None:
                self.notification_service.stop()
                self.notification_service = None
            self.notification_inbox.cancel_pending()
            self.dismiss_notification()
            self.status.set('外部通知已关闭。')

    def dismiss_notification(self, status='dismissed'):
        if self.active_notification is None:
            return
        identifier, _ = self.active_notification
        self.notification_inbox.mark(identifier, status)
        self.active_notification = None
        self.notification_until = None
        self.notification_frame = None
        self.pending_frame = bool(self.display)
        self.update_preview()

    def advance_notifications(self, now):
        if self.active_notification is not None and not self.display:
            self.dismiss_notification('failed')
        if self.active_notification is not None and self.notification_until is not None and now >= self.notification_until:
            self.dismiss_notification('completed')
        if self.active_notification is None and self.display:
            self.active_notification = self.notification_inbox.take()
        if self.active_notification is None:
            return False
        if self.notification_until is None or self.pending_frame:
            self.request_notification_frame()
        return True

    def request_notification_frame(self):
        identifier, notification = self.active_notification
        device, size = self.display, self.size
        upside_down = self.upside_down.get()
        was_live = self.live.get()
        self.pending_frame = False
        def operation():
            try:
                frame = render_notification(size, notification)
            except Exception as exc:
                raise ContentError(f'通知画面生成失败：{exc}') from exc
            device.live_operation(lambda: device.show(self.orient_for_screen(frame, upside_down)))
            return frame
        def completed(frame):
            if self.active_notification is None or self.active_notification[0] != identifier:
                return
            self.notification_frame = frame
            if self.notification_until is None:
                # Dwell time starts after the device acknowledged the frame.
                self.notification_until = time.monotonic()+notification.duration
            self.notification_inbox.mark(identifier, 'showing')
            self.update_preview(frame)
            self.status.set(f'通知：{notification.source} · {notification.title}')
        def failed(error):
            self.dismiss_notification('failed')
            if isinstance(error, ContentError):
                self.live.set(was_live)
        self.run(operation, completed, failed)

    def choose_image(self):
        path = filedialog.askopenfilename(filetypes=[
            ("图片", "*.png *.jpg *.jpeg *.bmp *.gif *.webp"), ("全部文件", "*.*")])
        if path:
            self.edit_picture(path)

    def edit_picture(self,path,clock=False,reuse=False):
        if self.close_requested:
            return
        if self.image_editor is not None:
            self.image_editor.window.lift()
            return
        from image_editor import ImageEditor
        from image_viewport import load_source
        model=(self.clock_framing if clock else self.image_framing) if reuse else None
        try:
            source=model.original if model is not None else load_source(path,self.background)
        except (OSError,ValueError) as exc:
            self.status.set(f'无法读取图片：{exc}')
            return
        def applied(image,framing):
            if clock:
                self.clock_background=image
                self.clock_framing=framing
                self.frame_cache.pop(('时钟',self.size),None)
                self.mode.set('时钟')
            else:
                self.image_path=path
                self.edited_image=image
                self.image_framing=framing
                self.mode.set('图片')
            self.mode_changed()
        self.image_editor=ImageEditor(self.root,source,self.size,applied,self.background,
                                     title='时钟底图取景' if clock else '图片取景',
                                     model=model,on_close=lambda:setattr(self,'image_editor',None))

    def adjust_image(self):
        if not self.image_path:
            return self.choose_image()
        self.edit_picture(self.image_path,reuse=True)

    def adjust_clock_background(self):
        if self.clock_background is None:
            return self.choose_clock_background()
        if self.clock_framing is None:
            from image_viewport import ImageViewport
            self.clock_framing=ImageViewport(self.clock_background,self.size,self.background)
        self.edit_picture('',clock=True,reuse=True)

    def choose_clock_background(self):
        path = filedialog.askopenfilename(filetypes=[('图片', '*.png *.jpg *.jpeg *.bmp *.webp'), ('全部文件', '*.*')])
        if path:
            self.edit_picture(path,clock=True)

    def clear_clock_background(self):
        self.clock_background = None
        self.clock_framing = None
        self.frame_cache.pop(('时钟', self.size), None)
        self.content_changed()

    def choose_color(self, attribute):
        color = colorchooser.askcolor(getattr(self, attribute))[1]
        if color:
            setattr(self, attribute, color)
            self.content_changed()

    def close(self):
        if self.closing:
            return
        self.close_requested = True
        if self.busy:
            self.status.set("等待当前操作完成后退出…")
            self.root.after(100, self.close)
            return
        self.closing = True
        if self.notification_service is not None:
            self.notification_service.stop()
            self.notification_service = None
        self.notification_inbox.cancel_pending()
        self.dismiss_notification()
        if self.image_editor is not None:
            self.image_editor.close()
        if self.power_window is not None:
            self.power_window.close()
        self.power_monitor.close()
        if self.animation_editor is not None:
            self.animation_editor.close(force=True)
        if self.asset_editor is not None:
            self.asset_editor.close(force=True)
        for timer in self.root.tk.call("after", "info"):
            self.root.after_cancel(timer)
        if self.display:
            self.display.close()
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
