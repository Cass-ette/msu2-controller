"""Supplemental host call trace using the same synthetic frames.

This is a separate diagnostic run, never part of the original timing results.
Records Display._write, serial.write/read and flush, without adding I/O calls.
No flash commands are invoked. Payload bytes are represented by length/SHA-256.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

from PIL import Image, ImageDraw

from benchmark_refresh import load_source, source_metadata


class CallTrace:
    def __init__(self, display, source):
        self.display = display
        self.source_file = str(Path(source.__file__).resolve())
        self.events = []
        self.parent = None
        self.original_write = display._write
        self.serial_write = display.serial.write
        self.serial_read = display.serial.read
        self.serial_flush = display.serial.flush

    def callers(self):
        result = []
        frame = sys._getframe(2)
        while frame is not None:
            if frame.f_code.co_filename == self.source_file:
                result.append({'function': frame.f_code.co_name, 'line': frame.f_lineno})
            frame = frame.f_back
        return result

    def event(self, kind, **fields):
        value = {'sequence': len(self.events) + 1, 'kind': kind,
                 'callers': self.callers(), **fields}
        self.events.append(value)
        return value

    def write(self, data):
        raw = bytes(data)
        payload = len(raw) > 6
        event = self.event('Display._write', requested_length=len(raw), payload=payload,
                           **({'sha256': hashlib.sha256(raw).hexdigest()} if payload
                              else {'hex': raw.hex(' ')}))
        previous, self.parent = self.parent, event
        try:
            result = self.original_write(data)
            # _write returns None. serial.write below records accepted byte counts.
            event['return_value'] = result
            return result
        finally:
            self.parent = previous

    def serial_write_call(self, data):
        raw = bytes(data)
        payload = bool(self.parent and self.parent['payload'])
        event = self.event('serial.write', requested_length=len(raw),
                           parent_sequence=self.parent['sequence'] if self.parent else None,
                           **({'sha256': hashlib.sha256(raw).hexdigest()} if payload
                              else {'hex': raw.hex(' ')}))
        count = self.serial_write(data)
        event['actual_return_length'] = count
        return count

    def read(self, count):
        event = self.event('serial.read', requested_length=count)
        result = self.serial_read(count)
        event['actual_return_length'] = len(result)
        event['hex'] = result.hex(' ')
        return result

    def flush(self):
        self.event('serial.flush')
        return self.serial_flush()

    def install(self):
        self.display._write = self.write
        self.display.serial.write = self.serial_write_call
        self.display.serial.read = self.read
        self.display.serial.flush = self.flush

    def restore(self):
        self.display._write = self.original_write
        self.display.serial.write = self.serial_write
        self.display.serial.read = self.serial_read
        self.display.serial.flush = self.serial_flush


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', required=True)
    parser.add_argument('--source-dir', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = load_source(args.source_dir)
    result = {'evidence_kind': 'supplemental_hardware_host_call_trace',
              'captured_utc': datetime.now(timezone.utc).isoformat(),
              'metadata': source_metadata(source), 'frames': [],
              'scope': 'Hooks installed after connect; only Display.show calls; no GUI/ADC/extra register calls'}
    display = source.Display(args.port)
    try:
        info = display.connect()
        if (info.width, info.height) != (160, 80):
            raise RuntimeError('Unexpected display dimensions')
        result['device'] = {'protocol': info.protocol, 'width': info.width, 'height': info.height}
        trace = CallTrace(display, source)
        trace.install()
        try:
            for pattern, index in (('fixed', 0), ('moving', 1)):
                trace.events = []
                image = Image.new('RGB', (160, 80), (0, 0, 0))
                x = 20 if pattern == 'fixed' else (index * 3) % 144
                ImageDraw.Draw(image).rectangle((x, 24, x + 15, 39), fill=(255, 255, 255))
                display.show(image)
                result['frames'].append({'pattern': pattern, 'index': index, 'events': trace.events})
                # Outside measured frame scope. Keeps the two trace frames distinct.
                if pattern == 'fixed':
                    time.sleep(.1)
        finally:
            trace.restore()
    finally:
        display.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    for frame in result['frames']:
        kinds = [event['kind'] for event in frame['events']]
        print(frame['pattern'], {name: kinds.count(name) for name in sorted(set(kinds))})
    print('Saved host call trace:', args.output.name)


if __name__ == '__main__':
    main()
