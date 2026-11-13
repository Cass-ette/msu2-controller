"""Build a self-contained Apple silicon app and a compressed drag-install DMG."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
APP_NAME = 'MSU2 小屏控制器.app'


def make_icon():
    assets = ROOT / 'assets'
    assets.mkdir(exist_ok=True)
    # Draw the device as vector-like shapes at 2x, then downsample for each size.
    image = Image.new('RGBA', (2048, 2048))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((80, 80, 1968, 1968), radius=420, fill='#132334')
    draw.rounded_rectangle((256, 448, 1792, 1440), radius=156, fill='#09111d',
                           outline='#335568', width=20)
    draw.rounded_rectangle((336, 528, 1712, 1280), radius=64, fill='#143e43')
    draw.line((432, 912, 640, 912, 752, 720, 944, 1104, 1088, 848, 1304, 848),
              fill='#6ee7b7', width=64, joint='curve')
    draw.ellipse((1408, 688, 1568, 848), fill='#f5c66f')
    draw.rounded_rectangle((864, 1460, 1184, 1540), radius=36, fill='#335568')
    draw.rounded_rectangle((672, 1584, 1376, 1656), radius=36, fill='#6ee7b7')
    icon = image.resize((1024, 1024), Image.Resampling.LANCZOS)
    icon.save(assets / 'MSU2.png')
    icon.save(assets / 'MSU2.icns', format='ICNS')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument('--app-only', action='store_true')
    choice.add_argument('--dmg-only', action='store_true')
    args = parser.parse_args()
    if sys.platform != 'darwin' or platform.machine() != 'arm64':
        parser.error('Build this configuration on an Apple silicon Mac.')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if not args.dmg_only:
        make_icon()
        subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
                        '--distpath', str(output), '--workpath', str(ROOT / 'build' / 'pyinstaller'),
                        str(ROOT / 'MSU2Controller.spec')], cwd=ROOT, check=True)
    bundle = output / APP_NAME
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(bundle)], check=True)
    if args.app_only:
        return
    dmg = output / 'MSU2Controller-Mac-arm64-20261113.dmg'
    with tempfile.TemporaryDirectory(prefix='msu2-dmg-') as temporary:
        stage = Path(temporary)
        shutil.copytree(bundle, stage / APP_NAME, symlinks=True)
        (stage / 'Applications').symlink_to('/Applications', target_is_directory=True)
        (stage / '安装说明.txt').write_text(
            '将 MSU2 小屏控制器拖到 Applications，然后从应用程序打开。\n'
            '内置 Python 和依赖，无需终端、虚拟环境或额外安装。\n'
            'Apple 芯片版本。投屏时请为此应用授权屏幕录制。\n'
            '备份：~/Library/Application Support/MSU2Controller/backups\n'
            '日志：~/Library/Logs/MSU2Controller\n', encoding='utf-8')
        subprocess.run(['/usr/bin/hdiutil', 'create', '-srcfolder', str(stage), '-volname',
                        'MSU2 小屏控制器', '-format', 'UDZO', '-imagekey', 'zlib-level=9',
                        '-ov', str(dmg)], check=True)
    summary = {'app': str(bundle), 'dmg': str(dmg),
               'dmg_bytes': dmg.stat().st_size,
               'dmg_sha256': hashlib.sha256(dmg.read_bytes()).hexdigest(),
               'architecture': 'arm64', 'signature': 'ad-hoc; not Developer ID notarized'}
    (output / 'mac-build.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    dmg.with_suffix('.dmg.sha256').write_text(summary['dmg_sha256'] + '  ' + dmg.name + '\n')
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
