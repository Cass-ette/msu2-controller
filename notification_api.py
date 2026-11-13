"""Opt-in loopback HTTP adapter. HTTP threads only write to the shared inbox."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
from pathlib import Path
import secrets
import tempfile
from threading import Thread

from notifications import NotificationQueueFull
from app_paths import data_directory

DEFAULT_DISCOVERY = data_directory() / '.runtime' / 'api.json'


class NotificationServer:
    def __init__(self, inbox, discovery_path=DEFAULT_DISCOVERY):
        self.inbox = inbox
        self.discovery_path = Path(discovery_path)
        self.token = secrets.token_hex(32)
        self.server = None
        self.thread = None

    def start(self):
        if self.server is not None:
            return self
        owner = self
        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.0'

            def setup(self):
                super().setup()
                self.connection.settimeout(2)

            def log_message(self, *_):
                pass  # Do not log message content or credentials.

            def reply(self, status, payload):
                data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(data)

            def authorized(self):
                expected_host = f'127.0.0.1:{owner.server.server_port}'
                if self.headers.get('Host') != expected_host or self.headers.get('Origin'):
                    self.reply(403, {'error': '只接受本机脚本请求'})
                    return False
                authorization = self.headers.get('Authorization', '')
                if not hmac.compare_digest(authorization.encode(), ('Bearer '+owner.token).encode()):
                    self.reply(401, {'error': '无效通知接口凭据'})
                    return False
                return True

            def do_GET(self):
                if not self.authorized():
                    return
                if self.path == '/v1/status':
                    self.reply(200, owner.inbox.status())
                elif self.path.startswith('/v1/notifications/'):
                    item = owner.inbox.get(self.path.removeprefix('/v1/notifications/'))
                    self.reply(200 if item else 404, item or {'error': '通知不存在或历史已清理'})
                else:
                    self.reply(404, {'error': '接口不存在'})

            def do_POST(self):
                if not self.authorized():
                    return
                if self.path != '/v1/notifications':
                    self.reply(404, {'error': '接口不存在'})
                    return
                if self.headers.get_content_type() != 'application/json' or self.headers.get('Transfer-Encoding'):
                    self.reply(415, {'error': '需要带 Content-Length 的 application/json'})
                    return
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 1 <= length <= 8192:
                        self.reply(413, {'error': '通知请求大小须为 1–8192 字节'})
                        return
                    raw = self.rfile.read(length)
                    if len(raw) != length:
                        raise ValueError('请求内容不完整')
                    item = owner.inbox.submit(json.loads(raw))
                    self.reply(202, item)
                except NotificationQueueFull as exc:
                    self.reply(429, {'error': str(exc)})
                except (ValueError, TypeError, UnicodeError) as exc:
                    self.reply(400, {'error': str(exc)})
                except (OSError, TimeoutError):
                    self.close_connection = True

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.daemon_threads = True
        self.server = server
        try:
            self.discovery_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.document = {'api_version': 1, 'url': f'http://127.0.0.1:{server.server_port}',
                             'token': self.token}
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=self.discovery_path.parent, delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(json.dumps(self.document).encode('utf-8'))
                os.replace(temporary, self.discovery_path)
            finally:
                if temporary is not None and temporary.exists():
                    temporary.unlink()
            self.thread = Thread(target=server.serve_forever, kwargs={'poll_interval': .1}, daemon=True)
            self.thread.start()
        except Exception:
            server.server_close()
            self.server = None
            raise
        return self

    def stop(self):
        if self.server is None:
            return
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.server = None
        try:
            # Do not remove another running controller's discovery file.
            current = json.loads(self.discovery_path.read_text(encoding='utf-8'))
            if current.get('token') == self.token:
                self.discovery_path.unlink()
        except (OSError, ValueError, TypeError):
            pass
