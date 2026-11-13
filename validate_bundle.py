"""Exercise the actual bundled runtime, UI resources and optional live display."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import tkinter as tk

from PIL import Image, ImageDraw

from animation import load_gif, normalize
from app import App, font, render
from app_paths import data_directory, log_directory
from msu2 import Display, find_ports
from notification_api import NotificationServer
from notifications import NotificationInbox
from notify_client import NotificationClient
from platform_support import screen_capture_access
from power_monitor import read_mac_power


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--smoke-test', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--hardware', action='store_true')
    args = parser.parse_args()
    report = {'started_utc': datetime.now(timezone.utc).isoformat(),
              'frozen': bool(getattr(sys, 'frozen', False)), 'executable': sys.executable,
              'resources': str(Path(__file__).parent), 'flash_written': False,
              'steps': [], 'passed': False}
    ui = device = server = None
    try:
        root = tk.Tk()
        root.withdraw()
        ui = App(root)
        ui.live.set(False)
        ui.hardware_button.set(False)
        # Leave all hardware access below on this thread; GUI timers stay idle.
        for timer in root.tk.call('after', 'info'):
            root.after_cancel(timer)
        report['steps'].append({'step': 'tk_gui', 'version': str(root.tk.call('info', 'patchlevel'))})
        data_directory().mkdir(parents=True, exist_ok=True)
        log_directory().mkdir(parents=True, exist_ok=True)
        report['data_directory'] = str(data_directory())
        report['log_directory'] = str(log_directory())
        report['font'] = list(font(16).getname())
        with tempfile.TemporaryDirectory(prefix='msu2-bundle-') as directory:
            source = Image.new('RGB', (200, 600), '#174e42')
            draw = ImageDraw.Draw(source)
            draw.rectangle((0, 0, 199, 199), fill='#30548e')
            draw.text((8, 220), '中文取景', font=font(22), fill='white')
            path = Path(directory) / 'portrait.png'
            source.save(path)
            ui.edit_picture(str(path))
            editor = ui.image_editor
            editor.rotate(True)
            editor.model.zoom_to(1.3)
            editor.model.pan(15, 0)
            expected = editor.model.render()
            editor.apply()
            assert ui.image().tobytes() == expected.tobytes()
            ui.open_asset_editor()
            ui.asset_editor.set_image(expected)
            ui.asset_editor.adjust_image()
            ui.asset_editor.image_editor.close()
            ui.asset_editor.close()
            ui.open_animation_editor()
            ui.animation_editor.close()
            ui.open_power_window()
            ui.power_window.close()
            report['steps'].append({'step': 'all_editor_windows_and_crop', 'passed': True})
        demo = normalize(load_gif(Path(__file__).parent / 'examples' / 'offline-demo.gif'))
        assert len(demo) == 36
        report['steps'].append({'step': 'bundled_example', 'frames': len(demo)})
        server = NotificationServer(NotificationInbox()).start()
        client = NotificationClient() if getattr(sys, 'frozen', False) else NotificationClient(server.discovery_path)
        assert client.discovery_path == server.discovery_path
        assert client.notify('安装验证', '应用内置接口', '通用通知验证')['status'] == 'queued'
        assert client.status()['api_version'] == 1
        server.stop()
        server = None
        report['steps'].append({'step': 'notification_http', 'passed': True})
        # Read-only sampling and permission query must also work in the bundle.
        power = read_mac_power()
        report['steps'].append({'step': 'power', 'state': power.state, 'error': power.error})
        report['capture_access'] = screen_capture_access()
        ports = find_ports()
        report['ports'] = [p.device for p in ports]
        if args.hardware:
            if len(ports) != 1:
                raise RuntimeError(f'Expected one screen; found {len(ports)}')
            device = Display(ports[0].device)
            device.connect()
            chinese = render((160, 80), '文字', '应用版已连接', '中文 / 图标 / 内置运行时',
                             '#6ee7b7', '#101824', '')
            for image in (expected, chinese):
                device.live_operation(lambda: device.show(image))
                device.read_adc(9)
            report['steps'].append({'step': 'real_serial_display', 'frames': 2, 'passed': True})
        report['passed'] = True
    except Exception as exc:
        import traceback
        report['error'] = repr(exc)
        report['traceback'] = traceback.format_exc()
    finally:
        if device is not None:
            device.close()
        if server is not None:
            server.stop()
        if ui is not None:
            ui.close()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
