"""Back up only the 36-frame region before replacing an offline animation."""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile
import uuid
import zipfile

from PIL import Image
from assets import AssetCancelled, decode_image
from features import ContentError
from standby_format import FRAME_COUNT, FRAME_SIZE

FRAME_BYTES = 25600
REGION_BYTES = FRAME_COUNT * FRAME_BYTES


def save_animation_backup(path, data):
    if len(data) != REGION_BYTES:
        raise ValueError('离线动画备份大小无效')
    path = Path(path)
    manifest = {'format': 'msu2-standby-backup', 'version': 1, 'start_page': 0,
                'pages': 3600, 'sha256': hashlib.sha256(data).hexdigest()}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.msu2-', delete=False) as stream:
            temporary = Path(stream.name)
            with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('manifest.json', json.dumps(manifest))
                archive.writestr('frames.bin', data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return path


def load_animation_backup(path):
    try:
        if Path(path).stat().st_size > REGION_BYTES + 8192:
            raise ValueError('备份文件过大')
        with zipfile.ZipFile(path) as archive:
            if sorted(archive.namelist()) != ['frames.bin', 'manifest.json']:
                raise ValueError('备份内容无效')
            if archive.getinfo('manifest.json').file_size > 4096 or archive.getinfo('frames.bin').file_size != REGION_BYTES:
                raise ValueError('备份数据大小无效')
            manifest = json.loads(archive.read('manifest.json'))
            data = archive.read('frames.bin')
        expected = {'format': 'msu2-standby-backup', 'version': 1, 'start_page': 0, 'pages': 3600,
                    'sha256': hashlib.sha256(data).hexdigest()}
        if manifest != expected or any(type(manifest[k]) is not type(v) for k,v in expected.items()):
            raise ValueError('备份型号或校验不匹配')
        return data
    except (zipfile.BadZipFile, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError('无法读取离线动画备份') from exc


def backup_frames(path, upside_down=False):
    data = load_animation_backup(path)
    frames = tuple(decode_image(data[i:i+FRAME_BYTES]) for i in range(0, len(data), FRAME_BYTES))
    if upside_down:
        return tuple(frame.transpose(Image.Transpose.ROTATE_180) for frame in frames)
    return frames


def safe_burn(device, frames, directory, progress=None, cancelled=None):
    from msu2 import DeviceError, image_bytes
    device.require_asset_device()
    frames = tuple(frames)
    if len(frames) != FRAME_COUNT or any(frame.size != FRAME_SIZE for frame in frames):
        raise ValueError('需要 36 帧 160 × 80 画面')
    expected = b''.join(image_bytes(frame) for frame in frames)
    original = device.read_flash_region(0, REGION_BYTES, progress, cancelled)
    try:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (datetime.now().strftime('%Y%m%d-%H%M%S') + '-standby-' + uuid.uuid4().hex[:8] + '.msu2animation')
        save_animation_backup(path, original)
        if load_animation_backup(path) != original:
            raise ValueError('备份文件内容与原动画不一致')
    except (OSError, ValueError) as exc:
        raise ContentError(f'备份保存失败，未开始烧录：{exc}') from exc
    if progress:
        progress(f'离线动画原数据已备份：{path}；开始烧录…')
    if cancelled is not None and cancelled():
        raise AssetCancelled(f'已取消，原动画未改动；备份：{path}')
    try:
        result = device.burn_offline_frames(frames, progress)
        actual = device.read_flash_region(0, REGION_BYTES, progress)
        if actual != expected:
            raise DeviceError('离线动画完整回读校验失败')
        return {**result, 'backup_path': str(path), 'verified_bytes': len(actual)}
    except Exception as exc:
        raise DeviceError(f'烧录未完成：{exc}；原动画备份：{path}') from exc
