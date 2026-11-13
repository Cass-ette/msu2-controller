"""Real device: independent client -> HTTP queue -> temporary screen -> restore."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import tkinter as tk

from app import App, render_notification
from notification_api import NotificationServer
from notify_client import NotificationClient


def main():
    output = Path(__file__).parent/'validation'
    output.mkdir(exist_ok=True)
    report = {'started_utc':datetime.now(timezone.utc).isoformat(), 'steps':[]}
    def record(step, **details):
        item = {'step':step, **details}
        report['steps'].append(item)
        (output/'notifications-hardware.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(item,ensure_ascii=False),flush=True)
    root = tk.Tk()
    root.withdraw()
    ui = App(root)
    def pump_until(condition, seconds=20):
        deadline = time.monotonic()+seconds
        while time.monotonic() < deadline:
            root.update()
            if condition():
                return
            time.sleep(.01)
        raise RuntimeError('等待超时：'+ui.status.get())
    try:
        ui.live.set(False)
        ui.hardware_button.set(False)
        ui.title.set('自定义小屏')
        ui.subtitle.set('等待外部通知')
        ui.toggle_connection()
        pump_until(lambda:not ui.busy and not ui.pending_frame)
        if ui.display is None:
            raise RuntimeError(ui.status.get())
        original = ui.image()
        record('connected', ok=True, size=ui.size)
        shown = []
        real_show = ui.display.show
        def observed_show(frame):
            real_show(frame)
            shown.append(frame.tobytes())
        ui.display.show = observed_show
        with tempfile.TemporaryDirectory(prefix='msu2-notify-') as directory:
            discovery = Path(directory)/'api.json'
            ui.notification_service = NotificationServer(ui.notification_inbox,discovery).start()
            ui.api_enabled.set(True)
            ui.notification_inbox.set_connected(True)
            result = subprocess.run([sys.executable,'-B',str(Path(__file__).parent/'notify_client.py'),
                                      '--discovery',str(discovery),'notify','--source','应用 A',
                                      '--title','任务完成','--message','这是自定义通知',
                                      '--kind','success','--duration','4'],capture_output=True,text=True,check=True)
            first = json.loads(result.stdout)
            client = NotificationClient(discovery)
            second = client.notify('应用 B','收到新消息','任意程序均可调用',kind='info',duration=4)
            record('clients_submitted',ok=True,cli_id=first['id'],sdk_id=second['id'],
                   first_status=first['status'],second_status=second['status'])
            for index, item in enumerate((first,second),1):
                pump_until(lambda:client.get(item['id'])['status']=='showing')
                ui.notification_frame.save(output/f'notification-{index}.png')
                record('notification_showing',ok=True,id=item['id'],
                       source=item['notification']['source'],image_sha256=hashlib.sha256(ui.notification_frame.tobytes()).hexdigest())
                pump_until(lambda:client.get(item['id'])['status']=='completed')
            pump_until(lambda:not ui.busy and not ui.pending_frame and ui.active_notification is None)
            if not shown or shown[-1] != original.tobytes():
                raise RuntimeError('通知结束后未恢复原画面')
            if ui.title.get() != '自定义小屏' or ui.subtitle.get() != '等待外部通知' or ui.mode.get() != '文字':
                raise RuntimeError('通知修改了主窗口设置')
            record('original_restored',ok=True,acknowledged_frames=len(shown),
                   sha256=hashlib.sha256(shown[-1]).hexdigest(),settings_preserved=True)
            ui.api_enabled.set(False)
            ui.toggle_notification_api()
            if discovery.exists():
                raise RuntimeError('接口关闭后仍残留发现文件')
            record('finished',ok=True,flash_written=False)
        return 0
    except Exception as exc:
        record('failure',ok=False,error=repr(exc))
        return 1
    finally:
        ui.executor.shutdown(wait=True,cancel_futures=True)
        ui.busy=False
        ui.close()


if __name__ == '__main__':
    raise SystemExit(main())
