"""Display-only USB CDC pacing comparison. Never invokes flash commands.

Register fences prove command synchronization, not pixel correctness: visual
confirmation is recorded separately and never inferred from a successful ACK.
"""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import random
from statistics import median
import time

from PIL import Image, ImageDraw
from msu2 import Display, find_ports


PROFILES = {
    'safe-60-1ms': (60,.001,False),
    '60-no-delay': (60,0,False),
    '60-drain': (60,0,True),
    '60-100us': (60,.0001,False),
    '60-250us': (60,.00025,False),
    '60-500us': (60,.0005,False),
    '120-1ms': (120,.001,False),
    '384-1ms': (384,.001,False),
    '390-1ms': (390,.001,False),
    'bulk-drain': (65536,0,True),
    '64-no-delay': (64,0,False),
    '64-1ms': (64,.001,False),
    '120-no-delay': (120,0,False),
    '126-no-delay': (126,0,False),
    '240-no-delay': (240,0,False),
}


def pattern(index):
    # Keep initial diagnostic pixel payloads to a simple known palette.
    image=Image.new('RGB',(160,80),'black')
    draw=ImageDraw.Draw(image)
    palette=('white','red','lime','blue','black')
    for band in range(10):
        draw.rectangle((band*16,0,band*16+15,79),fill=palette[(band+index)%5])
    x=(index*13)%144
    draw.rectangle((x,24,x+15,55),fill='white',outline='black',width=2)
    return image


def content(index):
    from app import render
    from power_monitor import PowerSample
    if index%6==0:
        return pattern(index)
    if index%6==1:
        return render((160,80),'文字',f'高速测试 {index}','中文 / MSU2 MINI','#6ee7b7','#101824','')
    if index%6==2:
        return render((160,80),'电脑状态','','','#6ee7b7','#101824','',
                      {'cpu':index%101,'memory':(index*7)%101,'upload':1226,'download':236000})
    if index%6==3:
        return render((160,80),'功率与电池','','','#6ee7b7','#101824','',
                      PowerSample(time.monotonic(),system_w=18.7,input_w=62.6,battery_w=43.9,
                                  percent=75,external=True,installed=True,state='充电'))
    if index%6==4:
        return Image.frombytes('RGB',(160,80),random.Random(index).randbytes(38400))
    return render((160,80),'时钟','','','#6ee7b7','#101824','')


class TransportDisplay(Display):
    def __init__(self,port,profile):
        super().__init__(port)
        self.profile=profile
        self.large_writes=[]

    def _write(self,data):
        if len(data)<=6:
            return super()._write(data)
        chunk,delay,drain=self.profile
        view=memoryview(data)
        start=time.monotonic()
        calls=0
        drain_seconds=0.
        while view:
            count=self.serial.write(view[:chunk])
            if not count:
                raise RuntimeError('zero byte write')
            calls+=1
            view=view[count:]
            if drain:
                tick=time.monotonic()
                self.serial.flush()
                drain_seconds+=time.monotonic()-tick
            if delay:
                time.sleep(delay)
        self.large_writes.append({'bytes':len(data),'seconds':time.monotonic()-start,
                                  'calls':calls,'drain_seconds':drain_seconds})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profiles',nargs='+',choices=tuple(PROFILES),default=list(PROFILES))
    parser.add_argument('--frames',type=int,default=6)
    parser.add_argument('--mixed',action='store_true')
    parser.add_argument('--hold',type=float,default=0)
    parser.add_argument('--output',type=Path,default=Path('validation/transport-matrix.json'))
    args=parser.parse_args()
    if not 1<=args.frames<=600:
        parser.error('frames must be 1–600')
    ports=find_ports()
    if len(ports)!=1:
        raise RuntimeError(f'Expected one display; found {len(ports)}')
    report={'started_utc':datetime.now(timezone.utc).isoformat(),
            'port':ports[0].device,'flash_written':False,'pixel_correctness':'requires visual confirmation',
            'profiles':[]}
    args.output.parent.mkdir(exist_ok=True)
    for name in args.profiles:
        profile=PROFILES[name]
        row={'name':name,'chunk_bytes':profile[0],'delay_seconds':profile[1],
             'drain_each_write':profile[2],'frame_seconds':[],'passed':False}
        report['profiles'].append(row)
        device=TransportDisplay(ports[0].device,profile)
        try:
            row['device']=asdict(device.connect())
            for index in range(args.frames):
                frame=content(index) if args.mixed else pattern(index)
                start=time.monotonic()
                # Do not auto-recover and hide a profile's first error.
                device.show(frame)
                device.read_adc(9)
                row['frame_seconds'].append(time.monotonic()-start)
                if args.hold:
                    time.sleep(min(2.,max(0.,args.hold)))
            row['passed']=True
            row['median_seconds']=median(row['frame_seconds'])
        except Exception as exc:
            row['error']=str(exc)
            if device.serial is not None:
                row['pending_bytes']=device.serial.in_waiting
                row['reply_trailer_hex']=device.serial.read(min(device.serial.in_waiting,96)).hex(' ')
        finally:
            row['large_writes']=device.large_writes
            device.close()
            args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in row.items() if k not in ('large_writes','device','frame_seconds')},
                         ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
