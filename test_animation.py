"""Animation timeline, real file decoding, flash layout and GUI integration checks."""
from io import BytesIO
from pathlib import Path
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageTk

import animation
import animation_ui
import app
import features
import msu2
import test_features


def picture(index):
    frame = Image.new("RGB", animation.FRAME_SIZE, (index * 7 % 256, index * 13 % 256, index * 17 % 256))
    ImageDraw.Draw(frame).rectangle((1, 2, 28, 16), fill=(255, 0, index * 5 % 256))
    return frame


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_variable_gif_durations_and_short_loop(self):
        path = self.root / "timed.gif"
        red = Image.new("RGB", (16, 8), "red")
        green = Image.new("RGB", (16, 8), "green")
        red.save(path, save_all=True, append_images=[green], duration=[100, 300], loop=0)
        source = animation.load_gif(path)
        self.assertEqual(source.durations_ms, (100, 300))
        output = animation.normalize(source)
        self.assertEqual(len(output), 36)
        self.assertEqual([frame.getpixel((80, 40)) for frame in output[:5]],
                         [(255, 0, 0), (0, 128, 0), (0, 128, 0), (0, 128, 0), (255, 0, 0)])

    def test_gif_disposal_and_transparency_are_composited(self):
        path = self.root / "disposal.gif"
        palette = [0, 0, 0, 255, 0, 0, 0, 0, 255, 0, 255, 0] + [0] * (768 - 12)
        frames = [Image.new("P", (8, 4), 0) for _ in range(3)]
        for frame in frames:
            frame.putpalette(palette)
        ImageDraw.Draw(frames[0]).rectangle((0, 0, 3, 3), fill=1)
        ImageDraw.Draw(frames[1]).rectangle((4, 0, 7, 3), fill=2)
        ImageDraw.Draw(frames[2]).rectangle((0, 0, 7, 1), fill=3)
        frames[0].save(path, save_all=True, append_images=frames[1:], duration=100,
                       transparency=0, disposal=[1, 2, 1], optimize=False)
        source = animation.load_gif(path, background="#112233")
        self.assertEqual(len(source.frames), 3)
        self.assertEqual(source.frames[0].getpixel((140, 60)), (17, 34, 51))
        self.assertEqual(source.frames[1].getpixel((20, 60)), (255, 0, 0))
        self.assertEqual(source.frames[1].getpixel((140, 60)), (0, 0, 255))
        self.assertEqual(source.frames[2].getpixel((140, 60)), (17, 34, 51))
        self.assertEqual(source.frames[2].getpixel((80, 10)), (0, 255, 0))

    def test_sequence_uses_numeric_filename_order(self):
        paths = []
        for index in (10, 2, 0, 1):
            path = self.root / f"A{index}.png"
            Image.new("RGB", (160, 80), (index, 0, 0)).save(path)
            paths.append(path)
        source = animation.load_sequence(paths)
        self.assertEqual([frame.getpixel((0, 0))[0] for frame in source.frames], [0, 1, 2, 10])
        self.assertEqual(source.durations_ms, (100, 100, 100, 100))

    def test_fitting_letterbox_crop_and_alpha_background(self):
        image = Image.new("RGBA", (80, 80), (255, 0, 0, 128))
        fitted = animation.fit_frame(image, background="#0000ff")
        self.assertEqual(fitted.size, (160, 80))
        self.assertEqual(fitted.getpixel((0, 40)), (0, 0, 255))
        self.assertEqual(fitted.getpixel((80, 40)), (128, 0, 127))
        cropped = animation.fit_frame(image, "居中裁剪", "#0000ff")
        self.assertEqual(cropped.getpixel((0, 40)), (128, 0, 127))

    def test_zero_duration_gif_defaults_to_100ms(self):
        path = self.root / "still.gif"
        Image.new("RGB", (16, 8), "red").save(path, duration=0)
        self.assertEqual(animation.load_gif(path).durations_ms, (100,))

    def test_invalid_files_and_too_many_frames_are_rejected(self):
        path = self.root / "picture.png"
        Image.new("RGB", (16, 8), "red").save(path)
        with self.assertRaisesRegex(ValueError, "GIF"):
            animation.load_gif(path)
        with self.assertRaises(ValueError):
            animation.load_sequence([])
        with self.assertRaises(ValueError):
            animation.load_sequence([path] * (animation.MAX_SOURCE_FRAMES + 1))
        demo = Path(__file__).parent / "examples" / "offline-demo.gif"
        with patch.object(animation, "MAX_SOURCE_FRAMES", 2):
            with self.assertRaisesRegex(ValueError, "最多支持"):
                animation.load_gif(demo)


class TimelineTests(unittest.TestCase):
    def test_long_clip_can_be_cropped_or_fully_resampled(self):
        source = animation.AnimationSource(tuple(picture(i) for i in range(36)), (200,) * 36, "long")
        cropped = animation.normalize(source, animation.LONG_MODES[0])
        compressed = animation.normalize(source, animation.LONG_MODES[1])
        self.assertEqual(cropped[-1].tobytes(), source.frames[17].tobytes())
        for index, frame in enumerate(compressed):
            self.assertEqual(frame.tobytes(), source.frames[index].tobytes())

    def test_exact_36_frame_sequence_is_unchanged_and_copied(self):
        source = animation.AnimationSource(tuple(picture(i) for i in range(36)), (100,) * 36, "exact")
        output = animation.normalize(source)
        for before, after in zip(source.frames, output):
            self.assertEqual(before.tobytes(), after.tobytes())
            self.assertIsNot(before, after)
        output[0].putpixel((0, 0), (255, 255, 255))
        self.assertNotEqual(output[0].getpixel((0, 0)), source.frames[0].getpixel((0, 0)))

    def test_invalid_timeline_is_rejected(self):
        for source in (
                animation.AnimationSource((), (), "empty"),
                animation.AnimationSource((picture(0),), (), "missing"),
                animation.AnimationSource((picture(0),), (0,), "zero"),
                animation.AnimationSource((Image.new("RGB", (80, 80)),), (100,), "wrong size")):
            with self.subTest(source=source.label), self.assertRaises(ValueError):
                animation.normalize(source)


class FlashSimulator(msu2.Display):
    """Decode the real emitted staging packets into a 1 MiB simulated flash."""
    def __init__(self):
        super().__init__("SIMULATED")
        self.serial = object()
        self.info = msu2.DeviceInfo(self.port, "01", 160, 80)
        self.flash = bytearray(b"\xa5" * (1024 * 1024))
        self.staging = bytearray()
        self.commands = []
        self.reads = []
        self.shown = []

    def _write(self, data):
        assert len(data) == 384
        staging = bytearray()
        for slot in range(64):
            packet = data[slot * 6:(slot + 1) * 6]
            assert packet[:2] == bytes((4, slot))
            staging.extend(packet[2:])
        self.staging = staging

    def _exchange(self, packet, timeout=2):
        self.commands.append(bytes(packet))
        assert len(packet) == 6
        if packet[:2] == bytes((3, 2)):
            page, count = struct.unpack(">HH", packet[2:])
            assert page == 0 and count == 3600
            self.flash[:count * 256] = b"\xff" * (count * 256)
        else:
            assert packet[:2] == bytes((3, 3)) and packet[5] == 1
            page = int.from_bytes(packet[2:5], "big")
            assert 0 <= page < 3600
            self.flash[page * 256:(page + 1) * 256] = self.staging
        return packet

    def read_flash_byte(self, address):
        self.reads.append(address)
        return self.flash[address]

    def show(self, image):
        self.shown.append(image.copy())


class FlashTests(unittest.TestCase):
    def test_all_frames_are_distinct_correct_and_later_assets_untouched(self):
        device = FlashSimulator()
        frames = tuple(picture(i) for i in range(36))
        progress = []
        result = device.burn_offline_frames(frames, progress.append)
        expected = b"".join(msu2.image_bytes(frame) for frame in frames)
        self.assertEqual(device.flash[:len(expected)], expected)
        self.assertEqual(device.flash[len(expected):], b"\xa5" * (1024 * 1024 - len(expected)))
        self.assertEqual(len(device.commands), 3601)
        self.assertEqual(len(device.reads), 288)
        self.assertEqual(result, {"frames": 36, "pages": 3600, "sampled_bytes": 288})
        self.assertEqual(device.shown[0].tobytes(), frames[0].tobytes())
        self.assertIn("36/36", " ".join(progress))

    def test_invalid_last_frame_or_count_never_erases_flash(self):
        valid = [picture(0)] * 36
        invalid = valid[:-1] + [Image.new("RGB", (80, 160))]
        for frames in (valid[:-1], valid + valid[:1], invalid):
            device = FlashSimulator()
            with self.assertRaises(ValueError):
                device.burn_offline_frames(frames)
            self.assertEqual(device.commands, [])
            self.assertEqual(device.flash[:256], b"\xa5" * 256)

    def test_unsupported_screen_never_erases(self):
        device = FlashSimulator()
        device.info = msu2.DeviceInfo("SIMULATED", "02", 160, 80)
        with self.assertRaises(msu2.DeviceError):
            device.burn_offline_frames([picture(0)] * 36)
        self.assertEqual(device.commands, [])

    def test_static_api_still_repeats_the_same_image(self):
        device = FlashSimulator()
        frame = picture(12)
        device.burn_offline(frame)
        self.assertEqual(device.flash[:921600], msu2.image_bytes(frame) * 36)

    def test_sample_failure_does_not_report_success_or_show(self):
        device = FlashSimulator()
        with patch.object(device, "read_flash_byte", return_value=255):
            with self.assertRaisesRegex(msu2.DeviceError, "校验失败"):
                device.burn_offline_frames([Image.new("RGB", (160, 80), "black")] * 36)
        self.assertEqual(device.shown, [])

    def test_write_failure_stops_at_failed_page(self):
        device = FlashSimulator()
        exchange = device._exchange

        def fail(packet, timeout=2):
            if packet[:2] == bytes((3, 3)) and int.from_bytes(packet[2:5], "big") == 7:
                raise msu2.DeviceError("write failed")
            return exchange(packet, timeout)
        with patch.object(device, "_exchange", side_effect=fail):
            with self.assertRaisesRegex(msu2.DeviceError, "write failed"):
                device.burn_offline_frames([picture(0)] * 36)
        self.assertEqual(len(device.commands), 8)
        self.assertEqual(device.reads, [])
        self.assertEqual(device.shown, [])


class AnimationGuiTests(unittest.TestCase):
    setUp_base = test_features.GuiTests.setUp
    tearDown = test_features.GuiTests.tearDown
    complete = test_features.GuiTests.complete

    def setUp(self):
        self.setUp_base()
        self.display.info = msu2.DeviceInfo("SIMULATED", "01", 160, 80)
        self.animation_burns = []

        def burn_frames(frames, progress=None):
            self.display.enter("burn_frames")
            try:
                if self.display.gate:
                    assert self.display.gate.wait(3)
                self.animation_burns.append(tuple(frame.copy() for frame in frames))
                if progress:
                    progress("simulated animation burn")
                return {"frames": 36}
            finally:
                self.display.in_call = False
        self.display.burn_offline_frames = burn_frames
        self.ui.open_animation_editor()
        self.editor = self.ui.animation_editor

    def load_demo(self):
        self.editor.load_demo()
        self.complete()
        self.assertEqual(len(self.editor.frames), 36)
        self.editor.playing = False
        self.editor.seek(0)

    def test_preview_stays_upright_and_every_burned_frame_is_rotated(self):
        self.load_demo()
        self.ui.upside_down.set(True)
        before = ImageTk.getimage(self.editor.photo).convert("RGB")
        expected = self.editor.frames[0].resize(before.size, Image.Resampling.NEAREST)
        self.assertEqual(before.tobytes(), expected.tobytes())
        with patch.object(animation_ui.messagebox, "askyesno", return_value=True):
            self.editor.burn()
            self.complete()
        self.assertEqual(len(self.animation_burns), 1)
        for upright, burned in zip(self.editor.frames, self.animation_burns[0]):
            self.assertEqual(burned.tobytes(), upright.transpose(Image.Transpose.ROTATE_180).tobytes())
        self.assertEqual(ImageTk.getimage(self.editor.photo).convert("RGB").tobytes(), expected.tobytes())
        self.assertFalse(self.editor.working)

    def test_cancel_does_not_start_a_burn(self):
        self.load_demo()
        with patch.object(animation_ui.messagebox, "askyesno", return_value=False):
            self.editor.burn()
        self.assertEqual(self.display.calls, [])
        self.assertFalse(self.ui.busy)
        self.assertFalse(self.editor.working)

    def test_backup_restores_stored_direction_and_exact_pixels(self):
        import offline_backup
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'animation.msu2animation'
            for inverted in (False, True):
                with self.subTest(inverted=inverted):
                    originals = tuple(picture(index) for index in range(36))
                    stored = tuple(frame.transpose(Image.Transpose.ROTATE_180)
                                   if inverted else frame for frame in originals)
                    data = b''.join(msu2.image_bytes(frame) for frame in stored)
                    offline_backup.save_animation_backup(path, data)
                    self.ui.upside_down.set(not inverted)
                    self.editor.load('backup-inverted' if inverted else 'backup', (str(path),))
                    self.complete()
                    self.assertEqual(self.ui.upside_down.get(), inverted)
                    self.editor.playing = False
                    self.animation_burns.clear()
                    with patch.object(animation_ui.messagebox, 'askyesno', return_value=True):
                        self.editor.burn()
                        self.complete()
                    self.assertEqual(b''.join(msu2.image_bytes(frame)
                                             for frame in self.animation_burns[0]), data)

    def test_bad_import_keeps_previous_clip_and_serial_connection(self):
        self.load_demo()
        previous = self.editor.frames
        self.ui.live.set(True)
        self.editor.load("gif", ("this-file-does-not-exist.gif",))
        self.complete()
        self.assertIs(self.editor.frames, previous)
        self.assertIs(self.ui.display, self.display)
        self.assertFalse(self.display.closed)
        self.assertTrue(self.ui.live.get())
        self.assertIn("动画导入失败", self.ui.status.get())
        self.assertFalse(self.editor.working)

    def test_editor_pauses_host_scheduler_and_burn_serializes_all_io(self):
        self.load_demo()
        self.ui.live.set(True)
        self.ui.hardware_button.set(True)
        self.ui.button_detector = features.ButtonDebouncer(1000)
        self.ui.pending_frame = True
        self.ui.tick()
        self.assertEqual(self.display.calls, [])
        gate = threading.Event()
        self.display.gate = gate
        with patch.object(animation_ui.messagebox, "askyesno", return_value=True):
            self.editor.burn()
        deadline = time.monotonic() + 1
        while not self.display.calls and time.monotonic() < deadline:
            time.sleep(.005)
        self.editor.close()
        self.assertFalse(self.editor.closed)
        self.ui.tick()
        self.assertEqual(self.display.calls, ["burn_frames"])
        gate.set()
        self.complete()
        self.editor.close()
        self.assertTrue(self.editor.closed)
        self.assertIsNone(self.ui.animation_editor)

    def test_play_pause_seek_and_loop_use_fixed_100ms_frames(self):
        self.load_demo()
        self.editor.seek(10)
        self.assertEqual(self.editor.index, 10)
        self.assertFalse(self.editor.playing)
        with patch.object(animation_ui.time, "monotonic", return_value=100.0):
            self.editor.toggle_play()
        self.assertTrue(self.editor.playing)
        with patch.object(animation_ui.time, "monotonic", return_value=103.65):
            self.editor.tick()
        self.assertEqual(self.editor.index, 10)
        self.editor.seek(35)
        self.assertEqual(self.editor.frame_text.get(), "第 36 / 36 帧")

    def test_no_connected_screen_does_not_open_burn_confirmation(self):
        self.load_demo()
        self.ui.display = None
        with patch.object(animation_ui.messagebox, "askyesno") as confirm:
            self.editor.burn()
        confirm.assert_not_called()
        self.assertEqual(self.animation_burns, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
