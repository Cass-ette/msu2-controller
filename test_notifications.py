"""Notification contract, real local HTTP, scheduling and output isolation."""
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from PIL import Image, ImageTk

import app
import features
from notification_api import NotificationServer
from notifications import Notification, NotificationInbox, NotificationQueueFull
from notify_client import NotificationClient, NotificationClientError
import test_features


class InboxTests(unittest.TestCase):
    def test_invalid_payload_never_enters_queue(self):
        inbox = NotificationInbox()
        for payload in (None, [], {}, {'source':'custom','title':'ok','path':'file.bin'},
                        {'source':'a','title':'x','duration':True},
                        {'source':'a','title':'x','ttl':float('inf')},
                        {'source':'a','title':'x','kind':'unknown'},
                        {'source':'a','title':'x','message':'x'*201}):
            with self.subTest(payload=payload):
                with self.assertRaises((ValueError, TypeError)):
                    inbox.submit(payload)
        self.assertEqual(inbox.status()['queued'], 0)

    def test_fifo_expiry_and_bounded_history(self):
        now = [0]
        inbox = NotificationInbox(capacity=2, history=3, clock=lambda:now[0])
        expired = inbox.submit({'source':'custom','title':'old','ttl':1})
        second = inbox.submit({'source':'custom','title':'second','ttl':20})
        with self.assertRaises(NotificationQueueFull):
            inbox.submit({'source':'custom','title':'overflow'})
        now[0] = 2
        self.assertEqual(inbox.get(expired['id'])['status'], 'expired')
        third = inbox.submit({'source':'arbitrary app','title':'third'})
        self.assertEqual(inbox.take()[0],second['id'])
        inbox.mark(second['id'],'completed')
        inbox.submit({'source':'another','title':'fourth'})
        self.assertIsNone(inbox.get(expired['id']))
        self.assertEqual(inbox.take()[0],third['id'])

    def test_concurrent_producers_observe_capacity(self):
        inbox = NotificationInbox(capacity=4)
        def submit(index):
            try:
                return inbox.submit({'source':str(index),'title':'message'})['id']
            except NotificationQueueFull:
                return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(submit,range(30)))
        self.assertEqual(len([value for value in results if value]),4)
        self.assertEqual(inbox.status()['queued'],4)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.discovery = Path(self.directory.name)/'api.json'
        self.inbox = NotificationInbox(capacity=1)
        self.server = NotificationServer(self.inbox, self.discovery).start()
        self.addCleanup(self.server.stop)
        self.client = NotificationClient(self.discovery)

    def request(self, method, path, payload=None, headers=None):
        doc = self.server.document
        connection = http.client.HTTPConnection('127.0.0.1',self.server.server.server_port,timeout=2)
        authorization = {'Authorization':'Bearer '+doc['token'], 'Content-Type':'application/json'}
        authorization.update(headers or {})
        data = json.dumps(payload).encode() if payload is not None else None
        connection.request(method,path,body=data,headers=authorization)
        response = connection.getresponse()
        result = (response.status,json.loads(response.read()))
        connection.close()
        return result

    def test_sdk_submission_is_queued_until_a_device_consumer_displays_it(self):
        first = self.client.notify('任意程序','自定义消息','这是正文',duration=3)
        self.assertEqual(first['status'],'queued')
        self.assertEqual(self.client.get(first['id'])['status'],'queued')
        self.assertFalse(self.client.status()['connected'])
        self.assertEqual(self.inbox.take()[0],first['id'])
        self.inbox.mark(first['id'],'showing')
        self.assertEqual(self.client.get(first['id'])['status'],'showing')
        self.inbox.mark(first['id'],'completed')
        self.assertEqual(self.client.get(first['id'])['status'],'completed')

    def test_auth_origin_host_and_unknown_operations_cannot_submit(self):
        for headers, status in (({'Authorization':'Bearer invalid'},401),
                                ({'Origin':'https://example.org'},403),
                                ({'Host':'example.org'},403)):
            with self.subTest(headers=headers):
                result = self.request('POST','/v1/notifications',{'source':'a','title':'b'},headers)
                self.assertEqual(result[0],status)
        self.assertEqual(self.request('POST','/v1/flash',{})[0],404)
        self.assertEqual(self.inbox.status()['queued'],0)

    def test_validation_and_queue_full_have_distinct_http_results(self):
        self.assertEqual(self.request('POST','/v1/notifications',{'source':'a','title':'b','duration':-1})[0],400)
        self.client.notify('custom','first')
        with self.assertRaisesRegex(NotificationClientError,'429'):
            self.client.notify('custom','second')
        self.assertEqual(self.request('GET','/v1/notifications/invalid')[0],404)

    def test_stop_removes_discovery_and_client_reports_service_unavailable(self):
        self.server.stop()
        self.assertFalse(self.discovery.exists())
        with self.assertRaises(NotificationClientError):
            self.client.status()

    def test_client_refuses_remote_discovery(self):
        self.discovery.write_text(json.dumps({'api_version':1,'url':'http://example.org:80', 'token':'a'*64}))
        with self.assertRaises(NotificationClientError):
            self.client.status()


class NotificationGuiTests(unittest.TestCase):
    setUp = test_features.GuiTests.setUp
    tearDown = test_features.GuiTests.tearDown
    complete = test_features.GuiTests.complete

    def submit(self, **extra):
        return self.ui.notification_inbox.submit({'source':'Custom app','title':'你好',
                                                  'message':'收到新消息',**extra})

    def show_notification(self, **extra):
        record = self.submit(**extra)
        self.ui.tick()
        self.complete()
        self.assertEqual(self.ui.notification_inbox.get(record['id'])['status'],'showing')
        return record

    def test_upright_preview_oriented_output_and_context_restored_after_timeout(self):
        self.ui.title.set('原文字')
        self.ui.subtitle.set('原副标题')
        self.ui.upside_down.set(True)
        original = self.ui.image()
        record = self.show_notification(duration=2)
        shown = self.ui.notification_frame.copy()
        self.assertEqual(self.display.frames[-1].tobytes(),
                         self.ui.orient_for_screen(shown).tobytes())
        preview = ImageTk.getimage(self.ui.photo).convert('RGB')
        self.assertEqual(preview.tobytes(),shown.resize(preview.size, Image.Resampling.NEAREST).tobytes())
        self.assertEqual(self.ui.title.get(),'原文字')
        self.ui.notification_until = 0
        self.ui.tick()
        self.complete()
        self.assertEqual(self.ui.notification_inbox.get(record['id'])['status'],'completed')
        self.assertEqual(self.display.frames[-1].tobytes(),self.ui.orient_for_screen(original).tobytes())
        self.assertEqual(self.display.burns,[])

    def test_notifications_wait_for_existing_serial_job_and_material_editor(self):
        gate = threading.Event()
        started = threading.Event()
        def flash_job():
            self.display.enter('flash_job')
            started.set()
            try:
                if not gate.wait(3): raise RuntimeError('test gate timeout')
            finally:
                self.display.in_call = False
        record = self.submit()
        self.ui.run(flash_job, lambda _:None)
        self.assertTrue(started.wait(1))
        self.ui.tick()
        self.assertEqual(self.display.calls,['flash_job'])
        gate.set()
        self.complete()
        self.ui.open_asset_editor()
        self.ui.tick()
        self.assertEqual(self.ui.notification_inbox.get(record['id'])['status'],'queued')
        self.ui.asset_editor.close()
        self.ui.tick()
        self.complete()
        self.assertEqual(self.display.calls,['flash_job','show'])

    def test_no_connection_retains_queue_until_expiry(self):
        self.ui.display = None
        now = [0]
        self.ui.notification_inbox = NotificationInbox(clock=lambda:now[0])
        record = self.submit(ttl=1)
        self.ui.tick()
        self.assertIsNone(self.ui.active_notification)
        now[0]=2
        self.ui.display = self.display
        self.ui.tick()
        self.assertEqual(self.ui.notification_inbox.get(record['id'])['status'],'expired')
        self.assertEqual(self.display.frames,[])

    def test_user_mode_change_dismisses_notification_without_overwriting_settings(self):
        record = self.show_notification()
        self.ui.mode.set('电脑状态')
        self.ui.mode_changed()
        self.ui.tick()
        self.complete()
        self.assertIsNone(self.ui.active_notification)
        self.assertEqual(self.ui.notification_inbox.get(record['id'])['status'],'dismissed')
        self.assertEqual(self.ui.mode.get(),'电脑状态')

    def test_recovery_redraws_notification_without_restarting_its_duration(self):
        record = self.show_notification()
        deadline = self.ui.notification_until
        self.display.recovery_count = 1
        self.assertTrue(self.ui.sync_recovery())
        self.ui.tick()
        self.complete()
        self.assertEqual(self.ui.notification_until, deadline)
        self.assertEqual(self.ui.active_notification[0],record['id'])
        self.assertEqual(self.display.frames[0].tobytes(),self.display.frames[1].tobytes())

    def test_notification_render_failure_preserves_connection(self):
        self.ui.live.set(True)
        record = self.submit()
        with patch.object(app,'render_notification',side_effect=features.ContentError('bad notification')):
            self.ui.tick()
            self.complete()
        self.assertIs(self.ui.display,self.display)
        self.assertEqual(self.ui.notification_inbox.get(record['id'])['status'],'failed')
        self.assertTrue(self.ui.live.get())

    def test_static_burn_uses_base_content_and_dismisses_transient_notification(self):
        record = self.show_notification()
        expected = self.ui.image()
        with patch.object(app.messagebox,'askyesno',return_value=True):
            self.ui.burn_offline()
            self.complete()
        self.assertEqual(self.display.burns[0].tobytes(),expected.tobytes())
        self.assertEqual(self.ui.notification_inbox.get(record['id'])['status'],'dismissed')

    def test_dynamic_mode_requests_current_frame_after_notification(self):
        self.ui.mode.set('时钟')
        record = self.show_notification()
        self.ui.notification_until = 0
        with patch.object(self.ui,'render_request',wraps=self.ui.render_request) as render:
            self.ui.tick()
            self.complete()
        self.assertEqual(render.call_args.args[0]['mode'],'时钟')
        self.assertEqual(self.ui.notification_inbox.get(record['id'])['status'],'completed')


if __name__ == '__main__':
    unittest.main(verbosity=2)
