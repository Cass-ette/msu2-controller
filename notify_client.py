"""Dependency-free SDK and CLI for arbitrary local notification producers."""
import argparse
import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler

DEFAULT_DISCOVERY = Path(__file__).parent / '.runtime' / 'api.json'


def default_discovery():
    # Keep this SDK independently distributable: use only the standard library.
    if DEFAULT_DISCOVERY.exists():
        return DEFAULT_DISCOVERY
    if sys.platform == 'darwin':
        directory = Path.home() / 'Library' / 'Application Support'
    elif sys.platform == 'win32':
        directory = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local'))
    else:
        directory = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local' / 'share'))
    return directory / 'MSU2Controller' / '.runtime' / 'api.json'


class NotificationClientError(RuntimeError):
    pass


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


class NotificationClient:
    def __init__(self, discovery_path=None, timeout=3):
        self.discovery_path = Path(discovery_path) if discovery_path is not None else default_discovery()
        self.timeout = timeout

    def request(self, method, path, payload=None):
        try:
            doc = json.loads(self.discovery_path.read_text(encoding='utf-8'))
            url, token = doc['url'], doc['token']
            parsed = urlsplit(url)
            if (doc.get('api_version') != 1 or parsed.scheme != 'http' or parsed.hostname != '127.0.0.1'
                    or parsed.username or parsed.password or not parsed.port or parsed.path or parsed.query
                    or parsed.fragment or not isinstance(token, str) or len(token) != 64):
                raise ValueError('无效本机通知接口信息')
            data = json.dumps(payload, ensure_ascii=False).encode('utf-8') if payload is not None else None
            request = Request(url+path, data=data, method=method,
                              headers={'Authorization': 'Bearer '+token, 'Content-Type': 'application/json'})
            # Never send local credentials through environment-configured proxies.
            opener = build_opener(ProxyHandler({}), NoRedirects())
            with opener.open(request, timeout=self.timeout) as response:
                return json.load(response)
        except HTTPError as exc:
            try:
                message = json.loads(exc.read()).get('error', str(exc))
            except (ValueError, AttributeError):
                message = str(exc)
            raise NotificationClientError(f'HTTP {exc.code}: {message}') from exc
        except (OSError, URLError, ValueError, KeyError, TypeError) as exc:
            raise NotificationClientError(f'无法连接本机消息接口，请先在控制器启用：{exc}') from exc

    def notify(self, source, title, message='', kind='info', duration=5, ttl=300):
        return self.request('POST', '/v1/notifications',
                            {'source': source, 'title': title, 'message': message,
                             'kind': kind, 'duration': duration, 'ttl': ttl})

    def status(self):
        return self.request('GET', '/v1/status')

    def get(self, identifier):
        import re
        if not re.fullmatch('[0-9a-f]{32}', identifier):
            raise NotificationClientError('通知 ID 无效')
        return self.request('GET', '/v1/notifications/'+identifier)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--discovery', type=Path)
    commands = parser.add_subparsers(dest='command', required=True)
    notify = commands.add_parser('notify')
    notify.add_argument('--source', required=True)
    notify.add_argument('--title', required=True)
    notify.add_argument('--message', default='')
    notify.add_argument('--kind', choices=('info','success','warning','error'), default='info')
    notify.add_argument('--duration', type=float, default=5)
    notify.add_argument('--ttl', type=float, default=300)
    commands.add_parser('status')
    query = commands.add_parser('get')
    query.add_argument('id')
    args = parser.parse_args()
    client = NotificationClient(args.discovery)
    try:
        if args.command == 'notify':
            result = client.notify(args.source, args.title, args.message, args.kind, args.duration, args.ttl)
        else:
            result = client.status() if args.command == 'status' else client.get(args.id)
        print(json.dumps(result,ensure_ascii=False))
        return 0
    except NotificationClientError as exc:
        parser.exit(1, str(exc)+'\n')


if __name__ == '__main__':
    raise SystemExit(main())
