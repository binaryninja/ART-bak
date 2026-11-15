"""Explicit durable normalized recording, never enabled by default.

No network, schema setup, retries, logging of provider errors, or header capture.
Unlike the compatibility Recorder, scope is immutable (never parsed from session),
write failures propagate, and usage plus record commit atomically. The caller's
executor must provide transaction() yielding its transaction-bound executor.
Commit failure is uncertain: never replay provider work to repair a recording.
Post-provider settlement catches even cancellation/interruption exceptions and
raises redacted RecordingPersistenceError: completion cannot be assumed. Provider
interruptions retain their original type only when settlement itself succeeds.

A mandatory binding_validator(scope, inner)->literal True is checked immediately
before each provider invocation or lazy stream admission. It must attest the
selected immutable provider/profile configuration, not request labels.
Scope is a trusted host assertion, not proof of arbitrary inner-provider identity.
The host must construct it from the same freshly validated model/profile binding
as inner (including any wrappers), never from client-supplied labels. This module
cannot inspect opaque providers or guarantee their configuration stays unchanged.
It is not yet automatically wired into the profile/chat assembly.

Only normalized request/response data is captured. Raw HTTP bodies/headers are
unsupported and always empty, even if context already contains a Capture.
A required redact(direction, serialized_json, scope)->str policy must remove
private content from normalized messages; this adapter cannot identify every
secret embedded in user text. Redaction output is bounded before any write.
Provider exceptions are re-raised but their text is never stored or logged.

Streams persist on exhaustion/error/explicit close after iteration begins.
Close partially consumed streams promptly (contextlib.closing); a bare break
is insufficient. Never-started generators invoke neither provider nor database.
Usage is only observed reported usage, not an invented estimate of unreported
billable work. Cancellation is cooperative, not interruption of blocked calls.
"""
from dataclasses import dataclass, asdict
from collections.abc import Mapping
from time import monotonic
from . import Recorder, Usage, _get, _json
from ..db.llm_records import LLMRecordStore, LLMRecord, LLMUsage


class RecordingPersistenceError(RuntimeError):
    """Provider may already have run; database commit may be uncertain. No retry."""


class RecordingBindingError(RuntimeError): pass


class CaptureLimit(ValueError): pass
class RecordingCancelled(RuntimeError): pass


@dataclass(frozen=True)
class RecordingScope:
    task_id: str
    session_id: str
    worker: str
    model: str
    profile_name: str
    exploration_id: int = 0


def _bounded(value, limit):
    if not isinstance(value, str) or '\0' in value or len(value.encode('utf-8')) > limit:
        raise CaptureLimit('Recording content exceeds bound or is invalid')
    return value


def _cancel(context):
    err = getattr(context, 'err', None)
    if callable(err) and err() is not None: raise RecordingCancelled('Recording cancelled')
    if isinstance(context, Mapping) and context.get('cancelled'):
        raise RecordingCancelled('Recording cancelled')


class PostgresRecorder:
    def __init__(self, inner, store, *, scope, enabled, redact, binding_validator,
                 max_body_bytes=1048576, max_events=10000, clock=monotonic):
        if enabled is not True:
            raise ValueError('Recording requires explicit enabled=True')
        if not isinstance(store, LLMRecordStore) or not callable(getattr(store.executor, 'transaction', None)):
            raise ValueError('Explicit transactional recording store required')
        if not isinstance(scope, RecordingScope) or store.task_id != scope.task_id:
            raise ValueError('Exact fixed task recording scope required')
        for name in ('task_id','session_id','worker','model','profile_name'):
            _bounded(getattr(scope,name),4096)
        if not scope.session_id or not scope.model or type(scope.exploration_id) is not int or not 0 <= scope.exploration_id < 2**63:
            raise ValueError('Explicit session/model/exploration scope required')
        if not callable(redact) or not callable(clock) or not callable(binding_validator):
            raise ValueError('Redactor, binding validator and clock required')
        if type(max_body_bytes) is not int or not 1 <= max_body_bytes <= 16777216:
            raise ValueError('Invalid body bound')
        if type(max_events) is not int or not 1 <= max_events <= 100000:
            raise ValueError('Invalid event bound')
        self.inner, self.store, self.scope = inner, store, scope
        self.redact, self.clock = redact, clock
        self.binding_validator = binding_validator
        self.max_body_bytes, self.max_events = max_body_bytes, max_events
        self.serializer = Recorder(inner)

    def _begin(self, context, request):
        scope, inner = self.scope, self.inner
        # Freeze this attested identity for settlement even if active selection changes.
        # Never add inferred scope to provider context or overwrite its authority.
        if isinstance(context, Mapping):
            for name in ('task_id','session_id','worker','model','profile_name','exploration_id'):
                if name in context and context[name] != getattr(scope,name):
                    raise ValueError('Recording context scope mismatch')
            context = dict(context)
            context.pop('capture', None)
        _cancel(context)
        raw = _bounded(self.serializer.serialize_request(request),self.max_body_bytes)
        request_body = _bounded(self.redact('request',raw,scope),self.max_body_bytes)
        start = self.clock()
        try:
            if self.binding_validator(scope, inner) is not True:
                raise RecordingBindingError('Recording provider binding denied')
        except BaseException:
            # No provider work has started; hide host configuration details.
            raise RecordingBindingError('Recording provider binding unavailable') from None
        return context, request_body, start, scope, inner

    def _finish(self, request_body, start, usage, response, status, scope):
        try:
            raw = _bounded(response,self.max_body_bytes)
            body = _bounded(self.redact('response',raw,scope),self.max_body_bytes)
            elapsed = max(0,min(2**31-1,int((self.clock()-start)*1000)))
            values = asdict(usage)
            if any(type(v) is not int or not 0 <= v < 2**31 for v in values.values()):
                raise ValueError('Invalid usage')
            common = dict(task_id=scope.task_id,worker=scope.worker,
                model=scope.model,profile_name=scope.profile_name,latency_ms=elapsed,
                input_tokens=usage.input_tokens,output_tokens=usage.output_tokens,
                cache_read=usage.cache_read_tokens,cache_write=usage.cache_write_tokens,status=status)
            record = LLMRecord(**common,session_id=scope.session_id,
                error='' if status=='ok' else 'Provider attempt '+status,
                request_body=request_body,response_body=body)
            meter = LLMUsage(**common,exploration_id=scope.exploration_id)
            with self.store.executor.transaction() as executor:
                scoped = LLMRecordStore(executor,task_id=scope.task_id)
                scoped.insert_usage(meter)
                scoped.insert(record)
        except BaseException:
            # A cancellation during commit can mean the rows committed. Do not
            # expose driver detail or let callers mistake this for safe replay.
            raise RecordingPersistenceError('Recording persistence failed; provider work must not be retried') from None

    def complete(self, context, request):
        original_context = context
        context, body, start, scope, inner = self._begin(context,request)
        usage, response, status = Usage(), '{}', 'error'
        try:
            result = inner.complete(context,request)
            message, stop, reported = result
            usage.add(reported)
            response = _bounded(_json({'message':message,'stop_reason':stop,'usage':asdict(usage)}),self.max_body_bytes)
            _cancel(original_context)
            _cancel(context)
            status = 'ok'
            return result
        except RecordingCancelled:
            status = 'cancelled'
            raise
        finally:
            self._finish(body,start,usage,response,status,scope)

    def stream(self, context, request):
        # Generator body is lazy; no write or provider call until consumption.
        original_context = context
        context, body, start, scope, inner = self._begin(context,request)
        usage, events, size, status, source = Usage(), [], 2, 'incomplete', None
        try:
            source = iter(inner.stream(context,request))
            for event in source:
                kind = _get(event,'type','')
                if kind in ('message_start','message_delta'):
                    usage.add(_get(event,'usage',{}))
                encoded = _json(event)
                size += len(encoded.encode('utf-8')) + (1 if events else 0)
                if len(events) >= self.max_events or size > self.max_body_bytes:
                    raise CaptureLimit('Stream recording exceeds bound')
                events.append(encoded)
                _cancel(original_context)
                _cancel(context)
                if kind == 'message_stop': status = 'ok'
                yield event
            _cancel(original_context)
            _cancel(context)
        except GeneratorExit:
            status = 'cancelled'
            raise
        except RecordingCancelled:
            status = 'cancelled'
            raise
        except BaseException:
            status = 'error'
            raise
        finally:
            try:
                if source is not None and callable(getattr(source,'close',None)):
                    source.close()
            except BaseException:
                status = 'error'
                raise
            finally:
                self._finish(body,start,usage,'['+','.join(events)+']',status,scope)
