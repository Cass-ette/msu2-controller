"""Permission recovery uses the Tk thread, preserves serial access and retries safely."""
import threading
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image

import app
import capture_ui
import features
import platform_support
import test_features


class PermissionPlatformTests(unittest.TestCase):
    def test_native_request_and_settings_are_mac_only(self):
        with patch.object(platform_support.sys, 'platform', 'win32'), \
             patch.object(platform_support.ctypes, 'CDLL') as load, \
             patch.object(platform_support.subprocess, 'run') as run:
            self.assertFalse(platform_support.request_screen_capture_access()['required'])
            self.assertFalse(platform_support.open_screen_capture_settings())
        load.assert_not_called()
        run.assert_not_called()

    def test_request_uses_native_prompt_only_when_needed_and_handles_missing_api(self):
        native = MagicMock()
        native.CGRequestScreenCaptureAccess.return_value = True
        with patch.object(platform_support, 'screen_capture_access', return_value={'required': True, 'granted': False}), \
             patch.object(platform_support.ctypes, 'CDLL', return_value=native):
            self.assertTrue(platform_support.request_screen_capture_access()['granted'])
            native.CGRequestScreenCaptureAccess.assert_called_once_with()
        with patch.object(platform_support, 'screen_capture_access', return_value={'required': True, 'granted': True}), \
             patch.object(platform_support.ctypes, 'CDLL') as load:
            self.assertTrue(platform_support.request_screen_capture_access()['granted'])
            load.assert_not_called()
        with patch.object(platform_support, 'screen_capture_access', return_value={'required': True, 'granted': False}), \
             patch.object(platform_support.ctypes, 'CDLL', side_effect=OSError('unavailable')):
            self.assertIsNone(platform_support.request_screen_capture_access()['granted'])

    def test_settings_deep_link_is_argument_not_shell_command(self):
        with patch.object(platform_support.sys, 'platform', 'darwin'), \
             patch.object(platform_support.subprocess, 'run') as run:
            self.assertTrue(platform_support.open_screen_capture_settings())
        args, kwargs = run.call_args
        self.assertEqual(args[0], ['/usr/bin/open', 'x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture'])
        self.assertTrue(kwargs['check'])
        self.assertNotIn('shell', kwargs)

    def test_permission_denial_is_typed_and_never_attempts_capture(self):
        with patch.object(features.sys, 'platform', 'darwin'), \
             patch.object(features, 'screen_capture_access', return_value={'granted': False}), \
             patch.object(features.ImageGrab, 'grab') as grab:
            with self.assertRaises(features.ScreenCapturePermissionError):
                features.capture_desktop((160, 80))
        grab.assert_not_called()

    def test_windows_capture_does_not_enter_mac_permission_flow(self):
        with patch.object(features.sys, 'platform', 'win32'), \
             patch.object(features, 'screen_capture_access') as access, \
             patch.object(features.ImageGrab, 'grab', return_value=Image.new('RGB', (320, 160))) as grab:
            self.assertEqual(features.capture_desktop((160, 80), '整个桌面').size, (160, 80))
        access.assert_not_called()
        grab.assert_called_once_with(all_screens=True)


class CapturePermissionGuiTests(unittest.TestCase):
    setUp = test_features.GuiTests.setUp
    tearDown = test_features.GuiTests.tearDown
    complete = test_features.GuiTests.complete

    def deny_capture(self, native):
        self.ui.mode.set('投屏')
        self.ui.live.set(True)
        with patch.object(self.ui, 'render_request', side_effect=features.ScreenCapturePermissionError('需要屏幕录制权限')), \
             patch.object(app, 'request_screen_capture_access', side_effect=native):
            self.ui.send()
            self.complete()
        return self.ui.capture_permission_window

    def test_denial_prompts_once_on_tk_thread_and_keeps_serial_available(self):
        requested = []

        def native():
            self.assertIs(threading.current_thread(), threading.main_thread())
            requested.append(True)
            return {'granted': False}

        window = self.deny_capture(native)
        self.assertIsNotNone(window)
        self.assertFalse(self.ui.live.get())
        self.assertFalse(self.ui.modal)
        self.assertIs(self.ui.display, self.display)
        self.assertFalse(self.display.closed)
        self.assertEqual(self.display.frames, [])
        self.ui.send()
        self.ui.send()
        self.assertIs(self.ui.capture_permission_window, window)
        self.assertFalse(self.ui.busy)
        window.close()
        self.deny_capture(native)
        self.assertEqual(len(requested), 1)
        # The help window must not prevent the single worker running keepalive.
        seen = []
        self.ui.run(lambda: seen.append('serial'), lambda _: None)
        self.complete()
        self.assertEqual(seen, ['serial'])

    def test_grant_in_native_prompt_resumes_without_showing_help(self):
        frame = Image.new('RGB', (160, 80), '#123456')
        self.ui.mode.set('投屏')
        self.ui.live.set(True)
        with patch.object(self.ui, 'render_request', side_effect=[features.ScreenCapturePermissionError(), frame]), \
             patch.object(app, 'request_screen_capture_access', return_value={'granted': True}) as native:
            self.ui.send()
            self.complete()
            self.assertIsNone(self.ui.capture_permission_window)
            self.assertTrue(self.ui.live.get())
            self.ui.live.set(False)
            self.ui.tick()
            self.complete()
        native.assert_called_once_with()
        self.assertEqual(len(self.display.frames), 1)

    def test_settings_and_retry_keep_help_until_permission_then_send_current_frame(self):
        window = self.deny_capture(lambda: {'granted': False})
        with patch.object(capture_ui, 'open_screen_capture_settings', return_value=True) as opened:
            window.open_settings()
        opened.assert_called_once_with()
        with patch.object(capture_ui, 'screen_capture_access', return_value={'granted': False}):
            window.retry()
        self.assertIs(self.ui.capture_permission_window, window)
        self.assertFalse(self.ui.busy)
        self.assertIn('尚未检测到', window.status.get())
        with patch.object(capture_ui, 'screen_capture_access', return_value={'granted': True}):
            window.retry()
        self.assertIsNone(self.ui.capture_permission_window)
        self.assertTrue(self.ui.live.get())
        self.assertTrue(self.ui.pending_frame)
        self.ui.live.set(False)
        with patch.object(self.ui, 'render_request', return_value=Image.new('RGB', (160, 80), 'green')):
            self.ui.tick()
            self.complete()
        self.assertEqual(len(self.display.frames), 1)
        self.assertIn('画面已发送', self.ui.status.get())

    def test_unavailable_settings_keeps_manual_route_and_can_cancel(self):
        window = self.deny_capture(lambda: {'granted': None})
        with patch.object(capture_ui, 'open_screen_capture_settings', side_effect=OSError('unavailable')):
            window.open_settings()
        self.assertIn('系统设置 → 隐私与安全性', window.status.get())
        window.close()
        self.assertIsNone(self.ui.capture_permission_window)
        self.assertFalse(self.ui.live.get())
        self.assertFalse(self.ui.pending_frame)
        self.assertIs(self.ui.display, self.display)

    def test_stale_permission_error_cannot_prompt_or_pause_new_mode(self):
        started, release = threading.Event(), threading.Event()

        def denied(_):
            started.set()
            if not release.wait(2):
                raise RuntimeError('test timeout')
            raise features.ScreenCapturePermissionError()

        self.ui.mode.set('投屏')
        self.ui.live.set(True)
        with patch.object(self.ui, 'render_request', side_effect=denied), \
             patch.object(app, 'request_screen_capture_access') as native:
            self.ui.send()
            try:
                self.assertTrue(started.wait(1))
                self.ui.mode.set('时钟')
                self.ui.mode_changed()
            finally:
                release.set()
            self.complete()
        native.assert_not_called()
        self.assertTrue(self.ui.live.get())
        self.assertIsNone(self.ui.capture_permission_window)

    def test_mode_change_closes_help_without_restoring_capture(self):
        self.deny_capture(lambda: {'granted': False})
        self.ui.mode.set('文字')
        self.ui.mode_changed()
        self.assertIsNone(self.ui.capture_permission_window)
        self.assertEqual(self.ui.mode.get(), '文字')
        self.assertFalse(self.ui.live.get())


if __name__ == '__main__':
    unittest.main(verbosity=2)
