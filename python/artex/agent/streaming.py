"""Bounded provider-stream normalization for the synchronous agent turn boundary.

Consumes the normalized event protocol from providers, Pool and Recorder, never
raw SSE. Buffers the COMPLETE assistant turn before BoundedAgent validates calls
and authorizes any tools. There are no retries, tool callbacks, background tasks
or synthetic nonstream profile changes. Usage fields are cumulative snapshots
per turn (Anthropic start input and later output; OpenAI final usage), not summed
per fragment. Progress is optional provisional data, never durable completion.

Cancellation and an explicit live-generation/profile fence run before/after each
iterator advance and before progress delivery. Blocking next()/close() still
requires transport deadlines/cooperation. The owned iterator is closed on every
exit; cleanup failure is an error, never a successfully completed response.
An optional trusted usage_callback receives validated cumulative-counter
increments before the post-read live fence, retaining known costs on stale or
cancelled summaries without exposing content or authorizing further actions.
"""
from copy import deepcopy
from dataclasses import dataclass
import json
from .runtime import RunStopped


@dataclass(frozen=True)
class StreamLimits:
    max_events: int = 100000
    max_bytes: int = 4 * 1024 * 1024
    max_blocks: int = 1024

    def __post_init__(self):
        for value in (self.max_events, self.max_bytes, self.max_blocks):
            if type(value) is not int or value <= 0:
                raise ValueError('Positive stream bounds required')


class StreamProtocolError(ValueError):
    pass


def _text(value):
    if not isinstance(value, str):
        raise StreamProtocolError('Invalid streamed text')
    value.encode('utf-8')
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise StreamProtocolError('Duplicate tool argument key')
        result[key] = value
    return result


class StreamingCompletion:
    def __init__(self, provider, *, check, limits=StreamLimits(), progress=None, usage_callback=None):
        if not callable(getattr(provider, 'stream', None)) or not callable(check):
            raise ValueError('Explicit streaming provider and live fence required')
        if not isinstance(limits, StreamLimits) or (progress is not None and not callable(progress)):
            raise ValueError('Invalid stream configuration')
        if usage_callback is not None and not callable(usage_callback):raise ValueError('Invalid usage callback')
        self.usage_callback=usage_callback
        self.provider, self.check, self.limits, self.progress = provider, check, limits, progress

    def complete(self, context, request):
        def fence():
            context.check()
            self.check()
            context.check()
        fence()
        iterator = None
        started = stopped = refused = False
        reason = ''
        usage = {'input_tokens': 0, 'output_tokens': 0, 'cache_read_tokens': 0, 'cache_write_tokens': 0}
        blocks, order, ids = {}, [], set()
        event_count = byte_count = 0
        output_cap = request.get('max_tokens')
        if type(output_cap) is not int or output_cap <= 0:
            raise StreamProtocolError('Bounded output-token request required')

        def block(key, kind):
            if key not in blocks:
                if len(blocks) >= self.limits.max_blocks:
                    raise RunStopped('stream_budget')
                blocks[key] = {'type': kind}
                order.append(key)
            elif blocks[key]['type'] != kind:
                raise StreamProtocolError('Conflicting stream block types')
            return blocks[key]

        try:
            iterator = iter(self.provider.stream(context, request))
            while True:
                fence()
                try:
                    event = next(iterator)
                except StopIteration:
                    break
                event_count += 1
                if event_count > self.limits.max_events:
                    raise RunStopped('stream_budget')
                if type(event) is not dict:
                    raise StreamProtocolError('Invalid stream event')
                byte_count += len(json.dumps(event, ensure_ascii=False, allow_nan=False).encode('utf-8'))
                if byte_count > self.limits.max_bytes:
                    raise RunStopped('stream_budget')
                kind = event.get('type')
                if 'usage' in event:
                    if kind not in {'message_start', 'message_delta'} or type(event['usage']) is not dict:
                        raise StreamProtocolError('Invalid stream usage')
                    delta={key:0 for key in usage}
                    for field, value in event['usage'].items():
                        if field not in usage or type(value) is not int or value < 0:
                            raise StreamProtocolError('Invalid stream usage counter')
                        delta[field]=max(0,value-usage[field])
                        usage[field] = max(usage[field], value)
                    if self.usage_callback is not None:self.usage_callback(delta)
                    if usage['output_tokens'] > output_cap:
                        raise RunStopped('token_budget')
                fence()
                if kind == 'message_start':
                    if started:
                        raise StreamProtocolError('Duplicate stream start')
                    started = True
                    if 'model' in event:
                        _text(event['model'])
                elif not started:
                    raise StreamProtocolError('Event before stream start')
                elif kind == 'message_stop':
                    if not reason:
                        raise StreamProtocolError('Missing terminal stop reason')
                    stopped = True
                elif kind == 'message_delta':
                    value = event.get('stop_reason', '')
                    if value:
                        if value not in {'end_turn', 'stop_sequence', 'tool_use', 'max_tokens', 'refusal', 'refused', 'content_filter', 'pause_turn'}:
                            raise StreamProtocolError('Unknown stream stop reason')
                        refused = refused or value in {'refusal', 'refused', 'content_filter'}
                        if reason and reason != value and not refused:
                            raise StreamProtocolError('Conflicting stream stop reasons')
                        reason = value
                elif kind in {'text_delta', 'thinking_delta', 'thinking_signature', 'tool_use_start', 'tool_input_delta'}:
                    if reason:
                        raise StreamProtocolError('Content after terminal metadata')
                    index = event.get('index')
                    if index is not None and (type(index) is not int or index < 0):
                        raise StreamProtocolError('Invalid stream block index')
                    # OpenAI text/thinking deltas are unindexed; tool indexes
                    # live separately from those two synthesized text blocks.
                    key = index if index is not None else ('unindexed', 'thinking' if kind.startswith('thinking') else 'text')
                    if kind == 'tool_use_start':
                        if index is None or key in blocks:
                            raise StreamProtocolError('Duplicate or unindexed tool start')
                        ident, name = _text(event.get('tool_id')), _text(event.get('tool_name'))
                        if not ident or not name or ident in ids:
                            raise StreamProtocolError('Invalid tool identity')
                        ids.add(ident)
                        block(key, 'tool_use').update(id=ident, name=name, fragments='')
                    elif kind == 'tool_input_delta':
                        if key not in blocks or blocks[key]['type'] != 'tool_use' or event.get('tool_id') != blocks[key]['id']:
                            raise StreamProtocolError('Unpaired tool fragment')
                        blocks[key]['fragments'] += _text(event.get('text'))
                    else:
                        btype = 'thinking' if kind.startswith('thinking') else 'text'
                        current = block(key, btype)
                        field = 'signature' if kind == 'thinking_signature' else btype
                        current[field] = current.get(field, '') + _text(event.get('text'))
                else:
                    raise StreamProtocolError('Unsupported stream event')
                if self.progress is not None:
                    fence()
                    self.progress(deepcopy(event))
                    fence()
                if stopped:
                    break
            if not started or not stopped:
                raise StreamProtocolError('Stream ended without complete terminal sequence')
            content = []
            for key in order:
                current = blocks[key]
                if current['type'] == 'tool_use':
                    raw = current.pop('fragments')
                    def bad_constant(_):
                        raise StreamProtocolError('Nonfinite tool argument')
                    value = json.loads(raw or '{}', object_pairs_hook=_pairs, parse_constant=bad_constant)
                    if type(value) is not dict:
                        raise StreamProtocolError('Tool arguments must be an object')
                    current['input'] = value
                elif current['type'] == 'thinking' and 'thinking' not in current:
                    raise StreamProtocolError('Signature without thinking content')
                content.append(current)
            fence()
        finally:
            if iterator is not None:
                close = getattr(iterator, 'close', None)
                if callable(close):
                    try:
                        close()
                    except BaseException:
                        raise StreamProtocolError('Stream cleanup unconfirmed') from None
        # Closing an iterator can run provider/recorder finalizers and block.
        # Revalidate only after owned cleanup before exposing executable calls.
        fence()
        return {'role': 'assistant', 'content': content}, 'refusal' if refused else reason, usage
