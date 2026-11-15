"""Offline-capable completion recording with injected providers and sinks.

No provider, database, or network connection is created by this module.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
import json
import logging
from threading import RLock
from time import monotonic
from typing import Any, Mapping


def with_task_id(context, task_id):
    return _with(context, 'task_id', task_id)


def with_worker(context, worker):
    return _with(context, 'worker', worker)


def _with(context, key, value):
    result = dict(context or {})
    if value.strip():
        result[key] = value.strip()
    return result


def task_id_from(context):
    return (context or {}).get('task_id', '').strip()


def parse_session(session):
    if not session.startswith('exp'):
        return '', ''
    parts = session[3:].split('-')
    return parts[0], parts[1] if len(parts) > 1 else ''


@dataclass(frozen=True)
class Attempt:
    status: int
    body: bytes


class Capture:
    """Thread-safe capture; bodies stay bytes, including non-UTF8 data."""
    def __init__(self):
        self._lock = RLock()
        self._request = b''
        self._attempts = []

    def set_request(self, body):
        body = body.encode() if isinstance(body, str) else bytes(body)
        with self._lock:
            if not self._request:
                self._request = body

    def tee_response(self, status, source):
        if source is None:
            return None
        with self._lock:
            attempt = [status, bytearray()]
            self._attempts.append(attempt)
        return _Tee(source, self, attempt)

    def raw_request(self):
        with self._lock:
            return self._request

    def attempts(self):
        with self._lock:
            return [Attempt(status, bytes(body)) for status, body in self._attempts]

    def raw_response(self):
        attempts = self.attempts()
        if not attempts:
            return b''
        if len(attempts) == 1:
            return attempts[0].body
        return b''.join(
            f'===== attempt {i}/{len(attempts)} — HTTP {a.status} =====\n'.encode()
            + a.body + (b'' if a.body.endswith(b'\n') else b'\n')
            for i, a in enumerate(attempts, 1)
        )


class _Tee:
    def __init__(self, source, capture, attempt):
        self.source, self.capture, self.attempt = source, capture, attempt

    def read(self, size=-1):
        data = self.source.read(size)
        if data:
            with self.capture._lock:
                self.attempt[1].extend(data)
        return data

    def close(self):
        return self.source.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def new_capture(context):
    capture = Capture()
    return {**(context or {}), 'capture': capture}, capture


def capture_from(context):
    return (context or {}).get('capture')


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def add(self, other):
        for name in self.__dataclass_fields__:
            setattr(self, name, getattr(self, name) + _get(other, name, 0))


def _get(value, name, default=None):
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def _json(value):
    return json.dumps(value, ensure_ascii=False, default=lambda x: asdict(x) if is_dataclass(x) else vars(x))


class Recorder:
    """Provider decorator. Sink implements insert_llm_usage/insert_llm_record(dict).

    Provider.stream(context, request) yields event objects or mappings; errors
    are Python exceptions. Provider.complete returns (message, stop_reason, usage).
    Close a partially-consumed stream explicitly (or use contextlib.closing) to
    guarantee prompt metering; Python loop break alone does not close generators.
    """
    def __init__(self, inner, sink=None, model='', profile_name='', thinking_type='', reasoning_effort='', enabled=None, clock=monotonic):
        self.inner, self.sink = inner, sink
        self.model, self.profile_name = model, profile_name
        self.thinking_type, self.reasoning_effort = thinking_type, reasoning_effort
        self.enabled, self.clock = enabled, clock

    def serialize_request(self, request):
        body = {key: _get(request, key, default) for key, default in [('system', []), ('messages', []), ('max_tokens', 0)]}
        thinking = _get(request, 'thinking', '') or self.thinking_type
        if thinking or self.reasoning_effort:
            body['thinking'] = {'type': thinking, 'effort': self.reasoning_effort}
        tools = _get(request, 'tools', [])
        if tools:
            body['tools'] = [_get(tool, 'name', '') for tool in tools]
            body['tools_count'] = len(tools)
        if _get(request, 'temperature') is not None:
            body['temperature'] = _get(request, 'temperature')
        if _get(request, 'stop'):
            body['stop'] = _get(request, 'stop')
        return _json(body)

    def _begin(self, context, request):
        context = dict(context or {})
        record = self.enabled is None or self.enabled()
        session = context.get('session_id', '')
        parsed, worker = parse_session(session)
        try:
            exploration_id = int(parsed)
        except ValueError:
            exploration_id = 0
        state = dict(start=self.clock(), record=record, request_body=self.serialize_request(request) if record else '', session_id=session,
                     task_id=task_id_from(context) or parsed, worker=context.get('worker', '').strip() or worker,
                     exploration_id=exploration_id)
        if record:
            context, state['capture'] = new_capture(context)
        return context, state

    def _write(self, method, row):
        if self.sink is not None:
            try:
                getattr(self.sink, method)(row)
            except Exception:
                logging.getLogger(__name__).exception('LLM recording sink failed')

    def _finish(self, state, usage, text='', thinking='', stop_reason='', error=None):
        common = dict(task_id=state['task_id'], worker=state['worker'], model=self.model, profile_name=self.profile_name,
                      latency_ms=max(0, int((self.clock()-state['start'])*1000)), **asdict(usage), status='error' if error is not None else 'ok')
        if self.model or any(asdict(usage).values()):
            self._write('insert_llm_usage', {**common, 'exploration_id': state['exploration_id']})
        if state['record']:
            response = dict(text=text, stop_reason=stop_reason, usage=asdict(usage))
            if thinking:
                response['thinking'] = thinking
            capture = state['capture']
            self._write('insert_llm_record', {**common, 'session_id': state['session_id'], 'error': str(error) if error is not None else '',
                        'request_body': state['request_body'], 'response_body': _json(response),
                        'raw_request': capture.raw_request(), 'raw_response': capture.raw_response()})

    def stream(self, context, request):
        context, state = self._begin(context, request)
        def generate():
            usage, text, thinking, stop, error = Usage(), [], [], '', None
            source = None
            try:
                source = iter(self.inner.stream(context, request))
                for event in source:
                    kind = _get(event, 'type', '')
                    if kind in ('message_start', 'message_delta'):
                        usage.add(_get(event, 'usage', {}))
                    if kind == 'message_delta':
                        stop = _get(event, 'stop_reason', '') or stop
                    if state['record'] and kind == 'text_delta':
                        text.append(_get(event, 'text', ''))
                    if state['record'] and kind == 'thinking_delta':
                        thinking.append(_get(event, 'text', ''))
                    yield event
            except GeneratorExit:
                error = context.get('error')
                raise
            except BaseException as exc:
                error = exc
                raise
            finally:
                try:
                    if source is not None and hasattr(source, 'close'):
                        source.close()
                finally:
                    self._finish(state, usage, ''.join(text), ''.join(thinking), stop, error)
        return generate()

    def complete(self, context, request):
        context, state = self._begin(context, request)
        usage, text, thinking, stop, error = Usage(), '', '', '', None
        try:
            result = self.inner.complete(context, request)
            message, stop, returned_usage = result
            usage.add(returned_usage)
            content = _get(message, 'content', [])
            text = ''.join(_get(b, 'text', '') for b in content if _get(b, 'type') == 'text')
            thinking = ''.join(_get(b, 'thinking', '') for b in content if _get(b, 'type') == 'thinking')
            return result
        except BaseException as exc:
            error = exc
            raise
        finally:
            self._finish(state, usage, text, thinking, stop, error)


def wrap(inner, sink=None, model='', profile_name='', thinking_type='', reasoning_effort='', enabled=None):
    return Recorder(inner, sink, model, profile_name, thinking_type, reasoning_effort, enabled)
