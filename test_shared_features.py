"""Behavioral checks for shared assets, safety, orientation and OS boundaries."""
import errno
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image, ImageDraw, ImageTk

import app
import assets
import assets_ui
import doctor
import features
import flash_graphics
import msu2
import offline_backup
import platform_support
import test_features


class AssetDevice(msu2.Display):
    def __init__(self):
        super().__init__('FAKE')
        self.serial = object()
        self.info = msu2.DeviceInfo('FAKE', '01', 160, 80)
        self.flash = bytearray((i * 37) % 256 for i in range(1024 * 1024))
        self.commands = []
        self.staging = b''
        self.shown = []

    def _write(self, data):
        if len(data) != 384:
            raise AssertionError('Unexpected staging packet')
        self.staging = b''.join(data[i+2:i+6] for i in range(0, 384, 6))

    def _exchange(self, packet, timeout=2):
        self.commands.append(packet)
        if packet[:2] == bytes((3, 2)):
            page, count = struct.unpack('>HH', packet[2:])
            self.flash[page*256:(page+count)*256] = b'\xff' * (count*256)
        elif packet[:2] == bytes((3, 3)):
            page = int.from_bytes(packet[2:5], 'big')
            self.flash[page*256:(page+1)*256] = self.staging
        else:
            raise AssertionError('Unexpected command')
        return packet

    def read_flash_byte(self, address):
        return self.flash[address]

    def show(self, image):
        self.shown.append(image.copy())


class AssetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.device = AssetDevice()
        self.original = bytes(self.device.flash)

    def test_backup_is_durable_before_first_erase_and_restore_is_exact(self):
        real = self.device.write_asset
        def checked_write(key, data, progress):
            backups = list(self.directory.glob('*.msu2asset'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(assets.load_backup(backups[0]).data, self.original[3826*256:3926*256])
            return real(key, data, progress)
        data = bytes((i % 256 for i in range(25600)))
        with patch.object(self.device, 'write_asset', side_effect=checked_write):
            result = assets.replace_asset(self.device, 'clock_background', data, self.directory, True)
        self.assertEqual(result['verified_bytes'], 25600)
        self.assertEqual(self.device.flash[:3826*256], self.original[:3826*256])
        self.assertEqual(self.device.flash[3926*256:], self.original[3926*256:])
        backup = assets.load_backup(result['backup_path'])
        assets.restore_asset(self.device, 'clock_background', backup, self.directory)
        self.assertEqual(bytes(self.device.flash), self.original)

    def test_failed_backup_never_erases(self):
        with patch.object(assets, 'save_backup', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(features.ContentError, '未开始写入'):
                assets.replace_asset(self.device, 'album_image', b'\x00'*25600, self.directory)
        self.assertFalse(self.device.commands)
        self.assertEqual(bytes(self.device.flash), self.original)

    def test_failed_write_exposes_preserved_backup(self):
        with patch.object(self.device, 'write_asset', side_effect=msu2.DeviceTimeout('timeout')):
            with self.assertRaisesRegex(msu2.DeviceError, '原图备份'):
                assets.replace_asset(self.device, 'album_image', b'\x00'*25600, self.directory)
        backup = assets.load_backup(next(self.directory.glob('*.msu2asset')))
        self.assertEqual(backup.data, self.original[3926*256:4026*256])

    def test_cancelled_read_and_invalid_write_do_not_erase(self):
        with self.assertRaises(assets.AssetCancelled):
            self.device.read_asset('clock_background', cancelled=lambda: True)
        for key, data in (('bad', b'\0'*25600), ('clock_background', b'\0')):
            with self.assertRaises(ValueError):
                assets.replace_asset(self.device, key, data, self.directory)
        self.assertFalse(self.device.commands)

    def test_corrupted_backup_or_wrong_slot_is_rejected(self):
        original = assets.AssetBackup('clock_background', b'\x00'*25600)
        path = assets.save_backup(self.directory/'backup.msu2asset', original)
        doc = json.loads(path.read_text())
        doc['sha256'] = 'invalid'
        path.write_text(json.dumps(doc))
        with self.assertRaises(ValueError):
            assets.load_backup(path)
        with self.assertRaises(ValueError):
            assets.restore_asset(self.device, 'album_image', original, self.directory)
        self.assertFalse(self.device.commands)

    def test_full_readback_catches_middle_byte_corruption(self):
        read = self.device.read_asset
        with patch.object(self.device, 'read_asset', side_effect=lambda *a, **kw: bytes([read(*a,**kw)[0]^1])+read(*a,**kw)[1:]):
            with self.assertRaisesRegex(msu2.DeviceError, '完整回读校验失败'):
                self.device.write_asset('clock_background', b'\0'*25600)

    def test_bin_round_trip_and_orientation_are_lossless(self):
        data = self.original[3826*256:3926*256]
        self.assertEqual(msu2.image_bytes(assets.decode_image(data)), data)
        image = assets.decode_image(data)
        inverted = assets.oriented_bytes(image, True)
        path = self.directory/'image.bin'
        path.write_bytes(inverted)
        upright = assets.load_image_or_bin(path, True)
        self.assertEqual(upright.tobytes(), image.tobytes())
        backup = assets.AssetBackup('clock_background', inverted, True)
        self.assertEqual(assets.upright_image(backup).tobytes(), image.tobytes())

    def test_invalid_bin_and_region_bounds_are_rejected(self):
        path = self.directory/'image.bin'
        path.write_bytes(b'\0'*25601)
        with self.assertRaises(ValueError):
            assets.load_image_or_bin(path)
        for address, length in ((-1,1), (1024*1024,1), (0,0)):
            with self.assertRaises(ValueError):
                self.device.read_flash_region(address,length)

    def test_standby_backup_before_erase_full_verify_and_later_assets_preserved(self):
        frame = Image.new('RGB', (160,80), '#f03080')
        result = offline_backup.safe_burn(self.device, (frame,)*36, self.directory)
        self.assertEqual(result['verified_bytes'], 921600)
        self.assertEqual(offline_backup.load_animation_backup(result['backup_path']), self.original[:921600])
        self.assertEqual(self.device.flash[921600:], self.original[921600:])
        restored = offline_backup.backup_frames(result['backup_path'])
        self.assertEqual(b''.join(msu2.image_bytes(f) for f in restored), self.original[:921600])

    def test_standby_backup_failure_stops_before_erase(self):
        frame = Image.new('RGB',(160,80))
        with patch.object(offline_backup, 'save_animation_backup', side_effect=OSError('disk full')):
            with self.assertRaises(features.ContentError):
                offline_backup.safe_burn(self.device,(frame,)*36,self.directory)
        self.assertFalse(self.device.commands)

    def test_cancel_after_backup_never_starts_asset_or_animation_erase(self):
        cancelled = [False]
        save_asset = assets.save_backup
        def cancel_asset(*args):
            path = save_asset(*args)
            cancelled[0] = True
            return path
        with patch.object(assets, 'save_backup', side_effect=cancel_asset):
            with self.assertRaises(assets.AssetCancelled):
                assets.replace_asset(self.device, 'album_image', b'\0'*25600,
                                     self.directory, cancelled=lambda: cancelled[0])
        cancelled[0] = False
        save_animation = offline_backup.save_animation_backup
        def cancel_animation(*args):
            path = save_animation(*args)
            cancelled[0] = True
            return path
        with patch.object(offline_backup, 'save_animation_backup', side_effect=cancel_animation):
            with self.assertRaises(assets.AssetCancelled):
                offline_backup.safe_burn(self.device, (Image.new('RGB', (160,80)),)*36,
                                         self.directory, cancelled=lambda: cancelled[0])
        self.assertFalse(self.device.commands)
        self.assertEqual(bytes(self.device.flash), self.original)

    def test_invalid_saved_backup_stops_before_erase(self):
        def corrupt_asset(path, backup):
            Path(path).write_text('{}')
            return path
        with patch.object(assets, 'save_backup', side_effect=corrupt_asset):
            with self.assertRaises(features.ContentError):
                assets.replace_asset(self.device, 'album_image', b'\0'*25600, self.directory)
        with patch.object(offline_backup, 'load_animation_backup', return_value=b'bad'):
            with self.assertRaises(features.ContentError):
                offline_backup.safe_burn(self.device, (Image.new('RGB', (160,80)),)*36, self.directory)
        self.assertFalse(self.device.commands)


class GraphicsTests(unittest.TestCase):
    def test_monochrome_bits_are_msb_first_row_major(self):
        frame = flash_graphics.mono_image(bytes((0x80,0x01)), (8,2), 'red', 'blue')
        self.assertEqual(frame.getpixel((0,0)),(255,0,0))
        self.assertEqual(frame.getpixel((7,1)),(255,0,0))
        self.assertEqual(frame.getpixel((1,0)),(0,0,255))

    def test_ascii_reads_existing_glyph_pages_and_preserves_background(self):
        device = MagicMock()
        device.read_flash_region.return_value = b'\x80'+b'\x00'*255
        bg = Image.new('RGB',(160,80),'blue')
        frame = flash_graphics.read_ascii(device,'AA','red',background=bg)
        self.assertEqual(device.read_flash_region.call_count,1)
        self.assertEqual(device.read_flash_region.call_args.args[:2], ((3651+65)*256,256))
        self.assertEqual(frame.getpixel((0,8)),(255,0,0))
        self.assertEqual(frame.getpixel((32,8)),(255,0,0))
        self.assertEqual(frame.getpixel((1,8)),(0,0,255))
        self.assertEqual(bg.getpixel((0,8)),(0,0,255))

    def test_invalid_graphics_do_not_read_or_write(self):
        device=MagicMock()
        for text in ('中文','abcdef','\n'):
            with self.assertRaises(ValueError):
                flash_graphics.read_ascii(device,text,'red')
        for page,size in ((-1,(160,40)),(3600,(159,40)),(4096,(160,40))):
            with self.assertRaises(ValueError):
                flash_graphics.read_graphic(device,page,size,'red')
        device.read_flash_region.assert_not_called()
        device.write_asset.assert_not_called()


class PlatformTests(unittest.TestCase):
    def test_recovery_rejects_a_different_device_shape_before_replay(self):
        device = msu2.Display('FAKE')
        device.info = msu2.DeviceInfo('FAKE', '01', 160, 80)
        with patch.object(device, 'close') as close, patch.object(device, 'connect',
                return_value=msu2.DeviceInfo('FAKE', '01', 240, 240)):
            with self.assertRaisesRegex(msu2.DeviceError, '规格改变'):
                device.recover()
            self.assertEqual(close.call_count, 2)

    def test_access_errors_are_not_misreported_as_occupied_ports(self):
        for code in (errno.EPERM,errno.EACCES):
            self.assertEqual(platform_support.connection_failure(OSError(code,'denied')),'permission')
        self.assertEqual(platform_support.connection_failure(OSError(errno.EBUSY,'busy')),'busy')
        report={'platform':'darwin','python_supported':True,'modules':{},'screen_ports':['port'],
                'probe':{'ok':False,'error_kind':'permission'}}
        self.assertIn('沙箱',' '.join(doctor.advice(report)))

    def test_denied_recording_permission_does_not_capture_silently(self):
        with patch.object(features.sys,'platform','darwin'), patch.object(features,'screen_capture_access',return_value={'granted':False}), patch.object(features.ImageGrab,'grab') as grab:
            with self.assertRaises(features.ScreenCapturePermissionError):
                features.capture_desktop((160,80))
            grab.assert_not_called()

    def test_macos_pacing_and_windows_bulk_path_both_preserve_bytes(self):
        data=bytes(range(255))*3
        for platform in ('darwin','win32'):
            display=msu2.Display('FAKE')
            output=[]
            def write(view):
                output.append(bytes(view))
                return len(view)
            display.serial=SimpleNamespace(write=write)
            with patch.object(msu2.sys,'platform',platform),patch.object(msu2.time,'sleep'):
                display._write(data)
            self.assertEqual(b''.join(output),data)
            self.assertLessEqual(max(map(len,output)),60 if platform=='darwin' else len(data))
            if platform=='win32': self.assertEqual(len(output),1)

    def test_timeout_retries_only_idempotent_live_operation_once(self):
        device=msu2.Display('FAKE')
        operation=MagicMock(side_effect=[msu2.DeviceTimeout('timeout'),17])
        with patch.object(device,'recover') as recover:
            self.assertEqual(device.live_operation(operation),17)
            recover.assert_called_once()
        failing=MagicMock(side_effect=msu2.DeviceTimeout('timeout'))
        with patch.object(device,'recover') as recover:
            with self.assertRaises(msu2.DeviceTimeout):device.live_operation(failing)
            self.assertEqual(failing.call_count,2)
            recover.assert_called_once()


class AssetGuiTests(unittest.TestCase):
    setUp_base = test_features.GuiTests.setUp
    tearDown = test_features.GuiTests.tearDown
    complete = test_features.GuiTests.complete

    def setUp(self):
        self.setUp_base()
        self.ui.open_asset_editor()
        self.editor=self.ui.asset_editor
        self.source=Image.new('RGB',(160,80),'blue')
        ImageDraw.Draw(self.source).rectangle((0,0,20,20),fill='red')

    def test_preview_stays_upright_while_display_is_inverted(self):
        self.editor.set_image(self.source)
        self.ui.upside_down.set(True)
        self.editor.show()
        self.complete()
        self.assertEqual(self.display.frames[-1].tobytes(),self.source.transpose(Image.Transpose.ROTATE_180).tobytes())
        preview=ImageTk.getimage(self.editor.photo).convert('RGB')
        self.assertEqual(preview.tobytes(),self.source.resize(preview.size,Image.Resampling.NEAREST).tobytes())

    def test_imported_bin_can_be_reinterpreted_without_double_rotation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'image.bin'
            path.write_bytes(assets.oriented_bytes(self.source, True))
            with patch.object(assets_ui.filedialog, 'askopenfilename', return_value=str(path)):
                self.editor.import_image()
            self.editor.source_inverted.set(True)
            self.editor.reinterpret()
            self.assertEqual(self.editor.image.tobytes(), self.source.tobytes())
            self.editor.source_inverted.set(False)
            self.editor.reinterpret()
            self.assertEqual(self.editor.image.tobytes(),
                             self.source.transpose(Image.Transpose.ROTATE_180).tobytes())

    def test_cancel_write_leaves_device_and_data_unchanged(self):
        self.editor.set_image(self.source)
        with patch.object(assets_ui.messagebox,'askyesno',return_value=False),patch.object(assets_ui,'replace_asset') as replace:
            self.editor.upload()
            replace.assert_not_called()
        self.assertFalse(self.ui.busy)

    def test_asset_window_pauses_main_frames_and_button_polling(self):
        self.ui.pending_frame=True
        self.ui.hardware_button.set(True)
        self.ui.tick()
        self.assertEqual(self.display.calls,[])

    def test_clock_background_uses_upright_source(self):
        self.editor.set_image(self.source)
        self.ui.upside_down.set(True)
        self.editor.use_clock_background()
        self.assertEqual(self.ui.clock_background.tobytes(),self.source.tobytes())
        request=self.ui.frame_request()
        rendered=self.ui.render_request(request)
        self.assertEqual(rendered.getpixel((0,0)),(255,0,0))
        self.assertEqual(rendered.getpixel((159,0)),(0,0,255))

    def test_background_error_does_not_disconnect_screen(self):
        with patch.object(self.ui,'render_request',side_effect=features.ContentError('invalid background')):
            self.ui.send()
            self.complete()
        self.assertIs(self.ui.display,self.display)

    def test_idle_keepalive_runs_with_editor_and_buttons_disabled(self):
        self.display.last_activity=0
        # Match the real ping, which advances activity after a successful reply.
        # Tk can run another tick while complete() pumps events on a busy Mac.
        self.display.ping=MagicMock(side_effect=lambda: setattr(self.display,'last_activity',time.monotonic()))
        self.ui.tick()
        self.complete()
        self.display.ping.assert_called_once()
