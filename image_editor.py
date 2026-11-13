"""Interactive crop, pan, zoom and quarter-turn editing; no serial operations."""
import math
import copy
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageTk

from image_viewport import ImageViewport


class ImageEditor:
    def __init__(self, parent, source, size, on_apply, background='#101824',
                 title='图片取景', on_close=None, model=None):
        self.model=copy.copy(model) if model is not None else ImageViewport(source,size,background)
        self.model.center=list(self.model.center)
        self.on_apply,self.on_close=on_apply,on_close
        self.closed=False
        self.previous_grab=parent.grab_current()
        self.window=tk.Toplevel(parent)
        self.window.title(title)
        self.window.transient(parent)
        self.window.protocol('WM_DELETE_WINDOW',self.close)
        body=ttk.Frame(self.window,padding=16)
        body.pack(fill='both',expand=True)
        ttk.Label(body,text='拖动图片调整显示区域；滚轮或滑块缩放；可左右旋转 90°。').pack(anchor='w')
        self.preview_scale=min(640/size[0],320/size[1])
        self.canvas=tk.Canvas(body,width=round(size[0]*self.preview_scale),
                              height=round(size[1]*self.preview_scale),
                              highlightthickness=1,highlightbackground='#6ee7b7',cursor='fleur')
        self.canvas.pack(pady=12)
        self.item=self.canvas.create_image(0,0,anchor='nw')
        self.drag_start=None
        self.canvas.bind('<ButtonPress-1>',self.press)
        self.canvas.bind('<B1-Motion>',self.drag)
        self.canvas.bind('<ButtonRelease-1>',lambda event:setattr(self,'drag_start',None))
        self.canvas.bind('<MouseWheel>',self.wheel)
        self.canvas.bind('<Button-4>',lambda event:self.wheel(event,1.1))
        self.canvas.bind('<Button-5>',lambda event:self.wheel(event,1/1.1))
        self.zoom=tk.DoubleVar()
        self.info=tk.StringVar()
        row=ttk.Frame(body)
        row.pack(fill='x')
        for label,command in (
                ('左转 90°',lambda:self.rotate(False)),('右转 90°',lambda:self.rotate(True)),
                ('完整显示',lambda:self.reset(True)),('铺满取景',lambda:self.reset(False))):
            ttk.Button(row,text=label,command=command).pack(side='left',padx=(0,8))
        scale_row=ttk.Frame(body)
        scale_row.pack(fill='x',pady=10)
        ttk.Label(scale_row,text='缩放').pack(side='left')
        self.slider=ttk.Scale(scale_row,from_=math.log(.02),to=math.log(30),
                              variable=self.zoom,command=self.scale_changed)
        self.slider.pack(side='left',fill='x',expand=True,padx=12)
        ttk.Label(body,textvariable=self.info).pack(anchor='w')
        ttk.Label(body,text='框内即小屏显示内容。此处旋转图片内容；主窗口倒转仅影响设备输出。').pack(anchor='w',pady=8)
        bottom=ttk.Frame(body)
        bottom.pack(fill='x',pady=(6,0))
        ttk.Button(bottom,text='取消',command=self.close).pack(side='right')
        ttk.Button(bottom,text='应用取景',command=self.apply).pack(side='right',padx=10)
        self.refresh()
        self.window.grab_set()

    def refresh(self):
        if self.closed:
            return
        frame=self.model.render()
        self.photo=ImageTk.PhotoImage(frame.resize((round(frame.width*self.preview_scale),
                                                   round(frame.height*self.preview_scale)),Image.Resampling.NEAREST))
        self.canvas.itemconfigure(self.item,image=self.photo)
        self.zoom.set(math.log(self.model.zoom))
        self.info.set(f'原图 {self.model.original.width} × {self.model.original.height}  ·  '
                      f'取景 {frame.width} × {frame.height}  ·  '
                      f'相对铺满 {self.model.zoom*100:.0f}%  ·  右转 {self.model.quarters*90}°')

    def press(self,event):
        self.drag_start=(event.x,event.y)

    def drag(self,event):
        if self.drag_start is None:
            return
        x,y=self.drag_start
        self.model.pan((event.x-x)/self.preview_scale,(event.y-y)/self.preview_scale)
        self.drag_start=(event.x,event.y)
        self.refresh()

    def wheel(self,event,factor=None):
        if factor is None:
            factor=1.1 if event.delta>0 else 1/1.1
            if not event.delta:
                return 'break'
        self.model.zoom_to(self.model.zoom*factor,(event.x/self.preview_scale,event.y/self.preview_scale))
        self.refresh()
        return 'break'

    def scale_changed(self,value):
        self.model.zoom_to(math.exp(float(value)))
        self.refresh()

    def rotate(self,clockwise):
        self.model.rotate(clockwise)
        self.refresh()

    def reset(self,contain):
        self.model.reset(contain)
        self.refresh()

    def apply(self):
        self.on_apply(self.model.render(),self.model)
        self.close()

    def close(self):
        if self.closed:
            return
        self.closed=True
        self.window.grab_release()
        self.window.destroy()
        if self.previous_grab is not None and self.previous_grab.winfo_exists():
            self.previous_grab.grab_set()
        if self.on_close:
            self.on_close()
