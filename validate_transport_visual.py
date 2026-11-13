"""Compare old pacing and the production live path, without writing flash."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from statistics import median
import time

from PIL import Image, ImageDraw

from app import font
from msu2 import Display, find_ports


class VisualDisplay(Display):
    slow = False

    def _write(self, data):
        if not self.slow or len(data) <= 6:
            return super()._write(data)
        view = memoryview(data)
        while view:
            count = self.serial.write(view[:60])
            if not count:
                raise RuntimeError('zero byte write')
            view = view[count:]
            time.sleep(.001)


def frame(label, index):
    image = Image.new('RGB', (160, 80), '#101824')
    draw = ImageDraw.Draw(image)
    draw.text((4, 1), label, fill='#6ee7b7', font=font(15))
    draw.text((4, 22), f'中文清晰  {index:03d}', fill='white', font=font(12))
    draw.rectangle((0, 47, 159, 79), fill='#183c5c')
    x = (index * 7) % 140
    draw.rectangle((x, 49, x + 19, 77), fill='#eeb85a')
    draw.rectangle((0, 0, 159, 79), outline='#6ee7b7')
    return image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds-per-mode', type=int, default=20)
    args = parser.parse_args()
    if not 5 <= args.seconds_per_mode <= 60:
        parser.error('seconds-per-mode must be 5–60')
    ports = find_ports()
    if len(ports) != 1:
        raise RuntimeError(f'Expected one display; found {len(ports)}')
    report = {'started_utc': datetime.now(timezone.utc).isoformat(),
              'flash_written': False, 'modes': [],
              'pixel_correctness': 'requires user observation'}
    device = VisualDisplay(ports[0].device)
    try:
        device.connect()
        for slow, label in ((True, '旧节奏 60B / 1ms'), (False, '快速传图 60B')):
            device.slow = slow
            started = time.monotonic()
            times = []
            print(label, flush=True)
            while time.monotonic() - started < args.seconds_per_mode:
                image = frame(label, len(times))
                tick = time.monotonic()
                device.show(image)
                device.read_adc(9)
                elapsed = time.monotonic() - tick
                times.append(elapsed)
                time.sleep(max(0, 1 / 8 - elapsed))
            report['modes'].append({'label': label, 'frames': len(times),
                                    'seconds': time.monotonic() - started,
                                    'median_seconds': median(times), 'passed': True})
        device.show(frame('快速传图 · 结束', 0))
        report['production_path_verified'] = True
    finally:
        device.close()
        output = Path(__file__).parent / 'validation' / 'transport-visual.json'
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
