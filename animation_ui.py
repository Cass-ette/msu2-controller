"""Offline animation editor. All Tk work stays on the main thread."""
from pathlib import Path
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from animation import (FIT_MODES, FRAME_COUNT, FRAME_MS, FRAME_SIZE, LONG_MODES,
                       LOOP_MS, load_gif, load_sequence, normalize)
from features import ContentError
from offline_backup import safe_burn, backup_frames
from animation import AnimationSource


class AnimationEditor:
    def __init__(self, app):
        self.app = app
        self.closed = False
        self.working = False
        self.source = None
        self.selection = None
        self.frames = ()
        self.index = 0
        self.playing = False
        self.started_at = 0
        self.timer = None
        self.window = tk.Toplevel(app.root)
        self.window.title("MSU2 离线动画")
        self.window.minsize(700, 580)
        self.window.transient(app.root)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.fit = tk.StringVar(value=FIT_MODES[0])
        self.loaded_fit = self.fit.get()
        self.long_mode = tk.StringVar(value=LONG_MODES[0])
        self.position = tk.DoubleVar(value=0)
        self.frame_text = tk.StringVar(value="第 0 / 36 帧")
        self.play_text = tk.StringVar(value="播放")
        self.info = tk.StringVar(value="导入 GIF，或选择按 A0、A1、A2…命名的序列帧。")

        body = ttk.Frame(self.window, padding=18)
        body.pack(fill="both", expand=True)
        imports = ttk.Frame(body)
        imports.pack(fill="x")
        self.locked_controls = []
        for text, command in (("导入 GIF", self.choose_gif),
                              ("导入序列帧", self.choose_sequence),
                              ("打开动画备份", self.choose_backup),
                              ("示例动画", self.load_demo)):
            button = ttk.Button(imports, text=text, command=command)
            button.pack(side="left", padx=(0, 8))
            self.locked_controls.append(button)
        options = ttk.Frame(body)
        options.pack(fill="x", pady=12)
        for label, variable, values, command, width in (
                ("缩放", self.fit, FIT_MODES, self.refit, 10),
                ("超长处理", self.long_mode, LONG_MODES, self.rebuild, 22)):
            ttk.Label(options, text=label).pack(side="left", padx=(0, 6))
            control = ttk.Combobox(options, textvariable=variable, values=values,
                                   state="readonly", width=width)
            control.pack(side="left", padx=(0, 12))
            control.bind("<<ComboboxSelected>>", lambda event, action=command: action())
            self.locked_controls.append(control)

        self.preview = ttk.Label(body, anchor="center")
        self.preview.pack(fill="x", pady=6)
        blank = Image.new("RGB", FRAME_SIZE, app.background)
        self.photo = ImageTk.PhotoImage(blank.resize((480, 240)))
        self.preview.configure(image=self.photo)
        ttk.Label(body, text="预览保持正向；烧录后以 10 帧/秒、3.6 秒循环播放。").pack(pady=5)
        timeline = ttk.Frame(body)
        timeline.pack(fill="x", pady=6)
        ttk.Button(timeline, textvariable=self.play_text, command=self.toggle_play).pack(side="left")
        self.slider = ttk.Scale(timeline, from_=0, to=FRAME_COUNT - 1,
                                variable=self.position, command=self.seek)
        self.slider.pack(side="left", fill="x", expand=True, padx=10)
        ttk.Label(timeline, textvariable=self.frame_text, width=13).pack(side="right")
        ttk.Label(body, textvariable=self.info, wraplength=640).pack(anchor="w", pady=6)
        bottom = ttk.Frame(body)
        bottom.pack(fill="x", pady=8)
        direction = ttk.Checkbutton(bottom, text="小屏上下倒转（预览保持正向）",
                                    variable=app.upside_down)
        direction.pack(side="left")
        self.locked_controls.append(direction)
        self.burn_button = ttk.Button(bottom, text="烧录离线动画…", command=self.burn)
        self.burn_button.pack(side="right")
        self.burn_button.state(["disabled"])
        ttk.Label(body, textvariable=app.status, wraplength=640).pack(anchor="w", pady=5)
        self.window.grab_set()
        app.status.set("可以先预览动画；确认烧录时才会替换屏内的待机动画。")
        self.timer = self.window.after(20, self.tick)

    def set_working(self, value):
        self.working = value
        for control in self.locked_controls:
            control.state(["disabled"] if value else ["!disabled"])
        self.burn_button.state(["!disabled"] if self.frames and not value else ["disabled"])

    def choose_gif(self):
        if self.working or self.closed:
            return
        path = filedialog.askopenfilename(parent=self.window, title="选择 GIF 动画",
                                          filetypes=[("GIF 动画", "*.gif"), ("全部文件", "*.*")])
        if path:
            self.load("gif", (path,))

    def choose_sequence(self):
        if self.working or self.closed:
            return
        paths = filedialog.askopenfilenames(
            parent=self.window, title="选择序列帧（按文件名中的数字排序）",
            filetypes=[("图片序列", "*.png *.jpg *.jpeg *.bmp *.webp"), ("全部文件", "*.*")])
        if paths:
            self.load("sequence", paths)

    def choose_backup(self):
        if self.working or self.closed:
            return
        path = filedialog.askopenfilename(parent=self.window, filetypes=[('离线动画备份', '*.msu2animation')])
        if path:
            inverted = messagebox.askyesno('原动画方向', '这份备份中的小屏画面已倒转吗？\n选择“是”会还原正向预览。', parent=self.window)
            self.load('backup-inverted' if inverted else 'backup', (path,))

    def load_demo(self):
        self.load("gif", (str(Path(__file__).parent / "examples" / "offline-demo.gif"),))

    def load(self, kind, paths):
        if self.closed or self.working or self.app.close_requested:
            return False
        if self.app.busy:
            self.app.status.set("正在完成上一次操作，请稍后再导入。")
            return False
        fit = self.fit.get()
        background = self.app.background
        was_live = self.app.live.get()
        self.set_working(True)
        self.app.status.set("正在读取动画并适配画面…")

        def operation():
            try:
                if kind == "gif":
                    return load_gif(paths[0], fit, background)
                if kind.startswith('backup'):
                    frames = backup_frames(paths[0], kind == 'backup-inverted')
                    return AnimationSource(frames, (FRAME_MS,) * FRAME_COUNT, Path(paths[0]).name)
                return load_sequence(paths, fit, background)
            except Exception as exc:
                raise ContentError(f"动画导入失败：{exc}") from exc

        def completed(source):
            if self.closed:
                return
            self.source = source
            self.selection = (kind, tuple(paths))
            if kind.startswith('backup'):
                # Decode to upright for preview, then restore the stored direction
                # on output so importing a backup does not silently flip it.
                self.app.upside_down.set(kind == 'backup-inverted')
            self.loaded_fit = fit
            self.set_working(False)
            self.rebuild()
            self.app.status.set("动画已就绪；可以逐帧查看，或连接屏幕后烧录。")

        def failed(error):
            if self.closed:
                return
            self.fit.set(self.loaded_fit)
            self.set_working(False)
            # A bad local file should not change the host's live-update setting.
            if isinstance(error, ContentError):
                self.app.live.set(was_live)

        started = self.app.run(operation, completed, failed)
        if not started:
            self.set_working(False)
        return bool(started)

    def refit(self):
        if self.selection and not self.working:
            self.load(*self.selection)

    def rebuild(self):
        if self.closed or self.working or self.source is None:
            return
        self.frames = normalize(self.source, self.long_mode.get())
        duration = self.source.duration_ms
        action = "循环补齐" if duration < LOOP_MS else (
            self.long_mode.get() if duration > LOOP_MS else "原速播放")
        self.info.set(f"{self.source.label}\n源 {len(self.source.frames)} 帧 / {duration / 1000:g} 秒"
                      f" → 36 帧 / 3.6 秒 · {action}")
        self.index = -1
        self.show_frame(0)
        self.started_at = time.monotonic()
        self.playing = True
        self.play_text.set("暂停")
        self.burn_button.state(["!disabled"])

    def show_frame(self, index):
        if not self.frames or self.closed:
            return
        index = max(0, min(FRAME_COUNT - 1, int(index)))
        if index == self.index:
            return
        self.index = index
        image = self.frames[index].resize((480, 240), Image.Resampling.NEAREST)
        self.photo = ImageTk.PhotoImage(image)
        self.preview.configure(image=self.photo)
        self.position.set(index)
        self.frame_text.set(f"第 {index + 1} / 36 帧")

    def seek(self, value):
        self.playing = False
        self.play_text.set("播放")
        self.show_frame(round(float(value)))

    def toggle_play(self):
        if not self.frames:
            return
        self.playing = not self.playing
        self.play_text.set("暂停" if self.playing else "播放")
        if self.playing:
            self.started_at = time.monotonic() - self.index * FRAME_MS / 1000

    def tick(self):
        if self.closed:
            return
        if self.playing and self.frames:
            index = int((time.monotonic() - self.started_at) * 1000 / FRAME_MS) % FRAME_COUNT
            self.show_frame(index)
        self.timer = self.window.after(20, self.tick)

    def burn(self):
        if self.closed or self.working or self.app.busy or self.app.close_requested:
            return
        if not self.frames:
            self.app.status.set("请先导入动画。")
            return
        device = self.app.display
        if device is None:
            self.app.status.set("请先关闭动画窗口，在主界面连接屏幕，再打开动画窗口烧录。")
            return
        info = device.info
        if (info.width, info.height) != FRAME_SIZE or info.protocol != "01":
            self.app.status.set("离线动画烧录目前支持已核对的 160 × 80 小屏。")
            return
        self.app.modal = True
        try:
            confirmed = messagebox.askyesno(
                "烧录离线动画",
                "将预览中的 36 帧动画按小屏方向保存，替换原待机动画。\n"
                "单独供电时以 10 帧/秒、3.6 秒循环播放。\n\n"
                "会先备份原动画，再烧录并完整回读校验，可能需要较长时间。\n烧录期间请勿拔线。是否继续？", parent=self.window)
        finally:
            self.app.modal = False
        if not confirmed or self.app.close_requested or self.app.display is not device:
            return
        frames = tuple(self.app.orient_for_screen(frame) for frame in self.frames)
        self.set_working(True)
        self.app.pending_frame = False
        self.app.status.set("准备烧录离线动画，请勿拔线…")

        def completed(result):
            self.app.next_frame_at = time.monotonic() + 2
            if not self.closed:
                self.set_working(False)
            self.app.status.set("36 帧动画已烧录并完整校验，原动画备份已保存。请接电源测试。")

        def failed(error):
            if not self.closed:
                self.set_working(False)

        self.app.run(lambda: safe_burn(device, frames, self.app.backup_directory,
            progress=self.app.progress_updates.put, cancelled=lambda: self.app.close_requested), completed, failed)

    def close(self, force=False):
        if self.closed:
            return
        if self.working and not force:
            self.app.status.set("请等待当前操作完成后再关闭动画窗口。")
            return
        self.closed = True
        if self.timer:
            self.window.after_cancel(self.timer)
        self.window.grab_release()
        self.window.destroy()
        self.app.animation_editor = None
