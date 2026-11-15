"""Permission-gated synchronous agent loop; no built-in tools or network.

Grounded in pinned Norma v0.4.3 harness/query.go, harness/tools.go and hook/hook.go.
This deliberately smaller loop does NOT reproduce the planner/worker scheduler,
compaction, hook input rewriting, background tools, or streaming execution. It
calls an injected complete(context, request) provider. Tools are registered by a
trusted host and EVERY model/tool invocation requires explicit permission, scope
and approval grants for immutable canonical arguments. No bypass mode exists.

Limits are checked before and after callbacks, including after approval waits.
They cannot forcibly interrupt a Python callback. Providers/tools MUST cooperate
with context.check() and enforce timeouts on their own blocking operations. A
TaskManager Runner adapter must own its thread/handle and wait for this run and
all callback-owned work to drain; this module starts no threads and is not wired
as a Runner by default. Side effects already performed are never rolled back.

Token limits stop further calls once reported input+output usage reaches the
budget; each request's output cap is restricted to the remaining budget. They
are NOT a guaranteed invoice/cost cap: provider input usage is known afterward.
No retry, auto-continuation after refusal, tool discovery, shell or target access.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass, field, is_dataclass
import json
import math
import time
from typing import Callable


class RunStopped(RuntimeError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class Limits:
    max_turns: int = 8
    max_tokens: int = 32000
    max_seconds: float = 60
    max_tool_calls: int = 32
    max_tool_output_bytes: int = 65536
    max_request_bytes: int = 4 * 1024 * 1024

    def __post_init__(self):
        for name in ('max_turns', 'max_tokens', 'max_tool_calls', 'max_tool_output_bytes', 'max_request_bytes'):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError(f'{name} must be a positive integer')
        if not isinstance(self.max_seconds, (int, float)) or isinstance(self.max_seconds, bool) or not math.isfinite(self.max_seconds) or self.max_seconds <= 0:
            raise ValueError('max_seconds must be positive and finite')


@dataclass(frozen=True)
class Action:
    kind: str  # model or tool
    name: str
    input_json: str
    call_id: str = ''

    def arguments(self):
        """Return a fresh copy; a gate cannot mutate what will be executed."""
        return json.loads(self.input_json)


@dataclass(frozen=True)
class Grant:
    permission: bool = False
    in_scope: bool = False
    approved: bool = False


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: Mapping
    callback: Callable
    validate: Callable


@dataclass
class RunResult:
    reason: str
    messages: list
    usage: dict
    turns: int
    tool_calls: int
    events: list = field(default_factory=list)

    @property
    def text(self):
        for m in reversed(self.messages):
            if m.get('role') == 'assistant':
                return ''.join(b.get('text', '') for b in m.get('content', []) if b.get('type') == 'text')
        return ''


class RuntimeContext(dict):
    """Mapping compatible with Recorder, plus cooperative cancellation/deadline.

    A decorator that copies mappings loses the methods, so `runtime_context` is
    also carried in metadata for injected transports/tools to call check().
    """
    def __init__(self, metadata, token, deadline, clock, *, usage=None, max_tokens=None):
        super().__init__(metadata or {})
        self.token, self.deadline, self.clock = token, deadline, clock
        self['runtime_context'] = self
        self._usage, self._max_tokens = usage, max_tokens
        self._ledger_active=usage is not None

    @property
    def remaining_tokens(self):
        if not self._ledger_active or self._usage is None or self._max_tokens is None:
            raise RunStopped('usage_ledger_unavailable')
        return max(0,self._max_tokens-self._usage['input_tokens']-self._usage['output_tokens'])

    def charge_auxiliary_usage(self, used):
        # Account known auxiliary cost even if cancellation/staleness occurred
        # during the call; those fences run immediately afterward.
        if not self._ledger_active or self._usage is None or type(used) is not dict:
            raise RunStopped('invalid_usage')
        if set(used)-set(self._usage) or any(type(v) is not int or v<0 for v in used.values()):
            raise RunStopped('invalid_usage')
        for key,value in used.items():self._usage[key]+=value
        if self.remaining_tokens<=0:raise RunStopped('token_budget')

    def check(self):
        cancelled = getattr(self.token, 'cancelled', False)
        if callable(cancelled):
            cancelled = cancelled()
        if cancelled or self.get('cancelled') is True:
            raise RunStopped('cancelled')
        err = getattr(self.token, 'err', None)
        if callable(err) and err() is not None:
            raise RunStopped('cancelled')
        now = self.clock()
        if not isinstance(now, (int, float)) or not math.isfinite(now):
            raise RunStopped('invalid_clock')
        if now >= self.deadline:
            raise RunStopped('timeout')

    def err(self):
        try:
            self.check()
        except RunStopped as exc:
            return exc
        return None


def _json(value):
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        encoded.encode('utf-8')
        return encoded
    except (ValueError, TypeError, UnicodeError):
        raise RunStopped('invalid_protocol') from None


def _mapping(value):
    if is_dataclass(value):
        value = asdict(value)
    if not isinstance(value, Mapping):
        raise RunStopped('invalid_protocol')
    return deepcopy(dict(value))


def _arguments(value):
    if isinstance(value, (str, bytes, bytearray)):
        try:
            value = json.loads(value)
        except (ValueError, UnicodeError):
            raise RunStopped('invalid_protocol') from None
    if not isinstance(value, Mapping):
        raise RunStopped('invalid_protocol')
    # Round trip isolates values and rejects NaN / non-JSON objects.
    return json.loads(_json(dict(value)))


def _result(call_id, text, error=True):
    return {'type': 'tool_result', 'tool_use_id': call_id,
            'content': [{'type': 'text', 'text': text}], 'is_error': error}


class BoundedAgent:
    def __init__(self, provider, *, gate=None, tools=(), limits=Limits(), clock=time.monotonic):
        if not callable(getattr(provider, 'complete', None)):
            raise ValueError('An injected completion provider is required')
        self.provider, self.gate, self.limits, self.clock = provider, gate, limits, clock
        self.tools = {}
        for tool in tools:
            if not isinstance(tool, Tool) or not tool.name or tool.name in self.tools:
                raise ValueError('Tools must have unique nonempty names')
            if not callable(tool.callback) or not callable(tool.validate):
                raise ValueError('Every tool requires a callback and input validator')
            self.tools[tool.name] = Tool(tool.name, tool.description, _arguments(tool.input_schema), tool.callback, tool.validate)

    def _authorize(self, context, action):
        context.check()
        if not callable(self.gate):
            raise RunStopped('permission_denied')
        try:
            grant = self.gate(context, action)
        except RunStopped:
            raise
        except Exception:
            raise RunStopped('gate_error') from None
        context.check()  # Cancellation while waiting for approval never executes.
        if not isinstance(grant, Grant) or not (grant.permission is True and grant.in_scope is True and grant.approved is True):
            raise RunStopped('permission_denied')

    def run(self, request, *, token=None, metadata=None):
        limits = self.limits
        messages, events = [], []
        usage = dict(input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0)
        context = RuntimeContext(metadata, token, self.clock() + limits.max_seconds, self.clock,usage=usage,max_tokens=limits.max_tokens)
        turns = tool_calls = 0
        reason = 'max_turns'
        seen = set()
        try:
            base = _mapping(request)
            messages = deepcopy(base.pop('messages', []))
            if not isinstance(messages, list):
                raise RunStopped('invalid_protocol')
            # Validate initial history using the shared strict provider boundary.
            # No serialization output is used and no HTTP transport is invoked.
            from ..providers import Config, ProtocolError, serialize
            try:
                serialize(Config('openai', 'history-validation'), {'messages': messages})
            except (ProtocolError, TypeError, KeyError, AttributeError):
                raise RunStopped('invalid_protocol') from None
            for message in messages:
                for block in message.get('content', []):
                    if block.get('type') == 'tool_use':
                        seen.add(block['id'])
            # Only host-registered tools are advertised; caller/model cannot add one.
            base['tools'] = [{'name': t.name, 'description': t.description, 'input_schema': deepcopy(t.input_schema)} for t in self.tools.values()]
            requested_cap = base.get('max_tokens', limits.max_tokens)
            if type(requested_cap) is not int or requested_cap < 0:
                raise RunStopped('invalid_protocol')
            for _ in range(limits.max_turns):
                context.check()
                remaining = limits.max_tokens - usage['input_tokens'] - usage['output_tokens']
                if remaining <= 0:
                    raise RunStopped('token_budget')
                payload = {**deepcopy(base), 'messages': deepcopy(messages), 'max_tokens': min(requested_cap or remaining, remaining)}
                serialized = _json(payload)
                if len(serialized.encode('utf-8')) > limits.max_request_bytes:
                    raise RunStopped('request_budget')
                self._authorize(context, Action('model', 'complete', serialized))
                try:
                    returned, stop, used = self.provider.complete(context, json.loads(serialized))
                except RunStopped:
                    raise
                except Exception:
                    context.check()
                    raise RunStopped('model_error') from None
                turns += 1
                message = _mapping(returned)
                if message.get('role') != 'assistant' or not isinstance(message.get('content'), list):
                    raise RunStopped('invalid_protocol')
                used = _mapping(used)
                for key in usage:
                    value = used.get(key, 0)
                    if type(value) is not int or value < 0:
                        raise RunStopped('invalid_usage')
                    usage[key] += value
                allowed_stops = {'end_turn', 'stop_sequence', 'tool_use', 'max_tokens', 'refusal', 'refused', 'content_filter', 'pause_turn'}
                if not isinstance(stop, str) or stop not in allowed_stops:
                    raise RunStopped('invalid_protocol')
                # Validate the ENTIRE assistant turn before admitting any callback.
                _json(message)  # Reject non-JSON values and malformed Unicode.
                uses = []
                for block in message['content']:
                    if not isinstance(block, Mapping):
                        raise RunStopped('invalid_protocol')
                    if block.get('type') == 'tool_use':
                        if not isinstance(block.get('id'), str) or not block['id'] or block['id'] in seen or not isinstance(block.get('name'), str) or not block['name']:
                            raise RunStopped('invalid_protocol')
                        seen.add(block['id'])
                        block['input'] = _arguments(block.get('input', {}))
                        uses.append(block)
                    elif block.get('type') in ('text', 'thinking'):
                        key = 'text' if block['type'] == 'text' else 'thinking'
                        if not isinstance(block.get(key), str):
                            raise RunStopped('invalid_protocol')
                        if 'signature' in block and not isinstance(block['signature'], str):
                            raise RunStopped('invalid_protocol')
                    else:
                        raise RunStopped('invalid_protocol')
                refusal = message.get('refusal')
                if refusal is not None and not isinstance(refusal, str):
                    raise RunStopped('invalid_protocol')
                if refusal:
                    stop = 'refusal'
                terminal_stops = {'refusal', 'refused', 'content_filter', 'max_tokens', 'pause_turn'}
                if (uses and stop not in terminal_stops and stop != 'tool_use') or (not uses and stop == 'tool_use'):
                    raise RunStopped('invalid_protocol')
                messages.append(message)
                events.append({'type': 'model_complete', 'turn': turns, 'stop_reason': stop})
                terminal = None
                try:
                    context.check()
                    if usage['input_tokens'] + usage['output_tokens'] >= limits.max_tokens:
                        raise RunStopped('token_budget')
                    if stop in ('refusal', 'content_filter', 'refused'):
                        raise RunStopped('refused')
                    if stop == 'max_tokens':
                        raise RunStopped('output_limit')
                    if stop == 'pause_turn':
                        raise RunStopped('provider_paused')
                except RunStopped as exc:
                    terminal = exc.reason
                results = []
                for use in uses:
                    if terminal:
                        results.append(_result(use['id'], 'Not executed: run stopped (' + terminal + ').'))
                        continue
                    try:
                        context.check()
                        if tool_calls >= limits.max_tool_calls:
                            raise RunStopped('tool_budget')
                        tool = self.tools.get(use['name'])
                        if tool is None:
                            raise RunStopped('unknown_tool')
                        arguments = _arguments(use['input'])
                        try:
                            valid = tool.validate(deepcopy(arguments))
                        except RunStopped:
                            raise
                        except Exception:
                            raise RunStopped('invalid_tool_input') from None
                        if valid is not True:
                            raise RunStopped('invalid_tool_input')
                        action = Action('tool', tool.name, _json(arguments), use['id'])
                        self._authorize(context, action)
                        # Last cooperative boundary before callback execution.
                        context.check()
                        tool_calls += 1
                        try:
                            output = tool.callback(context, action.arguments())
                        except RunStopped:
                            raise
                        except Exception:
                            raise RunStopped('tool_error') from None
                        if not isinstance(output, str):
                            raise RunStopped('invalid_tool_output')
                        try:
                            raw = output.encode('utf-8')
                        except UnicodeError:
                            raise RunStopped('invalid_tool_output') from None
                        if len(raw) > limits.max_tool_output_bytes:
                            marker = b'\n[Output truncated by runtime limit]'
                            if limits.max_tool_output_bytes <= len(marker):
                                output = marker[:limits.max_tool_output_bytes].decode('ascii')
                            else:
                                output = raw[:limits.max_tool_output_bytes-len(marker)].decode('utf-8', errors='ignore') + marker.decode('ascii')
                        results.append(_result(use['id'], output or '(no output)', False))
                        events.append({'type': 'tool_result', 'call_id': use['id'], 'name': tool.name, 'status': 'completed'})
                        context.check()
                    except RunStopped as exc:
                        terminal = exc.reason
                        if not any(r['tool_use_id'] == use['id'] for r in results):
                            text = ('Tool callback stopped or failed; effects may have occurred. Do not retry automatically.'
                                    if exc.reason in ('tool_error', 'invalid_tool_output') else 'Not completed: ' + exc.reason + '. Do not retry automatically.')
                            results.append(_result(use['id'], text))
                        events.append({'type': 'tool_blocked_or_stopped', 'call_id': use['id'], 'reason': terminal})
                if results:
                    messages.append({'role': 'user', 'content': results})
                if terminal:
                    raise RunStopped(terminal)
                if not uses:
                    reason = 'completed'
                    break
        except RunStopped as exc:
            reason = exc.reason
        finally:
            context._ledger_active=False
        return RunResult(reason, messages, usage, turns, tool_calls, events)
