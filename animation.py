"""Import GIFs or numbered images into the device's fixed standby timeline."""
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
import re

from PIL import Image, ImageOps

from standby_format import FRAME_SIZE, FRAME_COUNT, FRAME_MS, LOOP_MS
MAX_SOURCE_FRAMES = 1000
LONG_MODES = ("截取前 3.6 秒", "完整压缩到 3.6 秒")
FIT_MODES = ("完整显示", "居中裁剪")


@dataclass(frozen=True)
class AnimationSource:
    frames: tuple
    durations_ms: tuple
    label: str

    @property
    def duration_ms(self):
        return sum(self.durations_ms)


def fit_frame(image, fit="完整显示", background="#101824"):
    """Flatten transparency onto the chosen background and fit to the display."""
    if fit not in FIT_MODES:
        raise ValueError("未知的动画缩放方式")
    rgba = ImageOps.exif_transpose(image).convert("RGBA")
    if fit == "居中裁剪":
        content = ImageOps.fit(rgba, FRAME_SIZE, method=Image.Resampling.LANCZOS)
    else:
        content = ImageOps.contain(rgba, FRAME_SIZE, method=Image.Resampling.LANCZOS)
    result = Image.new("RGB", FRAME_SIZE, background)
    result.paste(content, ((FRAME_SIZE[0] - content.width) // 2,
                           (FRAME_SIZE[1] - content.height) // 2), content)
    return result


def load_gif(path, fit="完整显示", background="#101824"):
    frames, durations = [], []
    with Image.open(path) as source:
        if source.format != "GIF":
            raise ValueError("请选择 GIF 文件；普通图片请用“导入序列帧”")
        index = 0
        while True:
            try:
                # Sequential seeking lets Pillow apply GIF disposal/compositing.
                source.seek(index)
            except EOFError:
                break
            if index >= MAX_SOURCE_FRAMES:
                raise ValueError(f"动画最多支持 {MAX_SOURCE_FRAMES} 个源帧，请先裁短")
            frames.append(fit_frame(source, fit, background))
            duration = source.info.get("duration", FRAME_MS)
            durations.append(duration if isinstance(duration, int) and duration > 0 else FRAME_MS)
            index += 1
    if not frames:
        raise ValueError("GIF 没有可读取的画面")
    return AnimationSource(tuple(frames), tuple(durations), Path(path).name)


def sequence_key(path):
    name = Path(path).name
    return (tuple(int(part) if part.isdigit() else part.casefold()
                  for part in re.split(r"(\d+)", name)), str(path).casefold())


def load_sequence(paths, fit="完整显示", background="#101824"):
    paths = sorted((Path(path) for path in paths), key=sequence_key)
    if not paths:
        raise ValueError("请至少选择一张序列帧图片")
    if len(paths) > MAX_SOURCE_FRAMES:
        raise ValueError(f"序列最多支持 {MAX_SOURCE_FRAMES} 张图片，请先减少数量")
    frames = []
    for path in paths:
        with Image.open(path) as source:
            frames.append(fit_frame(source, fit, background))
    label = f"{paths[0].name} … {paths[-1].name}（{len(paths)} 张）"
    return AnimationSource(tuple(frames), (FRAME_MS,) * len(frames), label)


def normalize(source, long_mode=LONG_MODES[0]):
    """Sample the source timeline at 10 Hz, looping short inputs."""
    if long_mode not in LONG_MODES:
        raise ValueError("未知的超长动画处理方式")
    if not source.frames or len(source.frames) != len(source.durations_ms):
        raise ValueError("动画画面和时长不完整")
    ends = []
    elapsed = 0
    for frame, duration in zip(source.frames, source.durations_ms):
        if frame.size != FRAME_SIZE or not isinstance(duration, int) or duration <= 0:
            raise ValueError("动画源帧尺寸或时长无效")
        elapsed += duration
        ends.append(elapsed)
    result = []
    for index in range(FRAME_COUNT):
        if elapsed > LOOP_MS and long_mode == LONG_MODES[1]:
            stamp = index * elapsed // FRAME_COUNT
        else:
            stamp = (index * FRAME_MS) % elapsed
        result.append(source.frames[bisect_right(ends, stamp)].copy())
    return tuple(result)
