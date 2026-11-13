"""Original Windows refresh timing loop, with portable CLI and JSON export.

Calls the selected source tree's unmodified Display.show(). No flash operations.
Default workload is fixed: 120 fixed frames, then 120 moving frames, at 10 FPS.
--check imports and identifies the selected source without opening any port.
"""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time

from PIL import Image, ImageDraw


def load_source(source_dir=None):
    directory = Path(source_dir).resolve() if source_dir else Path(__file__).resolve().parent
    sys.path.insert(0, str(directory))
    import msu2
    if Path(msu2.__file__).resolve().parent != directory:
        raise RuntimeError('Selected msu2 module is already imported from a different tree')
    for name in ('Display', 'image_bytes', 'encode_pixels'):
        if not hasattr(msu2, name):
            raise RuntimeError('Selected source lacks required API: ' + name)
    return msu2


def source_metadata(msu2):
    directory = Path(msu2.__file__).resolve().parent
    hashes = {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
              for name in ('msu2.py', 'app.py') if (directory / name).is_file()}
    commit = None
    dirty = None
    try:
        if (directory / '.git').exists():
            commit = subprocess.check_output(
                ['git', 'rev-parse', 'HEAD'], cwd=directory, text=True,
                stderr=subprocess.DEVNULL).strip()
            dirty = bool(subprocess.check_output(
                ['git', 'status', '--porcelain', '--', 'msu2.py', 'app.py'],
                cwd=directory, text=True, stderr=subprocess.DEVNULL).strip())
    except (OSError, subprocess.CalledProcessError):
        pass
    return {
        'source_commit': commit, 'measured_source_dirty': dirty, 'source_sha256': hashes,
        'python': sys.version, 'platform': platform.platform(),
        'dependencies': {name: importlib.metadata.version(name)
                         for name in ('pyserial', 'Pillow', 'psutil')},
    }


def distribution(values):
    values = sorted(values)
    n = len(values)
    return dict(median=statistics.median(values),
                p95=values[int((n - 1) * .95)], p99=values[int((n - 1) * .99)],
                max=values[-1])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', help='Serial port supplied by caller (COM port or /dev/cu.*)')
    parser.add_argument('--source-dir', type=Path, help='Explicit source tree; default is this script directory')
    parser.add_argument('--output', type=Path, help='Optional JSON with per-frame rows and aggregate metrics')
    parser.add_argument('--check', action='store_true', help='Check imports and source identity without hardware')
    args = parser.parse_args()
    if not args.check and not args.port:
        parser.error('--port is required unless --check is used')
    msu2 = load_source(args.source_dir)
    metadata = source_metadata(msu2)
    if args.check:
        print(json.dumps(metadata, indent=2))
        return
    results = {'evidence_kind': 'hardware_timing_run', 'metadata': metadata, 'patterns': {}}
    port=args.port
    d=msu2.Display(port)
    stats=None
    original_image_bytes=msu2.image_bytes
    original_encode=msu2.encode_pixels

    def timed_image_bytes(image):
        t=time.perf_counter_ns(); value=original_image_bytes(image); stats['rgb'] += time.perf_counter_ns()-t; return value

    def timed_encode(data):
        t=time.perf_counter_ns(); value=original_encode(data); stats['encode'] += time.perf_counter_ns()-t; return value

    msu2.image_bytes=timed_image_bytes
    msu2.encode_pixels=timed_encode

    def summary(values):
        v=sorted(values); n=len(v)
        return 'median={:.3f} p95={:.3f} p99={:.3f} max={:.3f}'.format(statistics.median(v),v[int((n-1)*.95)],v[int((n-1)*.99)],v[-1])
    try:
        info=d.connect(); print('CONNECTED protocol={} width={} height={}'.format(info.protocol,info.width,info.height),flush=True)
        if (info.width,info.height)!=(160,80): raise RuntimeError('Unexpected display dimensions')
        original_write=d._write
        original_read=d.serial.read
        current_stage=['pre']
        def timed_write(data):
            if len(data)>1000: current_stage[0]='post'
            elif bytes(data[:3])==b'\x02\x03\x07': current_stage[0]='ack'
            t=time.perf_counter_ns()
            try: return original_write(data)
            finally:
                stats['write'] += time.perf_counter_ns()-t
                stats['writes'] += 1
        def timed_read(count):
            t=time.perf_counter_ns()
            try: return original_read(count)
            finally:
                elapsed=time.perf_counter_ns()-t
                stats['read'] += elapsed
                stats[current_stage[0]+'_read'] += elapsed
                stats['reads'] += 1
        d._write=timed_write
        d.serial.read=timed_read
        for pattern in ('fixed','moving'):
            rows=[]; starts=[]; target=time.perf_counter()
            for i in range(120):
                target += .1
                delay=target-time.perf_counter()
                if delay>0: time.sleep(delay)
                start=time.perf_counter_ns(); starts.append(start)
                img=Image.new('RGB',(160,80),(0,0,0)); draw=ImageDraw.Draw(img)
                x=20 if pattern=='fixed' else (i*3)%144
                draw.rectangle((x,24,x+15,39),fill=(255,255,255))
                gen=time.perf_counter_ns()-start
                stats=dict.fromkeys(('rgb','encode','write','read','pre_read','ack_read','post_read','writes','reads'),0)
                current_stage[0]='pre'
                d.show(img)
                total=time.perf_counter_ns()-start
                rows.append((gen,stats['rgb'],stats['encode'],stats['write'],stats['read'],stats['pre_read'],stats['ack_read'],stats['post_read'],total,stats['writes'],stats['reads']))
            print('PATTERN',pattern,'frames',len(rows),'target_fps',10,flush=True)
            for j,name in enumerate(('generate','rgb565','encode_packets','write_calls','all_serial_reads','pre_register_reads','display_ack_reads','post_register_reads','total')):
                print(name,summary([r[j]/1e6 for r in rows]),flush=True)
            print('start_interval_ms',summary([(b-a)/1e6 for a,b in zip(starts,starts[1:])]),flush=True)
            print('write_call_counts',sorted(set(r[9] for r in rows)),'read_call_counts',sorted(set(r[10] for r in rows)),flush=True)
            names = ('generate','rgb565','encode_packets','write_calls','all_serial_reads','pre_register_reads','display_ack_reads','post_register_reads','total')
            metrics = {name: distribution([r[j]/1e6 for r in rows]) for j, name in enumerate(names)}
            metrics['start_interval_ms'] = distribution([(b-a)/1e6 for a,b in zip(starts,starts[1:])])
            results['patterns'][pattern] = {
                'frames': len(rows), 'target_fps': 10, 'metrics_ms': metrics,
                'protocol_write_call_counts': sorted(set(r[9] for r in rows)),
                'serial_read_call_counts': sorted(set(r[10] for r in rows)),
                'per_frame': [
                    {'index': i, 'start_ms': (starts[i]-starts[0])/1e6,
                     **{name: row[j]/1e6 for j, name in enumerate(names)},
                     'protocol_write_calls': row[9], 'serial_read_calls': row[10]}
                    for i, row in enumerate(rows)]}
    finally:
        d.close()
        msu2.image_bytes=original_image_bytes
        msu2.encode_pixels=original_encode
        print('CLOSED',flush=True)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(results, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
