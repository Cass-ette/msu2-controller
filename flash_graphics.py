"""Read existing flash graphics, compose upright, then use the shared display path.

The manual specifies one 256-byte page per 32x64 ASCII glyph. This decoder
assumes row-major, MSB-first pixels; confirm that layout against the installed
device assets. No font or monochrome data is written here.
"""
from PIL import Image, ImageColor

ASCII_START_PAGE = 3651


def mono_image(data, size, foreground='white', background='black'):
    width, height = size
    if width <= 0 or height <= 0 or width % 8 or len(data) != width * height // 8:
        raise ValueError('单色数据大小或宽度无效（宽度须为 8 的整数倍）')
    mask = Image.frombytes('1', size, data)
    output = Image.new('RGB', size, background)
    output.paste(ImageColor.getrgb(foreground), (0, 0, width, height), mask)
    return output


def read_graphic(device, page, size, foreground, background=None, position=(0, 0),
                 base_color='black', progress=None, cancelled=None):
    width, height = size
    x, y = position
    if (type(page) is not int or not 0 <= page < 4096 or width % 8
            or not 1 <= width <= 160 or not 1 <= height <= 80
            or x < 0 or y < 0 or x + width > 160 or y + height > 80):
        raise ValueError('单色图片页、尺寸或位置超出范围')
    data = device.read_flash_region(page * 256, width * height // 8, progress, cancelled)
    mask = Image.frombytes('1', size, data)
    output = background.copy() if background is not None else Image.new('RGB', (160, 80), base_color)
    if output.size != (160, 80):
        raise ValueError('背景图片必须为 160 × 80')
    output.paste(ImageColor.getrgb(foreground), (x, y, x + width, y + height), mask)
    return output


def read_ascii(device, text, foreground, background=None, base_color='black',
               start_page=ASCII_START_PAGE, position=(0, 8), progress=None, cancelled=None):
    x, y = position
    if (not text or len(text) > 5 or any(not 32 <= ord(char) <= 126 for char in text)
            or type(start_page) is not int or not 0 <= start_page <= 4096 - 128
            or x < 0 or y < 0 or x + 32 * len(text) > 160 or y + 64 > 80):
        raise ValueError('请输入最多 5 个可显示 ASCII 字符，并检查字库页和位置')
    output = background.copy() if background is not None else Image.new('RGB', (160, 80), base_color)
    if output.size != (160, 80):
        raise ValueError('背景图片必须为 160 × 80')
    glyphs = {}
    for index, char in enumerate(text):
        if char not in glyphs:
            data = device.read_flash_region((start_page + ord(char)) * 256, 256, progress, cancelled)
            glyphs[char] = Image.frombytes('1', (32, 64), data)
        left = x + 32 * index
        output.paste(ImageColor.getrgb(foreground), (left, y, left + 32, y + 64), glyphs[char])
    return output
