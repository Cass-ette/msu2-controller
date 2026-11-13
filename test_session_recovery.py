"""Wire-level regressions for MSN01 appearing where an ACK was expected."""
import unittest
from unittest.mock import patch

from PIL import Image

import features
import msu2
import test_features


class SessionSerial:
    """Firmware model that ignores commands after its host session expires."""
    def __init__(self):
        self.input = bytearray()
        self.writes = []
        self.closed = False
        self.active = True
        self.protocol = '01'
        self.max_read = 1  # Also exercise fragmented USB replies.
        self.reset_at = None
        self.resets_on_adc = 0
        self.descriptors = (b'Lcd_X\0\0' + bytes((32, 0, 0)) +
                            b'Lcd_Y\0\0' + bytes((32, 0, 1)) + b'\0').ljust(256, b'\0')

    @property
    def in_waiting(self):
        return len(self.input)

    def reset_input_buffer(self):
        self.input.clear()

    def expire(self):
        self.active = False
        self.input.extend(b'\0MSN' + self.protocol.encode())

    def read(self, count):
        count = min(count, self.max_read)
        result = bytes(self.input[:count])
        del self.input[:count]
        return result

    def write(self, data):
        data = bytes(data)
        self.writes.append(data)
        if data == msu2.HELLO:
            self.active = True
            self.input.extend(msu2.HELLO)
            return len(data)
        if not self.active:
            return len(data)
        for offset in range(0, len(data), 6):
            packet = data[offset:offset+6]
            if packet[:2] == b'\x00\x30':
                if packet[2] == 0:
                    address = int.from_bytes(packet[3:5], 'big')
                    self.input.extend(packet[:5] + bytes((self.descriptors[address-256],)))
                else:
                    self.input.extend(packet[:4] + (160 if packet[3] == 0 else 80).to_bytes(2, 'big'))
            elif packet[:2] == b'\x08\x09':
                if self.resets_on_adc:
                    self.resets_on_adc -= 1
                    self.expire()
                else:
                    self.input.extend(packet[:4] + (3830).to_bytes(2, 'big'))
            elif packet[:2] == b'\x03\x00':
                address = int.from_bytes(packet[2:5], 'big')
                if address == self.reset_at:
                    self.reset_at = None
                    self.expire()
                else:
                    self.input.extend(packet[:5] + bytes((address % 251,)))
            elif packet[:3] == b'\x02\x03\x07':
                self.input.extend(packet)
        return len(data)

    def close(self):
        self.closed = True


class SessionProtocolTests(unittest.TestCase):
    def setUp(self):
        self.device = msu2.Display('FAKE')
        self.serial = SessionSerial()
        self.device.serial = self.serial
        self.device.info = msu2.DeviceInfo('FAKE', '01', 160, 80)

    def test_register_banner_rehandshakes_on_same_port_and_retries_once(self):
        self.serial.expire()
        with patch.object(msu2.serial, 'Serial') as reopen:
            result = self.device.live_operation(lambda: self.device.read_adc(9))
        self.assertEqual(result, 3830)
        reopen.assert_not_called()
        self.assertFalse(self.serial.closed)
        self.assertEqual(self.device.recovery_count, 1)
        self.assertEqual(self.serial.writes.count(msu2.HELLO), 1)
        self.assertEqual(self.serial.writes.count(b'\x08\x09\0\0\0\0'), 2)

    def test_short_display_ack_recognizes_complete_banner_and_restores_frame(self):
        self.serial.max_read = 2
        self.serial.expire()
        with patch.object(msu2.sys, 'platform', 'win32'):
            self.device.live_operation(lambda: self.device.show(Image.new('RGB', (160, 80), 'red')))
        self.assertEqual(self.device.recovery_count, 1)
        frames = [data for data in self.serial.writes if len(data) == 39000]
        self.assertEqual(len(frames), 1)
        self.device.ping()
        self.assertFalse(self.serial.input)

    def test_backup_read_retries_same_byte_after_session_expiry(self):
        self.serial.reset_at = 256 + 17
        data = self.device.read_flash_region(256, 512)
        self.assertEqual(data, bytes(address % 251 for address in range(256, 768)))
        self.assertEqual(self.device.recovery_count, 1)
        request = b'\x03\x00' + (273).to_bytes(3, 'big') + b'\0'
        self.assertEqual(self.serial.writes.count(request), 2)

    def test_flash_erase_ack_banner_never_replays_write(self):
        self.serial.expire()
        with self.assertRaises(msu2.DeviceSessionReset):
            self.device.write_asset('clock_background', b'\0'*25600)
        self.assertEqual(self.device.recovery_count, 0)
        self.assertNotIn(msu2.HELLO, self.serial.writes)
        self.assertEqual(len(self.serial.writes), 1)
        self.assertTrue(self.serial.writes[0].startswith(b'\x03\x02'))

    def test_repeated_session_resets_stop_after_one_recovery(self):
        self.serial.resets_on_adc = 2
        with self.assertRaises(msu2.DeviceSessionReset):
            self.device.live_operation(lambda: self.device.read_adc(9))
        self.assertEqual(self.device.recovery_count, 1)
        self.assertEqual(self.serial.writes.count(msu2.HELLO), 1)

    def test_changed_protocol_stops_before_retrying_original_operation(self):
        self.serial.protocol = '02'
        self.serial.expire()
        with self.assertRaisesRegex(msu2.DeviceError, '规格改变'):
            self.device.live_operation(lambda: self.device.read_adc(9))
        self.assertTrue(self.serial.closed)
        self.assertEqual(self.serial.writes.count(b'\x08\x09\0\0\0\0'), 1)

    def test_other_invalid_reply_is_not_misidentified_as_a_banner(self):
        self.serial.input.extend(b'\x08\x01\0\0\0\x1d')
        with self.assertRaises(msu2.DeviceReplyError):
            self.device.read_adc(9)
        self.assertIsNone(self.device._pending_protocol)
        self.assertEqual(self.device.recovery_count, 0)


class SessionGuiTests(unittest.TestCase):
    setUp = test_features.GuiTests.setUp
    tearDown = test_features.GuiTests.tearDown
    complete = test_features.GuiTests.complete

    def test_recovery_keeps_connection_and_resends_static_screen(self):
        self.display.recovery_count = 1
        self.ui.mode.set('文字')
        self.ui.button_detector = features.ButtonDebouncer(3830)
        self.ui.button_detector.pressed = True
        self.ui.button_detector.candidate = True
        # Returning an ADC after recovery must not use the old pressed state.
        self.ui.run(lambda: (3830, 10), self.ui.button_polled)
        self.complete()
        self.assertIs(self.ui.display, self.display)
        self.assertFalse(self.display.closed)
        self.assertEqual(self.ui.mode.get(), '文字')
        self.assertTrue(self.ui.pending_frame)
        self.assertFalse(self.ui.button_detector.pressed)
        self.assertIn('自动恢复', self.ui.status.get())
        self.ui.tick()
        self.complete()
        self.assertEqual(len(self.display.frames), 1)
        self.assertFalse(self.ui.pending_frame)

    def test_long_modal_gap_recovers_before_first_resumed_send(self):
        def modal_gap_frame(image):
            self.display.recovery_count = 1
            self.display.frames.append(image.copy())
        self.ui.modal = True
        self.ui.tick()
        self.assertEqual(self.display.calls, [])
        self.ui.modal = False
        with patch.object(self.display, 'show', side_effect=modal_gap_frame):
            self.ui.send()
            self.complete()
        self.assertIs(self.ui.display, self.display)
        self.assertTrue(self.ui.pending_frame)
        self.assertIn('自动恢复', self.ui.status.get())


if __name__ == '__main__':
    unittest.main(verbosity=2)
