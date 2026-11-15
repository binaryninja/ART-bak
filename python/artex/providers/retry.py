"""Source-pinned, opt-in retry layers; no default provider/network construction.

Norma v0.4.3 llm/{provider,retry,openai}.go: connect defaults to 3
retries, OpenAI empty replies to 2, exponential 0.5 seconds capped at 8.
ARTEX server/task_llm.go: same-provider precommit retry defaults to 2,
0.5 seconds capped at 4. Negative counts disable, zero uses defaults;
positive intervals replace backoff. Counts mean retries, not total attempts.

Wire as RetryProvider(Provider(config, RetryTransport(transport, policy)),
policy, format=config.format). Both layers must use the same Policy. The outer
context carries a shared attempt/deadline budget through the transport. No
replay after ANY delivered event (stricter than upstream metadata buffering).
OpenAI terminal metadata is buffered only until an empty reply is classified.
Unknown/structured provider errors are NOT guessed transient: this adapter lacks
the structured quota/error classification needed for full Go parity. Known HTTP
408/429/500/502/503/504 and transport failures are retryable; quota-aware hosts
must normalize quota failures to nonretryable status before this layer.

Only side-effect-free model providers/transports may be wrapped. This does not
wrap tool execution or guarantee provider billing idempotency. Cancellation and
time bounds are cooperative: a blocking provider/transport must enforce its own
timeout. Retry waits use <=50ms slices and check context before/after each slice.
"""
from copy import deepcopy
from dataclasses import dataclass
import math
import time

from . import HTTPError, IncompleteStreamError
from .http import TransportError, TransportTimeout, TransportCancelled
from ..db.profiles import RetryOverride


class RetryLimitError(TimeoutError): pass


@dataclass(frozen=True)
class Policy:
    rules: RetryOverride = RetryOverride()
    max_attempts: int = 64
    max_seconds: float = 120

    def __post_init__(self):
        if not isinstance(self.rules, RetryOverride): raise ValueError('invalid retry rules')
        for rule in (self.rules.connect, self.rules.empty, self.rules.stream):
            if type(rule.attempts) is not int or not -1 <= rule.attempts <= 20:
                raise ValueError('retry count out of bounds')
            if type(rule.interval_ms) is not int or not 0 <= rule.interval_ms <= 3600000:
                raise ValueError('retry interval out of bounds')
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 1024:
            raise ValueError('invalid aggregate attempt bound')
        if type(self.max_seconds) not in (int,float) or not math.isfinite(self.max_seconds) or self.max_seconds <= 0:
            raise ValueError('invalid retry deadline')

    def count(self, kind):
        value = getattr(self.rules, kind).attempts
        return (3 if kind == 'connect' else 2) if value == 0 else max(0,value)

    def delay(self, kind, attempt):
        fixed = getattr(self.rules, kind).interval_ms
        return fixed / 1000 if fixed else min(0.5 * 2**attempt, 4 if kind == 'stream' else 8)


class _Budget:
    def __init__(self, policy, clock):
        self.clock, self.deadline, self.left = clock, clock()+policy.max_seconds, policy.max_attempts
    def check(self):
        now = self.clock()
        if not math.isfinite(now) or now >= self.deadline:
            raise RetryLimitError('retry deadline reached')
    def consume(self):
        self.check()
        if self.left <= 0: raise RetryLimitError('aggregate retry attempt limit reached')
        self.left -= 1


class _Context(dict):
    def __init__(self, original, budget):
        super().__init__(original if isinstance(original, dict) else {})
        self.original, self.budget = original, budget
    def check(self):
        check = getattr(self.original,'check',None)
        if callable(check): check()
        error = self.err()
        if error is not None:
            if isinstance(error,BaseException): raise error
            raise TransportCancelled('request canceled')
        self.budget.check()
    def err(self):
        err = getattr(self.original,'err',None)
        error = err() if callable(err) else None
        if error is not None: return error
        if isinstance(self.original,dict):
            if self.original.get('cancelled'): return TransportCancelled('request canceled')
            runtime = self.original.get('runtime_context')
            check = getattr(runtime,'check',None)
            if callable(check): check()
        return None


def _context(context, policy, clock):
    return context if isinstance(context,_Context) else _Context(context,_Budget(policy,clock))


def _transient(error):
    if isinstance(error, HTTPError): return error.status in (408,429,500,502,503,504)
    return isinstance(error,(TransportError,TransportTimeout,IncompleteStreamError,ConnectionResetError))


class _Layer:
    def __init__(self, policy, clock, sleep):
        if not isinstance(policy,Policy) or not callable(clock) or not callable(sleep):
            raise ValueError('invalid retry dependencies')
        self.policy, self.clock, self.sleep = policy,clock,sleep
    def _wait(self, context, kind, attempt):
        delay = self.policy.delay(kind,attempt)
        while delay > 0:
            context.check()
            step = min(delay,0.05,max(0,context.budget.deadline-context.budget.clock()))
            if step <= 0: raise RetryLimitError('retry deadline reached')
            self.sleep(step)
            delay -= step
            context.check()


class RetryTransport(_Layer):
    def __init__(self, transport, policy=Policy(), *, clock=time.monotonic, sleep=time.sleep):
        super().__init__(policy,clock,sleep)
        if not callable(transport): raise ValueError('explicit transport required')
        self.transport=transport
    def __call__(self, context, url, headers, body, stream):
        context=_context(context,self.policy,self.clock)
        for attempt in range(self.policy.count('connect')+1):
            context.check(); context.budget.consume()
            response=None
            try:
                response=self.transport(context,url,dict(headers),body,stream)
                context.check()
            except Exception as error:
                if response is not None: response.close()
                context.check()
                if attempt >= self.policy.count('connect') or not _transient(error): raise
            else:
                if response.status not in (408,429,500,502,503,504) or attempt >= self.policy.count('connect'):
                    return response
                response.close()
            self._wait(context,'connect',attempt)


class RetryProvider(_Layer):
    def __init__(self, provider, policy=Policy(), *, format, clock=time.monotonic, sleep=time.sleep):
        super().__init__(policy,clock,sleep)
        if format not in ('openai','anthropic'): raise ValueError('unsupported retry provider format')
        if not all(callable(getattr(provider,n,None)) for n in ('complete','stream')):
            raise ValueError('explicit provider required')
        self.provider,self.format=provider,format

    def complete(self, context, request):
        context=_context(context,self.policy,self.clock)
        request=deepcopy(request)
        for outer in range(self.policy.count('stream')+1):
            try:
                for empty in range(self.policy.count('empty')+1):
                    context.check(); context.budget.consume()
                    result=self.provider.complete(context,deepcopy(request))
                    context.check()
                    msg,stop,usage=result
                    retry_empty=(self.format=='openai' and isinstance(msg,dict) and
                                 msg.get('content')==[] and not msg.get('refusal') and stop in ('','end_turn'))
                    if not retry_empty or empty >= self.policy.count('empty'): return result
                    self._wait(context,'empty',empty)
            except Exception as error:
                context.check()
                if outer >= self.policy.count('stream') or not _transient(error): raise
                self._wait(context,'stream',outer)

    def stream(self, context, request):
        return self._stream(_context(context,self.policy,self.clock),deepcopy(request))

    def _stream(self, context, request):
        empty=0
        outer=0
        while True:
            delivered=False
            iterator=None
            pending=[]
            stop=''
            stopped=False
            refused=False
            try:
                context.check(); context.budget.consume()
                iterator=iter(self.provider.stream(context,deepcopy(request)))
                for event in iterator:
                    context.check()
                    kind=event.get('type') if isinstance(event,dict) else None
                    # Only OpenAI's bounded terminal metadata may be deferred.
                    if self.format=='openai' and not delivered and kind in ('message_start','message_delta','message_stop'):
                        if len(pending)>=8: raise ValueError('excessive empty stream metadata')
                        pending.append(deepcopy(event))
                        if kind=='message_delta':
                            stop=event.get('stop_reason','')
                            refused = refused or stop in ('refusal','refused','content_filter')
                        if kind=='message_stop': stopped=True
                        continue
                    for item in pending:
                        delivered=True
                        yield item
                        context.check()
                    pending=[]
                    delivered=True
                    yield event
                    context.check()
                context.check()
                if self.format=='openai' and not delivered and stopped and not refused and stop in ('','end_turn') and empty < self.policy.count('empty'):
                    empty+=1
                    retry_kind,retry_index='empty',empty-1
                else:
                    for event in pending:
                        delivered=True
                        yield event
                        context.check()
                    return
            except Exception as error:
                context.check()
                if delivered or refused or outer >= self.policy.count('stream') or not _transient(error): raise
                retry_kind,retry_index='stream',outer
                outer+=1
                empty=0
            finally:
                close=getattr(iterator,'close',None)
                if callable(close): close()
            self._wait(context,retry_kind,retry_index)
