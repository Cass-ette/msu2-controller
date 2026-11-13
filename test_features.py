"""Regression checks for live content, physical buttons and serialized updates."""
import threading
import time
import tkinter as tk
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageTk

import app
import features
from msu2 import DeviceInfo, Display


class FeatureTests(unittest.TestCase):
    def test_button_noise_long_hold_and_release(self):
        button = features.ButtonDebouncer(1000)
        samples = [(0,1000),(.1,990),(.2,875),(.3,1000),
                   (.4,200),(.41,1000),(.42,200),(.46,200),
                   (.8,200),(1.5,200),(1.6,1000),(1.61,200),
                   (1.62,1000),(1.66,1000),(1.8,1000),
                   (2,200),(2.04,200),(2.2,1000),(2.24,1000)]
        fired = [now for now,value in samples if button.update(value,now)]
        self.assertEqual(fired,[1.66,2.24])

    def test_button_recovers_from_held_connection(self):
        button = features.ButtonDebouncer(100)
        self.assertFalse(button.update(1000,0))
        self.assertFalse(button.update(200,.1))
        self.assertFalse(button.update(200,.14))
        self.assertFalse(button.update(1000,.2))
        self.assertTrue(button.update(1000,.24))

    def test_adc_packet_and_reply(self):
        display = Display("SIMULATED")
        display.serial = object()
        display.info = DeviceInfo("SIMULATED","01",160,80)
        with patch.object(display,"_exchange",return_value=bytes((8,9,0,0,0x0a,0xbc))) as exchange:
            self.assertEqual(display.read_adc(9),0x0abc)
            exchange.assert_called_once_with(bytes((8,9,0,0,0,0)))
        with self.assertRaises(ValueError):
            display.read_adc(16)

    def test_system_rates_use_elapsed_time_and_handle_resets(self):
        monitor = features.SystemMonitor()
        memory = SimpleNamespace(percent=42,used=4,total=10)
        networks = [SimpleNamespace(bytes_sent=a,bytes_recv=b)
                    for a,b in ((100,200),(500,1000),(10,20))]
        with patch.object(features.psutil,"cpu_percent",return_value=17), \
             patch.object(features.psutil,"virtual_memory",return_value=memory), \
             patch.object(features.psutil,"net_io_counters",side_effect=networks), \
             patch.object(features.time,"monotonic",side_effect=(10,12,14)):
            first,second,third = [monitor.sample() for _ in range(3)]
        self.assertIsNone(first["upload"])
        self.assertEqual(second["upload"],200)
        self.assertEqual(second["download"],400)
        self.assertEqual(third["upload"],0)
        self.assertEqual(third["download"],0)
        self.assertEqual(second["memory"],42)
        self.assertEqual(second["cpu"],17)

    def test_capture_scope_and_fit_preserve_size(self):
        source = Image.new("RGB",(200,200),"red")
        with patch.object(features.sys,"platform","win32"), \
             patch.object(features.ImageGrab,"grab",return_value=source) as grab:
            full = features.capture_desktop((160,80),"整个桌面","完整显示","#000000")
            grab.assert_called_once_with(all_screens=True)
            self.assertEqual(full.size,(160,80))
            self.assertEqual(full.getpixel((0,40)),(0,0,0))
            self.assertEqual(full.getpixel((80,40)),(255,0,0))
            crop = features.capture_desktop((160,80),"主屏幕","居中裁剪")
            self.assertEqual(grab.call_args.kwargs,{"all_screens":False})
            self.assertEqual(crop.getpixel((0,0)),(255,0,0))
        with patch.object(features.ImageGrab,"grab",side_effect=OSError("blocked")):
            with self.assertRaises(features.ContentError):
                features.capture_desktop((160,80))


class FakeDisplay:
    def live_operation(self, operation):
        return operation()

    def __init__(self,gate=None):
        self.frames = []
        self.burns = []
        self.samples = deque([1000]*100)
        self.closed = False
        self.calls = []
        self.in_call = False
        self.gate = gate

    def enter(self,kind):
        assert not self.in_call,"overlapping serial operations"
        assert threading.current_thread() is not threading.main_thread(),"serial ran on UI thread"
        self.in_call = True
        self.calls.append(kind)

    def connect(self):
        self.enter("connect")
        try:
            return DeviceInfo("SIMULATED","01",160,80)
        finally:
            self.in_call = False

    def read_adc(self,channel):
        self.enter("adc")
        try:
            assert channel == 9
            time.sleep(.002)
            return self.samples.popleft() if self.samples else 1000
        finally:
            self.in_call = False

    def show(self,image):
        self.enter("show")
        try:
            time.sleep(.01)
            self.frames.append(image.copy())
        finally:
            self.in_call = False

    def burn_offline(self,image,progress=None):
        self.enter("burn")
        try:
            if self.gate:
                assert self.gate.wait(3)
            self.burns.append(image.copy())
            if progress:
                progress("test progress")
            return {"frames":36}
        finally:
            self.in_call = False

    def close(self):
        self.closed = True


class GuiTests(unittest.TestCase):
    def setUp(self):
        # GUI tests isolate their fake display; backup safety is exercised by
        # the byte-level flash simulator in test_shared_features.
        def simulated_burn(device, frames, directory, progress=None, cancelled=None):
            if hasattr(device, 'burn_offline_frames'):
                return device.burn_offline_frames(frames, progress)
            return device.burn_offline(frames[0], progress)
        import animation_ui
        for module in (app, animation_ui):
            patcher = patch.object(module, 'safe_burn', side_effect=simulated_burn)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.root = tk.Tk()
        self.root.withdraw()
        self.ui = app.App(self.root)
        self.root.update_idletasks()
        # Make the tests schedule deterministic; individual cases pump actual timers.
        for identifier in self.root.tk.call("after","info"):
            self.root.after_cancel(identifier)
        self.ui.live.set(False)
        self.ui.hardware_button.set(False)
        self.ui.poll()
        self.display = FakeDisplay()
        self.ui.display = self.display
        self.ui.image_path = str(Path(__file__).with_name("preview.png"))

    def tearDown(self):
        self.ui.live.set(False)
        self.ui.hardware_button.set(False)
        self.ui.pending_frame = False
        self.ui.executor.shutdown(wait=True,cancel_futures=True)
        if not self.ui.closing:
            self.ui.busy = False
            self.ui.close()

    def complete(self):
        deadline = time.monotonic()+4
        while self.ui.busy and time.monotonic()<deadline:
            self.root.update()
            time.sleep(.005)
        self.assertFalse(self.ui.busy)

    def test_modes_skip_missing_image_and_sync(self):
        self.ui.image_path = ""
        seen = []
        for _ in range(6):
            self.ui.next_mode()
            seen.append(self.ui.mode.get())
        self.assertEqual(seen,["时钟","电脑状态","功率与电池","投屏","测试色条","文字"])
        self.ui.image_path = "selected.png"
        self.ui.mode.set("电脑状态")
        self.ui.next_mode()
        self.assertEqual(self.ui.mode.get(),"功率与电池")
        self.ui.next_mode()
        self.assertEqual(self.ui.mode.get(),"图片")

    def test_sampled_button_release_drives_mode_once(self):
        self.ui.button_detector = features.ButtonDebouncer(1000)
        for stamp,value in [(0,1000),(.1,200),(.15,200),(.8,200),(.9,1000),(.95,1000),(1.1,1000)]:
            self.ui.button_polled((value,stamp))
        self.assertEqual(self.ui.mode.get(),"时钟")
        self.assertTrue(self.ui.pending_frame)

    def test_upright_preview_and_oriented_live_and_offline_frames(self):
        source = Image.new("RGB",(160,80),"#112233")
        ImageDraw.Draw(source).rectangle((1,2,38,17),fill="red")
        self.ui.upside_down.set(True)
        for mode in app.MODES:
            self.ui.mode.set(mode)
            with patch.object(self.ui,"render_request",return_value=source):
                self.ui.send()
                self.complete()
            self.assertEqual(self.display.frames[-1].tobytes(),
                             source.transpose(Image.Transpose.ROTATE_180).tobytes())
            preview = ImageTk.getimage(self.ui.photo).convert("RGB")
            expected = source.resize(preview.size,Image.Resampling.NEAREST)
            self.assertEqual(preview.tobytes(),expected.tobytes())
            with patch.object(app.messagebox,"askyesno",return_value=True):
                self.ui.burn_offline()
                self.complete()
            # Static modes re-render from the editor before burning; dynamic modes use the captured frame.
            upright = self.ui.image()
            self.assertEqual(self.display.burns[-1].tobytes(),
                             upright.transpose(Image.Transpose.ROTATE_180).tobytes())
        self.ui.upside_down.set(False)
        self.ui.mode.set("电脑状态")
        with patch.object(self.ui,"render_request",return_value=source):
            self.ui.send()
            self.complete()
        self.assertEqual(self.display.frames[-1].tobytes(),source.tobytes())

    def test_power_query_failure_keeps_serial_connection_and_live_updates(self):
        from power_monitor import PowerSample
        self.ui.mode.set('功率与电池')
        self.ui.live.set(True)
        with patch.object(self.ui.power_monitor, 'snapshot', return_value=PowerSample(10, state='采集失败', error='unavailable')):
            self.ui.send()
            self.complete()
        self.assertIs(self.ui.display, self.display)
        self.assertFalse(self.display.closed)
        self.assertTrue(self.ui.live.get())
        self.assertEqual(len(self.display.frames), 1)

    def test_power_details_are_read_only_and_do_not_pause_serial(self):
        from power_monitor import PowerSample
        self.ui.power_monitor.latest = PowerSample(time.monotonic(), system_w=20, input_w=50, battery_w=30)
        with patch.object(self.ui.power_monitor, 'snapshot', return_value=self.ui.power_monitor.latest):
            self.ui.open_power_window()
            window = self.ui.power_window
            self.ui.open_power_window()
            self.assertIs(self.ui.power_window, window)
            self.assertFalse(self.ui.modal)
            self.assertEqual(self.display.calls, [])
            window.show_on_screen()
            self.complete()
            self.assertEqual(self.ui.mode.get(), '功率与电池')
            self.assertTrue(self.ui.live.get())
            self.assertEqual(len(self.display.frames), 1)
            window.close()
        self.assertIsNone(self.ui.power_window)

    def test_capture_error_keeps_device_and_pauses_live(self):
        self.ui.mode.set("投屏")
        self.ui.live.set(True)
        with patch.object(self.ui,"render_request",side_effect=features.ContentError("screen denied")):
            self.ui.send()
            self.complete()
        self.assertIs(self.ui.display,self.display)
        self.assertFalse(self.display.closed)
        self.assertFalse(self.ui.live.get())
        self.assertIn("screen denied",self.ui.status.get())

    def test_stale_capture_is_discarded(self):
        started,release = threading.Event(),threading.Event()
        def slow_capture(request):
            started.set()
            self.assertTrue(release.wait(2))
            return Image.new("RGB",(160,80),"red")
        self.ui.mode.set("投屏")
        with patch.object(self.ui,"render_request",side_effect=slow_capture):
            self.ui.send()
            self.assertTrue(started.wait(1))
            self.ui.mode.set("文字")
            self.ui.mode_changed()
            release.set()
            self.complete()
        self.assertEqual(self.display.frames,[])
        self.assertTrue(self.ui.pending_frame)

    def test_burn_blocks_button_and_frame_operations(self):
        gate = threading.Event()
        self.display.gate = gate
        self.ui.mode.set("文字")
        self.ui.hardware_button.set(True)
        self.ui.button_detector = features.ButtonDebouncer(1000)
        with patch.object(app.messagebox,"askyesno",return_value=True):
            self.ui.burn_offline()
        deadline = time.monotonic()+1
        while "burn" not in self.display.calls and time.monotonic()<deadline:
            time.sleep(.005)
        for _ in range(5):
            self.ui.tick()
        self.assertEqual(self.display.calls,["burn"])
        gate.set()
        self.complete()

    def test_live_scheduler_polls_buttons_and_sends_frames(self):
        self.ui.mode.set("投屏")
        self.ui.live.set(True)
        self.ui.hardware_button.set(True)
        self.ui.button_detector = features.ButtonDebouncer(1000)
        frame = Image.new("RGB",(160,80),"blue")
        with patch.object(self.ui,"render_request",return_value=frame):
            self.ui.tick()
            deadline = time.monotonic()+.65
            while time.monotonic()<deadline:
                self.root.update()
                time.sleep(.005)
            self.ui.live.set(False)
            self.ui.hardware_button.set(False)
            self.complete()
        self.assertGreaterEqual(self.display.calls.count("adc"),2)
        self.assertGreaterEqual(self.display.calls.count("show"),2)

    def test_connection_calibrates_button_before_first_frame(self):
        self.ui.display = None
        self.ui.port.set("SIMULATED")
        with patch.object(app,"Display",return_value=self.display):
            self.ui.toggle_connection()
            self.complete()
        self.assertIs(self.ui.display,self.display)
        self.assertEqual(self.ui.button_detector.baseline,1000)
        self.assertEqual(self.display.calls,["connect"]+["adc"]*5)
        self.assertTrue(self.ui.pending_frame)

    def test_serial_failure_disconnects_device(self):
        self.ui.mode.set("文字")
        with patch.object(self.display,"show",side_effect=OSError("USB unplugged")):
            self.ui.send()
            self.complete()
        self.assertIsNone(self.ui.display)
        self.assertTrue(self.display.closed)
        self.assertIn("USB unplugged",self.ui.status.get())

    def test_close_waits_without_starting_new_work(self):
        started,release = threading.Event(),threading.Event()
        def work():
            started.set()
            release.wait(2)
        self.ui.run(work,lambda result:None)
        self.assertTrue(started.wait(1))
        self.ui.close()
        self.assertTrue(self.ui.close_requested)
        self.ui.pending_frame = True
        self.ui.tick()
        release.set()
        self.complete()
        self.ui.close()
        self.assertTrue(self.ui.closing)


if __name__ == "__main__":
    unittest.main(verbosity=2)
