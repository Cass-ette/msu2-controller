"""Checks for diagnostic accounting only; never opens a serial port."""
import hashlib
from types import SimpleNamespace
import unittest

import msu2
from benchmark_refresh import distribution
from trace_refresh_calls import CallTrace


class RefreshDiagnosticTests(unittest.TestCase):
    def test_original_floor_percentiles(self):
        values = list(reversed(range(120)))
        self.assertEqual(distribution(values),
                         {'median': 59.5, 'p95': 113, 'p99': 117, 'max': 119})

    def make_trace(self):
        self.written = bytearray()
        self.replies = bytearray(b'abcdef')
        self.flushes = 0

        def write(data):
            count = min(3, len(data))
            self.written.extend(data[:count])
            return count

        def read(count):
            value = bytes(self.replies[:min(count, 2)])
            del self.replies[:len(value)]
            return value

        def flush():
            self.flushes += 1

        display = msu2.Display('FAKE')
        display.serial = SimpleNamespace(write=write, read=read, flush=flush)
        return display, CallTrace(display, msu2)

    def test_trace_separates_protocol_calls_from_short_serial_io(self):
        display, trace = self.make_trace()
        before_write, before_read = display._write, display.serial.read
        trace.install()
        try:
            display._write(b'123456')
            self.assertEqual(display._read_exact(6), b'abcdef')
        finally:
            trace.restore()
        self.assertEqual(bytes(self.written), b'123456')
        protocol = [e for e in trace.events if e['kind'] == 'Display._write']
        writes = [e for e in trace.events if e['kind'] == 'serial.write']
        reads = [e for e in trace.events if e['kind'] == 'serial.read']
        self.assertEqual(len(protocol), 1)
        self.assertIsNone(protocol[0]['return_value'])
        self.assertEqual([e['requested_length'] for e in writes], [6, 3])
        self.assertEqual([e['actual_return_length'] for e in writes], [3, 3])
        self.assertEqual([e['requested_length'] for e in reads], [6, 4, 2])
        self.assertEqual([e['actual_return_length'] for e in reads], [2, 2, 2])
        self.assertEqual(self.flushes, 0)
        self.assertEqual(display._write, before_write)
        self.assertEqual(display.serial.read, before_read)

    def test_payload_trace_retains_only_length_and_digest(self):
        display, trace = self.make_trace()
        payload = b'0123456789abcdef'
        trace.install()
        try:
            display._write(payload)
        finally:
            trace.restore()
        for event in trace.events:
            self.assertNotIn('hex', event)
            self.assertIn('sha256', event)
        self.assertEqual(trace.events[0]['requested_length'], len(payload))
        self.assertEqual(trace.events[0]['sha256'], hashlib.sha256(payload).hexdigest())
        self.assertEqual(bytes(self.written), payload)


if __name__ == '__main__':
    unittest.main()
