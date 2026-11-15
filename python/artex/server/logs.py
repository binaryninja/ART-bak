"""Protected log snapshot/history contracts; SSE and async capture are not ported.

The ring is explicitly injected. Its existence alone does not configure logging,
persistence or subscribers. No logger is redirected on import/construction.
"""
from dataclasses import dataclass, asdict, replace
from datetime import datetime, timezone
from threading import Lock
import re
from urllib.parse import parse_qs, urlsplit
from .core import json_response, error_response


@dataclass(frozen=True)
class LogLine:
    seq: int
    ts: str
    level: str
    tag: str
    text: str
    db_id: int = 0

    def to_dict(self):
        value = asdict(self)
        if not self.db_id:
            del value['db_id']
        return value


def parse_log(line: str, *, now=None):
    message = line
    if len(message) >= 20 and message[4] == '/' and message[7] == '/' and message[10] == ' ':
        message = message[19:].strip()
    tag = message[1:message.index(']')] if message.startswith('[') and message.find(']') > 1 else ''
    lowered = message.lower()
    # Preserve historical log classification as well as translated English.
    errors = ('fatal', 'panic', 'error', 'err:', 'failed', 'discarded', 'denied', 'unreachable', '\u5931\u8d25', '\u4e22\u5f03', '\u62d2\u7edd', '\u2715', '\u4e0d\u53ef\u8fbe')
    warnings = ('warn', 'disabled', 'retry', '\u7981\u7528', 'skip', 'stopped', '\u26a0', '\u91cd\u8bd5')
    level = 'error' if any(k in lowered for k in errors) else 'warn' if any(k in lowered for k in warnings) else 'info'
    stamp = now if now is not None else datetime.now(timezone.utc)
    return LogLine(0, stamp.isoformat(timespec='seconds').replace('+00:00', 'Z'), level, tag, message)


class LogRing:
    def __init__(self, capacity=3000):
        if type(capacity) is not int or capacity < 1:
            raise ValueError('Log capacity must be positive')
        self.capacity, self._seq, self._lines, self._lock = capacity, 0, [], Lock()

    def append(self, line: LogLine):
        if not isinstance(line, LogLine):
            raise TypeError('Expected LogLine')
        with self._lock:
            self._seq += 1
            value = replace(line, seq=self._seq)
            self._lines.append(value)
            self._lines = self._lines[-self.capacity:]
            return value

    def recent(self, since, limit):
        if type(since) is not int or type(limit) is not int:
            raise ValueError('Log cursors and limits must be integers')
        with self._lock:
            limit = self.capacity if limit <= 0 or limit > self.capacity else limit
            return [line for line in self._lines if line.seq > since][-limit:], self._seq


def _parameter(request, name, default):
    value = parse_qs(urlsplit(request.target).query).get(name, [''])[0]
    try:
        # Go Atoi rejects whitespace and non-ASCII numeral forms.
        if not re.fullmatch(r'[+-]?[0-9]+', value):
            return default
        parsed = int(value)
        return parsed if -(2**63) <= parsed < 2**63 else default
    except ValueError:
        return default


class LogService:
    def __init__(self, store=None, ring=None):
        self.store, self.ring = store, ring

    def register(self, router):
        router.add('GET', '/api/logs', lambda request, params: self.recent(request))
        router.add('GET', '/api/logs/history', lambda request, params: self.history(request))

    def recent(self, request):
        if self.ring is None:
            return error_response(503, 'Live log capture is unavailable')
        lines, cursor = self.ring.recent(_parameter(request, 'since', 0), _parameter(request, 'limit', 500))
        return json_response(200, {'items': [line.to_dict() for line in lines], 'cursor': cursor})

    def history(self, request):
        if self.store is None:
            return json_response(200, {'items': [], 'has_more': False})
        before = _parameter(request, 'before', 0)
        limit = min(_parameter(request, 'limit', 200), 500)
        # Deliberately normalize nonpositive history limits, avoiding Go's
        # zero-limit has_more=true and PostgreSQL negative-limit failure.
        if limit <= 0:
            limit = 200
        try:
            rows = self.store.recent(limit) if before <= 0 else self.store.before(before, limit)
            return json_response(200, {'items': [row.to_dict() for row in rows], 'has_more': len(rows) == limit})
        except Exception:
            return error_response(500, 'Log history unavailable')
