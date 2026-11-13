"""Regression checks for frame fairness, cadence and cancelled notifications."""
from contextlib import contextmanager
import threading
import unittest
from unittest.mock import patch

from PIL import Image

import app
from features import ButtonDebouncer
import test_features


class GuiSchedulerTests(unittest.TestCase):
    setUp = test_features.GuiTests.setUp
    tearDown = test_features.GuiTests.tearDown
    complete = test_features.GuiTests.complete

    @contextmanager
    def controlled_worker(self):
        """Exercise public dispatch methods without wall-clock or USB timing."""
        now = [100.0]
        calls = []
        frame = Image.new('RGB', (160, 80), '#123456')
        self.display.last_activity = now[0]

        def read_adc(channel):
            self.assertEqual(channel, 9)
            calls.append(('adc', now[0]))
            self.display.last_activity = now[0]
            return 1000

        def show(image):
            calls.append(('frame', now[0]))
            self.display.frames.append(image.copy())
            self.display.last_activity = now[0]

        def execute(operation, done, failed=None, **kwargs):
            self.assertFalse(self.ui.busy, 'overlapping scheduler jobs')
            self.ui.busy = True
            try:
                result = operation()
            except Exception as error:
                if failed is None:
                    raise
                failed(error)
            else:
                done(result)
            finally:
                self.ui.busy = False
            return True

        with patch.object(app.time, 'monotonic', side_effect=lambda: now[0]), \
             patch.object(self.ui, 'run', side_effect=execute), \
             patch.object(self.ui, 'render_request', return_value=frame), \
             patch.object(self.display, 'read_adc', side_effect=read_adc), \
             patch.object(self.display, 'show', side_effect=show):
            yield now, calls

    def test_delayed_button_polls_do_not_starve_overdue_live_frames(self):
        self.ui.mode.set('投屏')
        self.ui.screen_fps.set('10')
        self.ui.live.set(True)
        self.ui.hardware_button.set(True)
        self.ui.button_detector = ButtonDebouncer(1000)
        self.ui.pending_frame = True
        self.ui.next_frame_at = 100.0
        self.ui.next_button_at = 100.0
        with self.controlled_worker() as (now, calls):
            # If the UI gets an opportunity only every 50 ms, a 30 ms button
            # deadline is always overdue too. Both kinds must still progress.
            for index in range(20):
                now[0] = 100.0 + index * .05
                self.ui.tick()
        self.assertGreaterEqual(sum(kind == 'frame' for kind, _ in calls), 4)
        self.assertGreaterEqual(sum(kind == 'adc' for kind, _ in calls), 4)

    def test_small_dispatch_delays_do_not_accumulate_frame_cadence_drift(self):
        self.ui.mode.set('投屏')
        self.ui.screen_fps.set('10')
        self.ui.live.set(True)
        self.ui.pending_frame = False
        self.ui.next_frame_at = 100.0
        with self.controlled_worker() as (now, _):
            for index in range(5):
                now[0] = 100.0 + index * .1 + .007
                self.ui.request_frame(send_to_device=True)
                self.assertAlmostEqual(self.ui.next_frame_at,
                                       100.0 + (index + 1) * .1, places=7)

    def test_missed_frame_slots_are_skipped_without_a_catchup_burst(self):
        self.ui.mode.set('投屏')
        self.ui.screen_fps.set('10')
        self.ui.live.set(True)
        self.ui.pending_frame = False
        self.ui.next_frame_at = 100.1
        with self.controlled_worker() as (now, calls):
            now[0] = 100.347
            self.ui.request_frame(send_to_device=True)
            self.assertAlmostEqual(self.ui.next_frame_at, 100.4, places=7)
            now[0] = 100.399
            self.ui.tick()
            self.assertEqual(sum(kind == 'frame' for kind, _ in calls), 1)

    def test_notification_dismissed_during_render_is_never_sent(self):
        record = self.ui.notification_inbox.submit(
            {'source': 'test', 'title': 'cancel during rendering', 'duration': 2})
        started, release = threading.Event(), threading.Event()
        obsolete = Image.new('RGB', (160, 80), '#fd10a8')

        def render(*args):
            started.set()
            if not release.wait(2):
                raise RuntimeError('test render gate timeout')
            return obsolete

        with patch.object(app, 'render_notification', side_effect=render):
            self.ui.tick()
            try:
                self.assertTrue(started.wait(1))
                self.ui.dismiss_notification()
            finally:
                release.set()
            self.complete()
        self.assertIsNone(self.ui.active_notification)
        self.assertEqual(self.ui.notification_inbox.get(record['id'])['status'], 'dismissed')
        self.assertIs(self.ui.display, self.display)
        self.assertFalse(any(frame.tobytes() == obsolete.tobytes()
                             for frame in self.display.frames))

    def test_repeated_wakeups_keep_only_one_dispatch_and_one_poll_timer(self):
        for _ in range(10):
            self.ui.tick()
            self.ui.poll()
        self.assertEqual(len(self.root.tk.call('after', 'info')), 2)

    def test_completion_can_close_the_window_without_rearming_timers(self):
        self.ui.run(lambda: None, lambda _: self.ui.close())
        self.complete()
        self.assertTrue(self.ui.closing)
        self.assertIsNone(self.ui._tick_after)


if __name__ == '__main__':
    unittest.main(verbosity=2)
