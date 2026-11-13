"""Non-destructive source image framing, independent of Tk and screen rotation."""
from __future__ import annotations

from PIL import Image, ImageOps


def load_source(path, background='#101824'):
    with Image.open(path) as source:
        rgba=ImageOps.exif_transpose(source).convert('RGBA')
    base=Image.new('RGBA',rgba.size,background)
    return Image.alpha_composite(base,rgba).convert('RGB')


class ImageViewport:
    """Pan uses output pixels; zoom is relative to cover, center in source pixels."""
    def __init__(self, source, size=(160,80), background='#101824'):
        if size[0]<1 or size[1]<1:
            raise ValueError('显示尺寸必须为正数')
        rgba=ImageOps.exif_transpose(source).convert('RGBA')
        self.original=Image.alpha_composite(Image.new('RGBA',rgba.size,background),rgba).convert('RGB')
        self.source=self.original
        self.size=size
        self.background=background
        self.quarters=0
        self.reset()

    @property
    def cover(self):
        return max(self.size[0]/self.source.width,self.size[1]/self.source.height)

    @property
    def scale(self):
        return self.cover*self.zoom

    def reset(self, contain=False):
        self.zoom=(min(self.size[0]/self.source.width,self.size[1]/self.source.height)/self.cover
                   if contain else 1.)
        self.center=[self.source.width/2,self.source.height/2]

    def _clamp(self):
        for axis,length in enumerate(self.source.size):
            half=self.size[axis]/self.scale/2
            self.center[axis]=(length/2 if half*2>=length
                               else max(half,min(length-half,self.center[axis])))

    def pan(self, dx, dy):
        self.center[0]-=dx/self.scale
        self.center[1]-=dy/self.scale
        self._clamp()

    def zoom_to(self, zoom, anchor=None):
        x,y=anchor if anchor is not None else (self.size[0]/2,self.size[1]/2)
        old_scale=self.scale
        sx=self.center[0]+(x-self.size[0]/2)/old_scale
        sy=self.center[1]+(y-self.size[1]/2)/old_scale
        self.zoom=max(.02,min(30.,float(zoom)))
        self.center=[sx-(x-self.size[0]/2)/self.scale,sy-(y-self.size[1]/2)/self.scale]
        self._clamp()

    def rotate(self, clockwise=True):
        self.quarters=(self.quarters+(1 if clockwise else -1))%4
        operations={1:Image.Transpose.ROTATE_270,2:Image.Transpose.ROTATE_180,3:Image.Transpose.ROTATE_90}
        self.source=(self.original.transpose(operations[self.quarters])
                     if self.quarters else self.original)
        self.reset()

    def render(self):
        # Transform directly into the small target; never allocate a huge zoomed
        # bitmap or repeatedly resample the previously edited output.
        inverse=1/self.scale
        left=self.center[0]-self.size[0]*inverse/2
        top=self.center[1]-self.size[1]*inverse/2
        return self.source.transform(self.size,Image.Transform.AFFINE,
                                     (inverse,0,left,0,inverse,top),
                                     resample=Image.Resampling.BICUBIC,fillcolor=self.background)
