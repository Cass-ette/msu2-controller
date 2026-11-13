"""Wire-format equivalence for the shared native pixel conversion path."""
import random
import struct
import unittest

from PIL import Image

from msu2 import encode_pixels, image_bytes


def reference_rgb565(image):
    raw = image.convert('RGB').tobytes()
    return b''.join(struct.pack('>H', (r >> 3) << 11 | (g >> 2) << 5 | b >> 3)
                    for r, g, b in zip(raw[::3], raw[1::3], raw[2::3]))


class PixelPipelineTests(unittest.TestCase):
    def test_primary_colors_and_discarded_low_bits(self):
        image = Image.new('RGB', (7, 1))
        image.putdata([(0, 0, 0), (255, 0, 0), (0, 255, 0), (0, 0, 255),
                       (255, 255, 255), (7, 3, 7), (8, 4, 8)])
        self.assertEqual(image_bytes(image), bytes.fromhex('0000 f800 07e0 001f ffff 0000 0821'))

    def test_all_65536_device_colors_round_trip(self):
        packed = b''.join(struct.pack('>H', value) for value in range(65536))
        raw = bytes(channel for value in range(65536)
                    for channel in (((value >> 11) & 31) * 255 // 31,
                                    ((value >> 5) & 63) * 255 // 63,
                                    (value & 31) * 255 // 31))
        self.assertEqual(image_bytes(Image.frombytes('RGB', (256, 256), raw)), packed)

    def test_every_channel_value_and_supported_source_modes_match_reference(self):
        raw = bytes(channel for r in range(256) for g in range(256)
                    for channel in (r, g, (r * 37 + g * 71) % 256))
        image = Image.frombytes('RGB', (256, 256), raw)
        for mode in ('RGB', 'RGBA', 'L', 'LA', 'P', '1', 'CMYK'):
            with self.subTest(mode=mode):
                source = image.convert(mode)
                before = source.tobytes()
                self.assertEqual(image_bytes(source), reference_rgb565(source))
                self.assertEqual(source.tobytes(), before)

    def test_staging_packets_preserve_payload_padding_and_used_length(self):
        rng = random.Random(565)
        source = rng.randbytes(25600)
        for length in (*range(2, 1026, 2), 25600):
            with self.subTest(length=length):
                pixels = source[:length]
                packet = encode_pixels(pixels)
                blocks = (length + 255) // 256
                self.assertEqual(len(packet), blocks * 390)
                decoded = bytearray()
                for block in range(blocks):
                    commands = packet[block * 390:(block + 1) * 390]
                    used = min(256, length - block * 256)
                    data = bytearray()
                    for slot in range(64):
                        command = commands[slot * 6:slot * 6 + 6]
                        self.assertEqual(command[:2], bytes((4, slot)))
                        data.extend(command[2:])
                    self.assertEqual(commands[-6:], bytes((2, 3, 8, used >> 8, used & 255, 0)))
                    self.assertEqual(data[used:], b'\xff' * (256 - used))
                    decoded.extend(data[:used])
                self.assertEqual(decoded, pixels)

    def test_invalid_pixel_lengths_are_rejected(self):
        for data in (b'', b'\0', b'\0' * 257):
            with self.subTest(length=len(data)), self.assertRaises(ValueError):
                encode_pixels(data)


if __name__ == '__main__':
    unittest.main()
