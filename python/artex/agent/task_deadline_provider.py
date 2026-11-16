"""Opt-in final-dispatch adapter; not installed by normal runtime assembly.

Only planner/worker facades sharing an original OwnedGenerationClock qualify.
Construction and stream creation are inert. The first actual provider boundary
stamps/validates once through the shared clock and shortens the SAME live context.
No thread, timer, retry, provider resolution, task completion or admission change.
Transport cooperation is still necessary to interrupt a blocking provider call.
"""
from copy import deepcopy
from .chat import ChatBinding
from .deadline import PreparedDispatchClock,OwnedGenerationClock,DispatchClockUnavailable
from .runtime import RuntimeContext


def _method_key(method):
    return getattr(method,'__self__',None),getattr(method,'__func__',method)


class DeadlineProvider:
    def __init__(self,binding,dispatch_clock):
        if type(binding) is not ChatBinding or type(dispatch_clock) is not PreparedDispatchClock:
            raise ValueError('Exact bound provider and prepared dispatch facade required')
        if type(dispatch_clock.generation_clock) is not OwnedGenerationClock:
            raise ValueError('Shared original generation clock required')
        if binding.model!=dispatch_clock.model or dispatch_clock.role not in ('planner','worker'):
            raise ValueError('Matching role and model required')
        method='stream' if binding.model.streaming else 'complete'
        transport=getattr(binding.provider,method,None)
        if not callable(transport):raise ValueError('Matching provider transport required')
        self.binding,self.dispatch_clock=binding,dispatch_clock
        self._binding_pin=binding;self._clock_pin=dispatch_clock
        self._provider=binding.provider;self._model=deepcopy(binding.model)
        self._method=method;self._transport=transport;self._transport_key=_method_key(transport)
        self._callbacks=(dispatch_clock.authorize,dispatch_clock.binding_current,
                         dispatch_clock.wall_clock,dispatch_clock.monotonic_clock)
        self._shared=dispatch_clock.generation_clock

    def _binding(self):
        c=self.dispatch_clock;b=self.binding
        if (b is not self._binding_pin or c is not self._clock_pin or b.provider is not self._provider
            or b.model!=self._model or c.model!=self._model or c.generation_clock is not self._shared
            or any(a is not b for a,b in zip((c.authorize,c.binding_current,c.wall_clock,c.monotonic_clock),self._callbacks))):
            raise DispatchClockUnavailable('Deadline provider binding changed')
        key=_method_key(getattr(self._provider,self._method,None))
        if any(a is not b for a,b in zip(key,self._transport_key)):
            raise DispatchClockUnavailable('Deadline transport changed')

    def _fence(self,context):
        self._binding()
        if type(context) is not RuntimeContext or context.clock is not self._callbacks[3]:
            raise DispatchClockUnavailable('Same original runtime clock required')
        # Read-only lifetime/configuration fence, not a fresh model permission.
        self.dispatch_clock.validate_dispatch(context)
        self._binding()
        context.remaining_seconds()

    def _dispatch(self,context,request):
        self._fence(context)
        if type(request) is not dict:raise ValueError('Provider request required')
        request=deepcopy(request)
        budget=self.dispatch_clock.before_dispatch(context,request,own_budget_seconds=context.remaining_seconds())
        self._binding()
        if budget.monotonic_deadline is None:raise DispatchClockUnavailable('Finite runtime ceiling required')
        context.clamp_deadline(budget.monotonic_deadline,clock=self._callbacks[3])
        self._fence(context)
        return request

    def complete(self,context,request):
        if self._method!='complete':raise DispatchClockUnavailable('Streaming model requires stream transport')
        prepared=self._dispatch(context,request)
        result=self._transport(context,prepared)
        self._fence(context)
        return result

    def stream(self,context,request):
        # Generator body is deliberately lazy: neither stamp nor provider call
        # occurs merely because a caller asks for an iterator.
        if self._method!='stream':raise DispatchClockUnavailable('Nonstreaming model requires complete transport')
        prepared=self._dispatch(context,request)
        iterator=None;failed=False
        try:
            iterator=iter(self._transport(context,prepared))
            while True:
                self._fence(context)
                try:event=next(iterator)
                except StopIteration:
                    self._fence(context)
                    return
                self._fence(context)
                yield event
        except GeneratorExit:
            # Explicit close is still a successful cleanup boundary unless its
            # close callback or final authority fence fails.
            raise
        except BaseException:
            failed=True
            raise
        finally:
            # Resource release must still happen after cancellation/revocation.
            # A close failure is surfaced; never retry or hide uncertain cleanup.
            if iterator is not None:
                close=getattr(iterator,'close',None)
                if callable(close):close()
                # A successful exhaustion/explicit close must not conceal a
                # close callback's revocation, rebind or elapsed deadline. When
                # dispatch already failed, preserve that original failure;
                # resource-close errors still propagate with its context.
                if not failed:self._fence(context)
