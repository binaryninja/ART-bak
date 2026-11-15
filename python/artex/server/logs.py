"""Protected log snapshot/history and explicit live SSE subscriptions.

The ring is explicitly injected. Its existence alone does not configure logging,
persistence or subscribers. No logger is redirected on import/construction.
"""
from dataclasses import dataclass, asdict, replace
from datetime import datetime, timezone
from threading import Lock, Condition
from collections import deque
import time
import json
import re
from urllib.parse import parse_qs, urlsplit
from .core import json_response, error_response
from .streaming import StreamingResponse, sse_frame


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
        self._subscribers = set()
        self._closed = False

    def append(self, line: LogLine):
        if not isinstance(line, LogLine):
            raise TypeError('Expected LogLine')
        with self._lock:
            if self._closed: raise RuntimeError('Log ring is closed')
            self._seq += 1
            value = replace(line, seq=self._seq)
            self._lines.append(value)
            self._lines = self._lines[-self.capacity:]
            for subscriber in self._subscribers:
                subscriber.offer(value)
            return value

    def recent(self, since, limit):
        if type(since) is not int or type(limit) is not int:
            raise ValueError('Log cursors and limits must be integers')
        with self._lock:
            limit = self.capacity if limit <= 0 or limit > self.capacity else limit
            return [line for line in self._lines if line.seq > since][-limit:], self._seq


    def subscribe(self, *, clock=time.monotonic, wait=None):
        subscription = LogSubscription(self, clock=clock, wait=wait)
        with self._lock:
            if self._closed: raise RuntimeError('Log ring is closed')
            self._subscribers.add(subscription)
        return subscription

    def _unsubscribe(self, subscription):
        with self._lock:
            self._subscribers.discard(subscription)

    @property
    def subscriber_count(self):
        with self._lock: return len(self._subscribers)

    def close(self):
        with self._lock:
            self._closed = True
            subscribers = tuple(self._subscribers)
            self._subscribers.clear()
        for subscriber in subscribers: subscriber.close()


class LogSubscription:
    """256-line FIFO, dropping newest on overflow like Go's nonblocking channel.

    Queue count is bounded; drops are observable locally, not invented SSE events.
    Close discards pending lines and wakes blocked readers. No thread is started.
    An injected wait(condition, timeout) runs with the condition lock held and
    must follow Condition.wait's release/reacquire contract.
    """
    def __init__(self, ring, *, clock=time.monotonic, wait=None):
        self.ring,self.clock,self.wait = ring,clock,wait
        self.condition=Condition()
        self.queue=deque()
        self.closed=False
        self.dropped=0

    def offer(self,line):
        with self.condition:
            if self.closed:return
            if len(self.queue)>=256:
                self.dropped+=1
                return
            self.queue.append(line)
            self.condition.notify()

    def poll(self,timeout):
        with self.condition:
            deadline=self.clock()+max(0,timeout)
            while not self.queue and not self.closed:
                remaining=deadline-self.clock()
                if remaining<=0:return None
                if self.wait is None:self.condition.wait(remaining)
                else:self.wait(self.condition,remaining)
            if self.closed:return None
            return self.queue.popleft()

    def close(self):
        with self.condition:
            if self.closed:return
            self.closed=True
            self.queue.clear()
            self.condition.notify_all()
        self.ring._unsubscribe(self)


class _LogStream:
    def __init__(self,subscription,lines,cursor,since,clock):
        self.subscription=subscription
        self.backlog=deque(lines)
        self.since=max(cursor,since)
        self.clock=clock
        self.ping_at=clock()+20
        self.closed=False

    def __iter__(self):return self

    def __next__(self):
        if self.closed or self.subscription.closed:
            self.close();raise StopIteration
        try:
            if self.backlog:
                line=self.backlog.popleft()
            else:
                while True:
                    if self.clock()>=self.ping_at:
                        self.ping_at=self.clock()+20
                        return b': ping\n\n'
                    line=self.subscription.poll(max(0,self.ping_at-self.clock()))
                    if self.subscription.closed:
                        self.close();raise StopIteration
                    if line is None:
                        self.ping_at=self.clock()+20
                        return b': ping\n\n'
                    if line.seq>self.since:
                        self.since=line.seq
                        break
            return sse_frame(json.dumps(line.to_dict(),ensure_ascii=False,allow_nan=False,separators=(',',':')))
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.closed:return
        self.closed=True
        self.backlog.clear()
        self.subscription.close()


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
    def __init__(self, store=None, ring=None, *, stream_clock=time.monotonic, stream_wait=None):
        self.store, self.ring = store, ring
        self.stream_clock,self.stream_wait = stream_clock,stream_wait

    def register(self, router):
        router.add('GET', '/api/logs', lambda request, params: self.recent(request))
        router.add('GET', '/api/logs/history', lambda request, params: self.history(request))
        router.add('GET', '/api/logs/stream', lambda request, params: self.stream(request))

    def stream(self, request):
        if self.ring is None:return error_response(503,'Live log capture is unavailable')
        # Unlike snapshot's legacy forgiving parser, an ambiguous reconnect
        # cursor must not silently replay a different range of sensitive logs.
        try:
            query=parse_qs(urlsplit(request.target).query,keep_blank_values=True,max_num_fields=1)
            if set(query)-{'since'} or any(len(v)!=1 for v in query.values()):raise ValueError()
            raw=query.get('since',['0'])[0]
            if not re.fullmatch(r'[0-9]{1,19}',raw):raise ValueError()
            since=int(raw)
            if since>=2**63:raise ValueError()
        except ValueError:return error_response(400,'Invalid log stream cursor')
        subscription=None
        try:
            subscription=self.ring.subscribe(clock=self.stream_clock,wait=self.stream_wait)
            lines,cursor=self.ring.recent(since,1000)
            stream=_LogStream(subscription,lines,cursor,since,self.stream_clock)
            return StreamingResponse(200,stream,headers={'Content-Type':'text/event-stream',
                'Cache-Control':'no-cache','X-Accel-Buffering':'no'},on_close=stream.close)
        except Exception:
            if subscription is not None:subscription.close()
            return error_response(503,'Live log stream unavailable')

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
