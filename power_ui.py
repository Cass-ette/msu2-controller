"""Read-only desktop power dashboard; does not own the serial port."""
import time
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageTk

from power_graphics import render_dashboard


class PowerWindow:
    def __init__(self, app):
        self.app = app
        self.window = tk.Toplevel(app.root)
        self.window.title('功率与电池 · MSU2')
        self.window.minsize(720,580)
        self.window.geometry('900x710')
        self.window.protocol('WM_DELETE_WINDOW',self.close)
        self.closed = False
        self.timer = None
        self.canvas = tk.Canvas(self.window,background='#101824',highlightthickness=0)
        self.canvas.pack(fill='both',expand=True)
        self.canvas_item = self.canvas.create_image(0,0,anchor='nw')
        row = ttk.Frame(self.window,padding=10)
        row.pack(fill='x')
        ttk.Label(row,text='无需连接小屏；功率来自系统遥测，≈ 表示估算。').pack(side='left')
        ttk.Button(row,text='显示到小屏',command=self.show_on_screen).pack(side='right')
        self.refresh()

    def refresh(self):
        if self.closed:
            return
        from app import font
        sample = self.app.power_monitor.snapshot()
        frame = render_dashboard(sample,self.app.power_monitor.samples(),font,time.monotonic())
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        if width < 100:
            width, height = 900,650
        scale = min(width/900,height/650)
        self.photo = ImageTk.PhotoImage(frame.resize((round(900*scale),round(650*scale)),Image.Resampling.LANCZOS))
        self.canvas.itemconfigure(self.canvas_item,image=self.photo)
        self.canvas.coords(self.canvas_item,max(0,(width-900*scale)/2),max(0,(height-650*scale)/2))
        self.timer = self.window.after(1000,self.refresh)

    def show_on_screen(self):
        self.app.mode.set('功率与电池')
        self.app.live.set(True)
        self.app.mode_changed()
        if self.app.display:
            self.app.send()

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.timer is not None:
            self.window.after_cancel(self.timer)
        self.app.power_window = None
        self.window.destroy()
