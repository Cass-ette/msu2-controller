"""Source-independent notification messages and a bounded thread-safe inbox.

No GUI, USB, HTTP or third-party application code belongs in this module.
"""
from collections import OrderedDict, deque
from dataclasses import dataclass, asdict
import math
import re
from threading import Lock
import time
import uuid

KINDS = ('info', 'success', 'warning', 'error')


class NotificationQueueFull(ValueError):
    pass


def bounded_text(value, name, limit, required=False):
    if (not isinstance(value, str) or len(value) > limit or
            (required and not value.strip()) or
            any(ord(char) < 32 and char not in '\n\t' for char in value)):
        raise ValueError(f'{name} 必须是最多 {limit} 字符的文本')
    return value


@dataclass(frozen=True)
class Notification:
    source: str
    title: str
    message: str = ''
    kind: str = 'info'
    duration: float = 5
    ttl: float = 300

    @classmethod
    def from_payload(cls, payload):
        if not isinstance(payload, dict):
            raise ValueError('通知必须是 JSON 对象')
        allowed = {'source', 'title', 'message', 'kind', 'duration', 'ttl'}
        if set(payload) - allowed:
            raise ValueError('未知通知字段：' + ', '.join(sorted(set(payload)-allowed)))
        value = cls(**payload) if 'source' in payload and 'title' in payload else None
        if value is None:
            raise ValueError('通知必须包含 source 和 title')
        bounded_text(value.source, 'source', 40, True)
        bounded_text(value.title, 'title', 80, True)
        bounded_text(value.message, 'message', 200)
        if value.kind not in KINDS:
            raise ValueError('kind 必须是 info / success / warning / error')
        for name, number, lower, upper in (('duration', value.duration, 1, 60),
                                           ('ttl', value.ttl, 1, 3600)):
            if type(number) not in (int, float) or not math.isfinite(number) or not lower <= number <= upper:
                raise ValueError(f'{name} 必须在 {lower}–{upper} 秒之间')
        return value


class NotificationInbox:
    def __init__(self, capacity=16, history=128, clock=time.monotonic):
        self.capacity = capacity
        self.history = max(history, capacity+1)
        self.clock = clock
        self.pending = deque()
        self.records = OrderedDict()
        self.lock = Lock()
        self.connected = False

    def _expire(self):
        now = self.clock()
        remaining = deque()
        for identifier in self.pending:
            record = self.records[identifier]
            if now >= record['expires_at']:
                record['status'] = 'expired'
            else:
                remaining.append(identifier)
        self.pending = remaining

    def _public(self, identifier):
        record = self.records[identifier]
        return {'id': identifier, 'status': record['status'],
                'notification': asdict(record['notification'])}

    def submit(self, payload):
        notification = Notification.from_payload(payload)
        with self.lock:
            self._expire()
            if len(self.pending) >= self.capacity:
                raise NotificationQueueFull('通知队列已满，请稍后重试')
            while len(self.records) >= self.history:
                removable = next((key for key, item in self.records.items()
                                  if item['status'] not in ('queued', 'sending', 'showing')), None)
                if removable is None:
                    raise NotificationQueueFull('通知记录已满')
                del self.records[removable]
            identifier = uuid.uuid4().hex
            self.records[identifier] = {'notification': notification, 'status': 'queued',
                                        'expires_at': self.clock()+notification.ttl}
            self.pending.append(identifier)
            return self._public(identifier)

    def take(self):
        with self.lock:
            self._expire()
            if not self.pending:
                return None
            identifier = self.pending.popleft()
            self.records[identifier]['status'] = 'sending'
            return identifier, self.records[identifier]['notification']

    def mark(self, identifier, status):
        if status not in ('showing', 'completed', 'dismissed', 'failed'):
            raise ValueError('无效通知状态')
        with self.lock:
            if identifier in self.records:
                self.records[identifier]['status'] = status

    def get(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch('[0-9a-f]{32}', identifier):
            return None
        with self.lock:
            self._expire()
            return self._public(identifier) if identifier in self.records else None

    def set_connected(self, connected):
        with self.lock:
            self.connected = bool(connected)

    def status(self):
        with self.lock:
            self._expire()
            active = [key for key, item in self.records.items() if item['status'] in ('sending', 'showing')]
            return {'api_version': 1, 'connected': self.connected, 'queued': len(self.pending),
                    'capacity': self.capacity, 'active': active}

    def cancel_pending(self):
        with self.lock:
            for identifier in self.pending:
                self.records[identifier]['status'] = 'dismissed'
            self.pending.clear()
