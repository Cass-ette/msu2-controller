"""Verified image slots in the bundled MSU2 MINI Demo V1.6 program."""
from dataclasses import dataclass

LAYOUT_ID = "msu2-mini-demo-v1.6-160x80"
IMAGE_SIZE = (160, 80)
PAGE_BYTES = 256
IMAGE_BYTES = 25600


@dataclass(frozen=True)
class AssetSlot:
    key: str
    label: str
    start_page: int
    pages: int = 100


SLOTS = (
    AssetSlot("clock_background", "时钟背景", 3826),
    AssetSlot("album_image", "相册图片（1 张）", 3926),
)


def slot_for(key):
    for slot in SLOTS:
        if key == slot.key:
            return slot
    raise ValueError("未知的屏内素材槽")
