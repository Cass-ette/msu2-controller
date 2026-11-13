"""Image conversion, bounded backup files and backup-before-write operations."""
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import base64
import binascii
import hashlib
import json
import os
import tempfile
import uuid

from PIL import Image

from asset_layout import IMAGE_BYTES, IMAGE_SIZE, LAYOUT_ID, slot_for
from features import ContentError


class AssetCancelled(ContentError):
    pass


@dataclass(frozen=True)
class AssetBackup:
    slot: str
    data: bytes
    upside_down: bool = False

    def __post_init__(self):
        slot_for(self.slot)
        if not isinstance(self.data, bytes) or len(self.data) != IMAGE_BYTES:
            raise ValueError("备份图像数据大小不正确")
        if type(self.upside_down) is not bool:
            raise ValueError("备份方向信息无效")


def decode_image(data):
    if len(data) != IMAGE_BYTES:
        raise ValueError("素材大小不正确")
    output = bytearray()
    for index in range(0, len(data), 2):
        value = (data[index] << 8) | data[index + 1]
        red, green, blue = (value >> 11) & 31, (value >> 5) & 63, value & 31
        output.extend(((red << 3) | (red >> 2), (green << 2) | (green >> 4),
                       (blue << 3) | (blue >> 2)))
    return Image.frombytes("RGB", IMAGE_SIZE, bytes(output))


def upright_image(backup):
    image = decode_image(backup.data)
    return image.transpose(Image.Transpose.ROTATE_180) if backup.upside_down else image


def backup_document(backup):
    slot = slot_for(backup.slot)
    return {
        "format": "msu2-asset-backup", "version": 1, "layout": LAYOUT_ID,
        "slot": slot.key, "start_page": slot.start_page, "pages": slot.pages,
        "width": 160, "height": 80, "encoding": "RGB565-BE",
        "upside_down": backup.upside_down,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "sha256": hashlib.sha256(backup.data).hexdigest(),
        "data": base64.b64encode(backup.data).decode("ascii"),
    }


def save_backup(path, backup):
    """Write to the destination atomically; never leave a partial backup."""
    path = Path(path)
    payload = json.dumps(backup_document(backup), ensure_ascii=False, indent=2).encode("utf-8")
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".msu2-", suffix=".tmp",
                                         delete=False) as stream:
            temp = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if temp is not None and temp.exists():
            temp.unlink()
    return path


def load_backup(path):
    with Path(path).open("rb") as stream:
        payload = stream.read(65537)
    if len(payload) > 65536:
        raise ValueError("备份文件大小不正确")
    try:
        doc = json.loads(payload)
        if not isinstance(doc, dict):
            raise ValueError("备份格式无效")
        slot = slot_for(doc.get("slot"))
        expected = {"format": "msu2-asset-backup", "version": 1, "layout": LAYOUT_ID,
                    "start_page": slot.start_page, "pages": slot.pages,
                    "width": 160, "height": 80, "encoding": "RGB565-BE"}
        if any(type(doc.get(key)) is not type(value) or doc[key] != value
               for key, value in expected.items()):
            raise ValueError("备份型号、地址或编码不匹配")
        data = base64.b64decode(doc["data"], validate=True)
        backup = AssetBackup(slot.key, data, doc["upside_down"])
        if hashlib.sha256(data).hexdigest() != doc.get("sha256"):
            raise ValueError("备份校验失败，文件可能已损坏")
        return backup
    except (KeyError, TypeError, binascii.Error, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("无法读取此素材备份") from exc


def capture_backup(device, key, upside_down=False, progress=None, cancelled=None):
    return AssetBackup(key, device.read_asset(key, progress, cancelled), upside_down)


def replace_asset(device, key, data, backup_directory, upside_down=False, progress=None,
                  cancelled=None):
    """Persist original bytes before any erase; use the same path for restore."""
    slot_for(key)
    if not isinstance(data, bytes) or len(data) != IMAGE_BYTES:
        raise ValueError("待写入素材大小不正确")
    original = capture_backup(device, key, upside_down, progress, cancelled)
    directory = Path(backup_directory)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        name = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + key + "-" + uuid.uuid4().hex[:8]
        path = save_backup(directory / (name + ".msu2asset"), original)
        if load_backup(path) != original:
            raise ValueError('备份文件内容与原图不一致')
    except (OSError, ValueError) as exc:
        raise ContentError(f"无法保存原图备份，未开始写入：{exc}") from exc
    if progress:
        progress(f"原图备份已保存：{path.name}；开始写入…")
    if cancelled is not None and cancelled():
        raise AssetCancelled(f'已取消，屏内素材未改动；原图备份：{path}')
    try:
        result = device.write_asset(key, data, progress)
    except Exception as exc:
        from msu2 import DeviceError
        raise DeviceError(f"写入未完成：{exc}；原图备份：{path}") from exc
    return {"backup_path": str(path), **result}


def load_image_or_bin(path, upside_down=False):
    path = Path(path)
    if path.suffix.lower() == '.bin':
        with path.open('rb') as stream:
            data = stream.read(IMAGE_BYTES + 1)
        result = decode_image(data)
        return result.transpose(Image.Transpose.ROTATE_180) if upside_down else result
    from animation import fit_frame
    with Image.open(path) as source:
        return fit_frame(source, '居中裁剪')


def oriented_bytes(image, upside_down=False):
    from msu2 import image_bytes
    if image.size != IMAGE_SIZE:
        raise ValueError('图片必须为 160 × 80')
    if upside_down:
        image = image.transpose(Image.Transpose.ROTATE_180)
    return image_bytes(image)


def restore_asset(device, selected_key, backup, backup_directory, current_upside_down=False,
                  progress=None):
    if selected_key != backup.slot:
        raise ValueError("备份属于其他素材槽，请选择对应槽位后恢复")
    # Restore exact stored bytes; do not apply a second orientation transform.
    return replace_asset(device, selected_key, backup.data, backup_directory,
                         current_upside_down, progress)
