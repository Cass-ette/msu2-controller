"""Measure the real Tk scheduler using synthetic frames and optional hardware.

The GUI and its single worker run normally, including physical-button polling.
Synthetic input isolates scheduling from desktop-capture permissions and cost.
No flash operations. Without --hardware, device I/O is simulated explicitly.
"""
import argparse
from datetime import datetime, timezone
import importlib
import json
from pathlib import Path
import sys
import threading
import time

from PIL import Image, ImageDraw

from benchmark_refresh import distribution, load_source, source_metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path)
    parser.add_argument('--hardware', action='store_true')
    parser.add_argument('--port')
    parser.add_argument('--seconds', type=float, default=12)
    parser.add_argument('--fps', type=int, choices=(2, 5, 10), default=10)
    parser.add_argument('--ui-step-ms', type=float, default=1,
                        help='Delay between Tk event-loop pumps; 50 models a busy UI')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.seconds < 3 or args.seconds > 60 or not 1 <= args.ui_step_ms <= 100:
        parser.error('seconds must be 3..60; ui-step-ms must be 1..100')
    if args.hardware and not args.port:
        parser.error('--hardware requires --port')
    msu2 = load_source(args.source_dir)
    module = importlib.import_module('app')
    if Path(module.__file__).resolve().parent != Path(msu2.__file__).resolve().parent:
        raise RuntimeError('GUI and transport must come from the same source tree')
    import tkinter as tk
    from features import ButtonDebouncer

    report = {'evidence_kind': 'hardware_tk_scheduler' if args.hardware else 'simulated_io_tk_scheduler',
              'started_utc': datetime.now(timezone.utc).isoformat(),
              'metadata': source_metadata(msu2), 'target_fps': args.fps,
              'requested_seconds': args.seconds, 'ui_step_ms': args.ui_step_ms,
              'input': 'synthetic RGB moving block through App.render_request',
              'button_polling': True, 'flash_written': False, 'passed': False}
    frames, adc, previews, job_delays = [], [], [], []
    measuring = [False]

    class SimulatedDisplay:
        def __init__(self):
            self.last_activity = time.monotonic()
            self.recovery_count = 0

        def live_operation(self, operation):
            return operation()

        def show(self, image):
            time.sleep(.045)
            self.last_activity = time.monotonic()

        def read_adc(self, channel):
            time.sleep(.002)
            self.last_activity = time.monotonic()
            return 1000

        def ping(self):
            self.last_activity = time.monotonic()

        def close(self):
            pass

    root = tk.Tk()
    root.withdraw()
    ui = module.App(root)
    ui.live.set(False)
    ui.hardware_button.set(False)
    device = msu2.Display(args.port) if args.hardware else SimulatedDisplay()
    real_show, real_adc = device.show, device.read_adc
    real_preview, real_run = ui.update_preview, ui.run

    def show(image):
        started = time.monotonic()
        result = real_show(image)
        if measuring[0]:
            frames.append({'start': started, 'duration_ms': (time.monotonic() - started) * 1000})
        return result

    def read_adc(channel):
        result = real_adc(channel)
        if measuring[0]:
            adc.append(time.monotonic())
        return result

    def preview(image=None):
        result = real_preview(image)
        if measuring[0]:
            previews.append(time.monotonic())
        return result

    def run(operation, done, failed=None):
        finished = []

        def observed():
            if threading.current_thread() is threading.main_thread():
                raise RuntimeError('Device operation ran on the Tk thread')
            try:
                return operation()
            finally:
                finished.append(time.monotonic())

        def completed(value):
            if measuring[0] and finished:
                job_delays.append((time.monotonic() - finished[0]) * 1000)
            done(value)
        return real_run(observed, completed, failed)

    def render(request):
        frame = Image.new('RGB', request['size'], '#101824')
        draw = ImageDraw.Draw(frame)
        x = len(frames) * 3 % 144
        draw.rectangle((x, 24, x + 15, 39), fill='white')
        return frame

    device.show, device.read_adc = show, read_adc
    ui.update_preview, ui.run, ui.render_request = preview, run, render

    def pump_until(condition, timeout):
        end = time.monotonic() + timeout
        while not condition():
            root.update()
            if time.monotonic() >= end:
                raise RuntimeError('GUI operation timed out: ' + ui.status.get())
            time.sleep(.001)

    try:
        if args.hardware:
            device.connect()
        ui.display = device
        ui.size = (160, 80)
        ui.button_detector = ButtonDebouncer(real_adc(9))
        ui.mode.set('投屏')
        ui.screen_fps.set(str(args.fps))
        ui.mode_changed()
        pump_until(lambda: not ui.busy and not ui.pending_frame, 5)
        ui.hardware_button.set(True)
        ui.live.set(True)
        ui.next_frame_at = time.monotonic()
        ui.next_button_at = time.monotonic()
        measuring[0] = True
        started = time.monotonic()
        while time.monotonic() - started < args.seconds:
            root.update()
            if ui.display is None:
                raise RuntimeError('Device disconnected: ' + ui.status.get())
            if ui.mode.get() != '投屏':
                raise RuntimeError('Physical button changed mode during benchmark')
            time.sleep(args.ui_step_ms / 1000)
        ui.live.set(False)
        ui.hardware_button.set(False)
        ui.pending_frame = False
        pump_until(lambda: not ui.busy, 5)
        measured_seconds = time.monotonic() - started
        measuring[0] = False
        frame_starts = [row['start'] for row in frames]
        intervals = [(b - a) * 1000 for a, b in zip(frame_starts, frame_starts[1:])]
        report.update({'measured_seconds': measured_seconds, 'frames': len(frames),
                       'completed_fps': len(frames) / measured_seconds,
                       'adc_samples': len(adc), 'preview_updates': len(previews),
                       'serial_recoveries': device.recovery_count,
                       'frame_start_interval_ms': distribution(intervals) if intervals else None,
                       'show_duration_ms': distribution([row['duration_ms'] for row in frames]) if frames else None,
                       'worker_completion_to_ui_ms': distribution(job_delays) if job_delays else None,
                       'frames_data': [{'start_ms': (row['start'] - started) * 1000,
                                        'duration_ms': row['duration_ms']} for row in frames],
                       'adc_times_ms': [(stamp - started) * 1000 for stamp in adc],
                       'passed': len(frames) >= 2 and len(adc) >= 2 and device.recovery_count == 0})
    except Exception as exc:
        report['error'] = str(exc)
    finally:
        measuring[0] = False
        ui.live.set(False)
        ui.hardware_button.set(False)
        ui.close_requested = True
        ui.executor.shutdown(wait=True, cancel_futures=True)
        ui.busy = False
        ui.close()
        device.close()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items()
                      if key not in ('frames_data', 'adc_times_ms', 'metadata')}, indent=2), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
