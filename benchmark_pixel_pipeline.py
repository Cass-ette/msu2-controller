"""Compare shared pixel preparation against a local baseline without hardware.

Use --baseline-dir pointing to an unchanged checkout of the previous version.
The order alternates each iteration, and both outputs must be byte-identical.
This benchmark measures CPU preparation, not serial throughput or LCD refresh.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

from PIL import Image, ImageDraw

import msu2
from benchmark_refresh import distribution, source_metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-dir', required=True, type=Path)
    parser.add_argument('--iterations', type=int, default=300)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.iterations < 2:
        parser.error('--iterations must be at least 2')
    source = args.baseline_dir / 'msu2.py'
    spec = importlib.util.spec_from_file_location('msu2_pixel_baseline', source)
    baseline = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = baseline
    spec.loader.exec_module(baseline)
    result = {
        'evidence_kind': 'host_pixel_preparation_only',
        'metadata': source_metadata(msu2),
        'baseline_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'warmup_iterations': 20, 'measured_iterations_per_pattern': args.iterations,
        'order': 'baseline then optimized on even iterations; reversed on odd iterations',
        'patterns': {},
    }
    fixed = Image.new('RGB', (160, 80))
    ImageDraw.Draw(fixed).rectangle((20, 24, 35, 39), fill='white')
    moving = Image.new('RGB', (160, 80))
    ImageDraw.Draw(moving).rectangle((3, 24, 18, 39), fill='white')
    colors = Image.frombytes('RGB', (160, 80), bytes(i % 251 for i in range(38400)))
    modules = {'baseline': baseline, 'optimized': msu2}
    for name, image in (('fixed', fixed), ('moving', moving), ('colors', colors)):
        rows = {label: [] for label in modules}
        for index in range(-20, args.iterations):
            outputs = {}
            order = ('baseline', 'optimized') if index % 2 == 0 else ('optimized', 'baseline')
            for label in order:
                module = modules[label]
                start = time.perf_counter_ns()
                pixels = module.image_bytes(image)
                converted = time.perf_counter_ns()
                packet = module.encode_pixels(pixels)
                finished = time.perf_counter_ns()
                outputs[label] = (pixels, packet)
                if index >= 0:
                    rows[label].append({'rgb565': (converted - start) / 1e6,
                                        'encode_packets': (finished - converted) / 1e6,
                                        'total': (finished - start) / 1e6})
            if outputs['baseline'] != outputs['optimized']:
                raise RuntimeError('Pixel or wire-command mismatch: ' + name)
        result['patterns'][name] = {
            'outputs_identical': True,
            'rgb565_sha256': hashlib.sha256(outputs['optimized'][0]).hexdigest(),
            'payload_sha256': hashlib.sha256(outputs['optimized'][1]).hexdigest(),
            'versions': {label: {
                'metrics_ms': {metric: distribution([row[metric] for row in samples])
                               for metric in ('rgb565', 'encode_packets', 'total')},
                'per_iteration_ms': samples,
            } for label, samples in rows.items()},
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({name: {label: data['metrics_ms'] for label, data in entry['versions'].items()}
                      for name, entry in result['patterns'].items()}, indent=2))


if __name__ == '__main__':
    main()
