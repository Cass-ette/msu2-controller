"""Image slots, backups, conversions and existing flash graphics in one editor."""
from pathlib import Path
from threading import Event
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from asset_layout import SLOTS
from assets import (AssetBackup, capture_backup, load_backup, load_image_or_bin,
                    oriented_bytes, replace_asset, save_backup, upright_image)
from features import ContentError

from app_paths import data_directory

BACKUP_DIRECTORY = data_directory() / 'backups'


class AssetEditor:
    def __init__(self, app):
        self.app = app
        self.working = False
        self.closed = False
        self.cancel_event = Event()
        self.backup = None
        self.image = None
        self.image_editor = None
        self.framing = None
        self.preview_kind = 'image'
        self.window = tk.Toplevel(app.root)
        self.window.title('MSU2 屏内素材')
        self.window.transient(app.root)
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.slot = tk.StringVar(value=SLOTS[0].label)
        self.source_inverted = tk.BooleanVar(value=False)
        self.info = tk.StringVar(value='读取屏内图片，或导入图片 / 160×80 RGB565 BIN。')
        self.graphic_kind = tk.StringVar(value='ASCII 字库')
        self.text = tk.StringVar(value='12:34')
        self.page = tk.StringVar(value='3651')
        self.mono_width = tk.StringVar(value='160')
        self.mono_height = tk.StringVar(value='40')
        self.background = tk.StringVar(value='纯色背景')
        body = ttk.Frame(self.window, padding=16)
        body.pack(fill='both', expand=True)
        tabs = ttk.Notebook(body)
        tabs.pack(fill='x')
        images, graphics = ttk.Frame(tabs, padding=10), ttk.Frame(tabs, padding=10)
        tabs.add(images, text='图片素材与备份')
        tabs.add(graphics, text='已有字库与单色图')
        self.controls = []
        self.selector = ttk.Combobox(images, textvariable=self.slot, values=[s.label for s in SLOTS], state='readonly', width=28)
        self.selector.grid(row=0, column=0, columnspan=2, sticky='w')
        self.controls.append(self.selector)
        self.selector.bind('<<ComboboxSelected>>', self.slot_changed)
        self.add_button(images, '读取屏内图片', self.read, 0, 2)
        self.add_button(images, '导入图片 / BIN', self.import_image, 1, 0)
        self.add_button(images, '打开备份', self.import_backup, 1, 1)
        self.add_button(images, '导出备份', self.export_backup, 1, 2)
        self.add_button(images, '导出正向 PNG', self.export_png, 2, 0)
        self.add_button(images, '导出小屏方向 BIN', self.export_bin, 2, 1)
        self.add_button(images, '用作时钟背景', self.use_clock_background, 2, 2)
        self.add_button(images, '上传当前图片…', self.upload, 3, 0)
        self.add_button(images, '恢复当前备份…', self.restore, 3, 1)
        self.add_button(images, '原图倒转写回…', self.flip_original, 3, 2)
        direction = ttk.Checkbutton(images, text='屏内原图 / 导入 BIN 已倒转（用于还原正向预览）',
                                    variable=self.source_inverted, command=self.reinterpret)
        direction.grid(row=4, column=0, columnspan=3, sticky='w', pady=8)
        self.controls.append(direction)
        self.add_button(images,'拖动 / 缩放 / 旋转…',self.adjust_image,5,0)
        for row, label, var, values in (
                (0, '内容', self.graphic_kind, ('ASCII 字库', '单色底图')),
                (1, '背景', self.background, ('纯色背景',) + tuple(s.label for s in SLOTS))):
            ttk.Label(graphics, text=label).grid(row=row, column=0, sticky='w')
            combo = ttk.Combobox(graphics, textvariable=var, values=values, state='readonly', width=28)
            combo.grid(row=row, column=1, columnspan=2, sticky='w', pady=4)
            self.controls.append(combo)
            if row == 0:
                combo.bind('<<ComboboxSelected>>', self.graphic_changed)
        for row, label, var in ((2, 'ASCII 文字（最多 5 字符）', self.text),
                                (3, '字库起始页 / 单色图片页', self.page),
                                (4, '单色图片宽度（8 的倍数）', self.mono_width),
                                (5, '单色图片高度', self.mono_height)):
            ttk.Label(graphics, text=label).grid(row=row, column=0, sticky='w')
            entry = ttk.Entry(graphics, textvariable=var, width=28)
            entry.grid(row=row, column=1, columnspan=2, sticky='w', pady=4)
            self.controls.append(entry)
        self.add_button(graphics, '读取并预览叠加', self.read_graphic, 6, 1)
        ttk.Label(graphics, text='使用屏内已有素材；前景色沿用主窗口设置。\n单色图默认页 3600，实际尺寸请按素材设置。', wraplength=540).grid(row=7, column=0, columnspan=3, pady=6)
        self.preview = ttk.Label(body)
        self.preview.pack(pady=12)
        self.set_image(Image.new('RGB', (160, 80), app.background))
        self.image = None
        ttk.Label(body, text='预览保持正向；显示和上传使用主窗口的“小屏上下倒转”设置。', wraplength=600).pack(anchor='w')
        ttk.Label(body, textvariable=self.info, wraplength=600).pack(anchor='w', pady=6)
        bottom = ttk.Frame(body)
        bottom.pack(fill='x')
        self.add_button(bottom, '显示到小屏', self.show, 0, 0)
        self.cancel_button = ttk.Button(bottom, text='取消读取', command=self.cancel_event.set)
        self.cancel_button.grid(row=0, column=1, padx=8)
        self.cancel_button.state(['disabled'])
        ttk.Label(body, textvariable=app.status, wraplength=600).pack(anchor='w', pady=8)
        self.window.grab_set()

    def add_button(self, parent, label, command, row, column):
        button = ttk.Button(parent, text=label, command=command)
        button.grid(row=row, column=column, padx=4, pady=5, sticky='w')
        self.controls.append(button)
        return button

    def key(self):
        return next(slot.key for slot in SLOTS if slot.label == self.slot.get())

    def set_image(self, image, kind='image', raw_image=None, framing=None):
        self.image = image.copy()
        self.framing = framing
        self.raw_image = raw_image
        self.preview_kind = kind
        self.photo = ImageTk.PhotoImage(image.resize((480, 240), Image.Resampling.NEAREST))
        self.preview.configure(image=self.photo)

    def slot_changed(self, event=None):
        self.backup = None
        self.image = None
        self.info.set('已切换素材槽，请重新读取或导入图片。')
        self.set_image(Image.new('RGB', (160, 80), self.app.background))
        self.image = None

    def reinterpret(self):
        if self.backup is not None:
            self.backup = AssetBackup(self.backup.slot, self.backup.data, self.source_inverted.get())
            self.set_image(upright_image(self.backup))
        elif self.raw_image is not None:
            raw = self.raw_image
            self.set_image(raw.transpose(Image.Transpose.ROTATE_180)
                           if self.source_inverted.get() else raw, raw_image=raw)

    def cancelled(self):
        return self.cancel_event.is_set() or self.app.close_requested

    def perform(self, operation, done, reading=False):
        if self.working or self.closed or self.app.busy or self.app.close_requested:
            return False
        self.working = True
        self.cancel_event.clear()
        for control in self.controls:
            control.state(['disabled'])
        self.cancel_button.state(['!disabled'] if reading else ['disabled'])

        def finished(result=None, error=None):
            self.working = False
            if self.closed:
                return
            for control in self.controls:
                control.state(['!disabled'])
            self.cancel_button.state(['disabled'])
            if error is None:
                done(result)

        started = self.app.run(operation, finished, lambda error: finished(error=error))
        if not started:
            finished(error=ContentError('正在执行其他操作'))
        return bool(started)

    def device(self):
        if self.app.display is None:
            self.app.status.set('请先在主窗口连接屏幕。')
        return self.app.display

    def read(self):
        device = self.device()
        if device is None:
            return
        key, inverted = self.key(), self.source_inverted.get()
        def done(backup):
            self.backup = backup
            self.set_image(upright_image(backup))
            self.info.set('屏内原图已读取，可导出备份或 PNG / BIN。')
            self.app.status.set('素材读取完成。')
        self.perform(lambda: capture_backup(device, key, inverted, self.app.progress_updates.put,
                                            self.cancelled), done, reading=True)

    def local(self, operation):
        try:
            operation()
        except (OSError, ValueError) as exc:
            self.app.status.set(str(exc))

    def import_image(self):
        path = filedialog.askopenfilename(parent=self.window, filetypes=[('图片 / RGB565 BIN', '*.png *.jpg *.jpeg *.bmp *.webp *.bin'), ('全部文件', '*.*')])
        if path:
            def load():
                if Path(path).suffix.lower() == '.bin':
                    image = load_image_or_bin(path, self.source_inverted.get())
                    self.backup = None
                    raw = (image.transpose(Image.Transpose.ROTATE_180)
                           if self.source_inverted.get() else image.copy())
                    self.set_image(image, raw_image=raw)
                    self.info.set(Path(path).name)
                else:
                    from image_viewport import load_source
                    self.edit_source(load_source(path,self.app.background),Path(path).name,new_import=True)
            self.local(load)

    def edit_source(self,source,label='当前图片',new_import=False):
        if self.working or self.closed or self.app.close_requested:
            return
        if self.image_editor is not None:
            self.image_editor.window.lift()
            return
        from image_editor import ImageEditor
        def applied(image,framing):
            if new_import:
                self.backup=None
                self.source_inverted.set(False)
            self.set_image(image,framing=framing)
            self.info.set(label+' · 已应用取景，上传前仍会先备份屏内原图。')
        self.image_editor=ImageEditor(self.window,source,(160,80),applied,self.app.background,
                                     model=None if new_import else self.framing,
                                     on_close=lambda:setattr(self,'image_editor',None))

    def adjust_image(self):
        if self.image is not None:
            self.edit_source(self.framing.original if self.framing else self.image)

    def import_backup(self):
        path = filedialog.askopenfilename(parent=self.window, filetypes=[('MSU2 素材备份', '*.msu2asset')])
        if path:
            def load():
                backup = load_backup(path)
                self.slot.set(next(s.label for s in SLOTS if s.key == backup.slot))
                self.backup = backup
                self.source_inverted.set(backup.upside_down)
                self.set_image(upright_image(backup))
                self.info.set('备份已校验；恢复会写回备份原始字节。')
            self.local(load)

    def export_backup(self):
        if self.backup is None:
            self.app.status.set('请先读取屏内原图或打开素材备份。')
            return
        path = filedialog.asksaveasfilename(parent=self.window, defaultextension='.msu2asset')
        if path:
            self.local(lambda: save_backup(path, self.backup))

    def export_png(self):
        if self.image is None:
            return
        path = filedialog.asksaveasfilename(parent=self.window, defaultextension='.png')
        if path:
            self.local(lambda: self.image.save(path, format='PNG'))

    def export_bin(self):
        if self.image is None:
            return
        data = oriented_bytes(self.image, self.app.upside_down.get())
        path = filedialog.asksaveasfilename(parent=self.window, defaultextension='.bin')
        if path:
            self.local(lambda: Path(path).write_bytes(data))

    def use_clock_background(self):
        if self.image is None:
            return
        self.app.clock_background = self.image.copy()
        self.app.clock_framing = self.framing
        self.app.mode.set('时钟')
        self.app.frame_cache.pop(('时钟', self.app.size), None)
        self.app.mode_changed()
        self.app.status.set('已用作时钟背景，关闭素材窗口后显示。')

    def confirm_write(self, text):
        self.app.modal = True
        try:
            return messagebox.askyesno('写入屏内素材', text + '\n\n会先保存原图备份，再写入并完整回读校验。期间请勿拔线。', parent=self.window)
        finally:
            self.app.modal = False

    def write(self, data, new_inverted, text):
        device = self.device()
        if device is None or self.working or self.app.busy:
            return
        key, original_inverted = self.key(), self.source_inverted.get()
        if not self.confirm_write(text) or self.app.display is not device or self.app.close_requested:
            return
        def done(result):
            self.backup = AssetBackup(key, data, new_inverted)
            self.source_inverted.set(new_inverted)
            self.set_image(upright_image(self.backup))
            self.info.set('完整校验通过；原图备份：' + result['backup_path'])
            self.app.status.set('素材写入完成，25600 字节完整回读校验通过。')
        self.perform(lambda: replace_asset(device, key, data, BACKUP_DIRECTORY, original_inverted,
                                           self.app.progress_updates.put,
                                           cancelled=lambda: self.app.close_requested), done)

    def upload(self):
        if self.image is not None:
            inverted = self.app.upside_down.get()
            self.write(oriented_bytes(self.image, inverted), inverted, '上传当前预览，替换所选素材槽。')

    def restore(self):
        if self.backup is None or self.backup.slot != self.key():
            self.app.status.set('请打开与所选素材槽匹配的备份。')
            return
        self.write(self.backup.data, self.backup.upside_down, '按原始字节恢复当前备份。')

    def flip_original(self):
        device = self.device()
        if device is None or self.working or self.app.busy:
            return
        key, inverted = self.key(), self.source_inverted.get()
        if not self.confirm_write('读取屏内原图并旋转 180° 写回。') or self.app.display is not device:
            return
        def operation():
            backup = capture_backup(device, key, inverted, self.app.progress_updates.put,
                                    cancelled=lambda: self.app.close_requested)
            # Rotate stored 16-bit pixels without decoding/re-encoding colors.
            pixels = [backup.data[i:i + 2] for i in range(0, len(backup.data), 2)]
            data = b''.join(reversed(pixels))
            result = replace_asset(device, key, data, BACKUP_DIRECTORY, inverted,
                                   self.app.progress_updates.put,
                                   cancelled=lambda: self.app.close_requested)
            return data, result
        def done(result):
            data, details = result
            self.backup = AssetBackup(key, data, not inverted)
            self.source_inverted.set(not inverted)
            self.set_image(upright_image(self.backup))
            self.info.set('倒转写回并完整校验通过；原图备份：' + details['backup_path'])
        self.perform(operation, done)

    def show(self):
        device = self.device()
        if device is not None and self.image is not None:
            image = self.app.orient_for_screen(self.image, self.app.upside_down.get())
            self.perform(lambda: device.live_operation(lambda: device.show(image)),
                         lambda _: self.app.status.set('素材画面已发送，通信确认通过。'))

    def graphic_changed(self, event=None):
        self.page.set('3651' if self.graphic_kind.get() == 'ASCII 字库' else '3600')

    def read_graphic(self):
        from flash_graphics import read_ascii, read_graphic
        device = self.device()
        if device is None or self.working:
            return
        try:
            page = int(self.page.get())
            size = (int(self.mono_width.get()), int(self.mono_height.get()))
        except ValueError:
            self.app.status.set('页地址和尺寸需要填写整数。')
            return
        kind, text, background = self.graphic_kind.get(), self.text.get(), self.background.get()
        fg, bg = self.app.foreground, self.app.background
        original_inverted = self.source_inverted.get()
        def operation():
            try:
                backdrop = None
                if background != '纯色背景':
                    key = next(s.key for s in SLOTS if s.label == background)
                    backdrop = upright_image(capture_backup(device, key, original_inverted,
                                                self.app.progress_updates.put, self.cancelled))
                kwargs = dict(background=backdrop, base_color=bg, progress=self.app.progress_updates.put,
                              cancelled=self.cancelled)
                if kind == 'ASCII 字库':
                    return read_ascii(device, text, fg, start_page=page, **kwargs)
                return read_graphic(device, page, size, fg, **kwargs)
            except ValueError as exc:
                raise ContentError(str(exc)) from exc
        def done(image):
            self.backup = None
            self.set_image(image, 'graphic')
            self.info.set('使用屏内已有数据完成叠加；预览正向，显示时应用小屏方向。')
        self.perform(operation, done, reading=True)

    def close(self, force=False):
        if self.closed:
            return
        if self.working and not force:
            self.app.status.set('请等待写入完成；读取可先点击“取消读取”。')
            return
        self.closed = True
        if self.image_editor is not None:
            self.image_editor.close()
        self.window.grab_release()
        self.window.destroy()
        self.app.asset_editor = None
