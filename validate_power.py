"""Read-only Mac power evidence; optional acknowledged live screen output."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from app import font
from power_graphics import render_dashboard, render_power
from power_monitor import PowerMonitor


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples',type=int,default=6)
    parser.add_argument('--hardware',action='store_true')
    args=parser.parse_args()
    if not 1 <= args.samples <= 150:
        parser.error('--samples must be 1–150')
    output=Path(__file__).parent/'validation'
    output.mkdir(exist_ok=True)
    monitor=PowerMonitor()
    report={'started_utc':datetime.now(timezone.utc).isoformat(),'samples':[],
            'flash_written':False,'panel_observed':False}
    device=None
    try:
        if args.hardware:
            from msu2 import Display,find_ports
            ports=find_ports()
            if len(ports)!=1:
                raise RuntimeError(f'Expected one display; found {len(ports)}')
            device=Display(ports[0].device)
            info=device.connect()
            report['size']=[info.width,info.height]
        for index in range(args.samples):
            sample=monitor.collect()
            report['samples'].append(asdict(sample))
            if sample.error:
                raise RuntimeError(sample.error)
            print(json.dumps(asdict(sample),ensure_ascii=False),flush=True)
            compact=render_power((160,80),sample,font)
            compact.save(output/'power-screen.png')
            render_dashboard(sample,monitor.samples(),font,time.monotonic()).save(output/'power-dashboard.png')
            if device:
                device.live_operation(lambda:device.show(compact))
                report['acknowledged_frames']=index+1
            if index+1<args.samples:
                time.sleep(2)
        report['ok']=True
    except Exception as exc:
        report.update(ok=False,error=str(exc))
        raise
    finally:
        monitor.close()
        if device:
            device.close()
        (output/'power-hardware.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':
    main()
