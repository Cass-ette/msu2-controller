"""Live-only macOS pacing optimization and conservative flash isolation."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from PIL import Image
import msu2


class TransportTests(unittest.TestCase):
    def device(self):
        device=msu2.Display('FAKE')
        device.info=msu2.DeviceInfo('FAKE','01',160,80)
        chunks=[]
        device.serial=SimpleNamespace(write=lambda view:chunks.append(bytes(view)) or len(view))
        return device,chunks

    def test_live_60_byte_chunks_keep_exact_payload_without_sleep(self):
        device,chunks=self.device()
        image=Image.new('RGB',(160,80),'#123456')
        with patch.object(msu2.sys,'platform','darwin'),patch.object(msu2.time,'sleep') as sleep, \
             patch.object(device,'_display_ack'),patch.object(device,'read_u16',side_effect=(160,80)):
            device.show(image)
        self.assertEqual(b''.join(chunks[3:]),msu2.encode_pixels(msu2.image_bytes(image)))
        self.assertEqual(set(map(len,chunks[3:])),{60})
        sleep.assert_not_called()
        self.assertFalse(device._display_payload)

    def test_failed_live_write_restores_flash_pacing(self):
        device,_=self.device()
        calls=[0]
        def write(view):
            calls[0]+=1
            if len(view)>6:
                raise OSError('unplugged')
            return len(view)
        device.serial.write=write
        with patch.object(msu2.sys,'platform','darwin'),patch.object(device,'_display_ack'):
            with self.assertRaises(OSError):device.show(Image.new('RGB',(160,80)))
        self.assertFalse(device._display_payload)
        device.serial.write=lambda view:len(view)
        with patch.object(msu2.sys,'platform','darwin'),patch.object(msu2.time,'sleep') as sleep:
            device._write(b'\0'*384)
        self.assertEqual(sleep.call_count,7)

    def test_windows_live_path_keeps_original_bulk_write(self):
        device,chunks=self.device()
        with patch.object(msu2.sys,'platform','win32'),patch.object(msu2.time,'sleep') as sleep, \
             patch.object(device,'_display_ack'),patch.object(device,'read_u16',side_effect=(160,80)):
            device.show(Image.new('RGB',(160,80)))
        self.assertEqual(list(map(len,chunks)),[6,6,6,39000])
        sleep.assert_not_called()


if __name__=='__main__':
    unittest.main(verbosity=2)
