"""Permission recovery window, opened only after a Mac capture is denied."""
import subprocess
import sys
import tkinter as tk
from tkinter import ttk

from platform_support import open_screen_capture_settings, screen_capture_access


class CapturePermissionWindow:
    def __init__(self, app, resume_live):
        self.app = app
        self.resume_live = resume_live
        self.window = tk.Toplevel(app.root)
        self.window.title('允许屏幕录制')
        self.window.transient(app.root)
        self.window.resizable(False, False)
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        body = ttk.Frame(self.window, padding=22)
        body.pack(fill='both', expand=True)
        target = ('「MSU2 小屏控制器」' if getattr(sys, 'frozen', False)
                  else '启动本程序的终端或 Python')
        text = (f'投屏需要读取屏幕画面。\n\n'
                f'请在系统设置的「屏幕录制」中开启{target}。\n'
                '部分系统将这一项称为「屏幕与系统音频录制」。\n\n'
                '开启后，返回这里点击「已开启，重试」。\n'
                '若系统要求退出并重新打开，请按提示重启本应用。')
        ttk.Label(body, text=text, wraplength=460, justify='left').pack(anchor='w')
        self.status = tk.StringVar(value='')
        ttk.Label(body, textvariable=self.status, wraplength=460).pack(anchor='w', pady=(12, 0))
        buttons = ttk.Frame(body)
        buttons.pack(fill='x', pady=(16, 0))
        ttk.Button(buttons, text='稍后', command=self.close).pack(side='left')
        ttk.Button(buttons, text='打开系统设置', command=self.open_settings).pack(side='left', padx=10)
        retry = ttk.Button(buttons, text='已开启，重试', command=self.retry)
        retry.pack(side='right')
        self.window.bind('<Escape>', lambda _: self.close())
        self.window.bind('<Return>', lambda _: self.retry())
        self.window.lift()
        retry.focus_set()
        # No grab or wait_window: idle keepalive and the serial worker keep running.

    def open_settings(self):
        try:
            if not open_screen_capture_settings():
                raise OSError('当前系统没有此权限页')
            self.status.set('已打开设置；开启权限后返回这里重试。')
        except (OSError, subprocess.SubprocessError):
            self.status.set('无法直接打开设置。请前往系统设置 → 隐私与安全性 → 屏幕录制。')

    def retry(self):
        if screen_capture_access().get('granted') is False:
            self.status.set('尚未检测到权限。请先开启；若已经开启，请退出并重新打开本应用。')
            return
        resume_live = self.resume_live
        self.close()
        self.app.resume_screen_capture(resume_live)

    def close(self):
        self.window.destroy()
        self.app.capture_permission_window = None
