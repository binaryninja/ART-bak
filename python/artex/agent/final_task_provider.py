"""Explicit persisted planner binding for one live restricted final invocation.

No receipt-only authorization, model selection fallback, task clock stamping or
admission. The runner owns invocation lifetime and independently bounded context;
the existing resolver owns persisted configuration and profile read permissions.
"""
from copy import deepcopy
from .chat import ChatBinding
from .task_providers import TaskProviderResolver
from .runtime import RuntimeContext


class FinalProviderUnavailable(RuntimeError):pass


def _key(value):
    return (getattr(value,'__self__',None),getattr(value,'__func__',value))


class FinalTaskProviderAdapter:
    def __init__(self,resolver):
        if type(resolver) is not TaskProviderResolver:raise ValueError('Exact persisted task provider resolver required')
        self.resolver=resolver;self._resolver=resolver
        self._resources=resolver._resources;self._policies=resolver._pins()
        self._bind=_key(resolver.bind)

    def validate_bindings(self):
        r=self._resolver
        if self.resolver is not r or r._resources is not self._resources or _key(r.bind)!=self._bind:
            raise FinalProviderUnavailable('Final provider resources changed')
        r._resources_check(self._policies)
        return True

    def bind(self,invocation,model):
        from .final_assessment import FinalProviderInvocation
        if type(invocation) is not FinalProviderInvocation:raise ValueError('Original final invocation required')
        self.validate_bindings();invocation.validate_execution()
        if invocation.journal.executor is not self._resources[4]:raise FinalProviderUnavailable('Original journal/provider executor required')
        if model!=invocation.scope.model:raise FinalProviderUnavailable('Final model changed')
        # Binding performs scoped selection but never constructs a transport.
        binding=self._resolver.bind(invocation.scope,'planner',model,
            current=invocation.current,lifecycle_guard=invocation.guard)
        invocation.validate_execution();self.validate_bindings()
        return ChatBinding(model,_FinalProvider(self,invocation,binding))


class _FinalProvider:
    def __init__(self,owner,invocation,binding):
        self.owner,self.invocation,self.binding=owner,invocation,binding
        self._pins=(owner,invocation,binding,binding.provider)
        self._model=deepcopy(binding.model)
        self._scope=invocation.scope;self._clock=invocation.clock;self._deadline=invocation.deadline
        self._methods=tuple(_key(getattr(invocation,n)) for n in ('validate_execution','validate_context','current','guard'))
        self._name='stream' if binding.model.streaming else 'complete'
        self._transport=getattr(binding.provider,self._name)
        self._transport_key=_key(self._transport)

    def _fixed(self):
        o,i,b,p=self._pins
        if (self.owner is not o or self.invocation is not i or self.binding is not b or b.provider is not p
            or b.model!=self._model or i.scope is not self._scope or i.clock is not self._clock or i.deadline!=self._deadline
            or tuple(_key(getattr(i,n)) for n in ('validate_execution','validate_context','current','guard'))!=self._methods
            or _key(getattr(p,self._name,None))!=self._transport_key):
            raise FinalProviderUnavailable('Final invocation provider changed')
        o.validate_bindings()

    def _fence(self,context):
        self._fixed();i=self._pins[1]
        i.validate_execution()
        if type(context) is not RuntimeContext or context.clock is not self._clock:
            raise FinalProviderUnavailable('Original final runtime context required')
        i.validate_context(context)
        context.clamp_deadline(self._deadline,clock=self._clock)
        context.remaining_seconds()
        self._fixed();i.validate_context(context);i.current(self._scope)

    def complete(self,context,request):
        if self._name!='complete':raise FinalProviderUnavailable('Streaming final model requires stream')
        self._fence(context)
        result=self._transport(context,deepcopy(request))
        self._fence(context)
        return result

    def stream(self,context,request):
        # Lazy: not even a lifetime callback runs until first next().
        if self._name!='stream':raise FinalProviderUnavailable('Nonstreaming final model requires complete')
        self._fence(context);iterator=None;original=None
        try:
            iterator=iter(self._transport(context,deepcopy(request)))
            while True:
                self._fence(context)
                try:value=next(iterator)
                except StopIteration:break
                self._fence(context)
                yield value
        except GeneratorExit:
            raise
        except BaseException as exc:
            original=exc;raise
        finally:
            failure=None
            if iterator is not None:
                try:
                    close=getattr(iterator,'close',None)
                    if callable(close):close()
                except BaseException as exc:failure=exc
            try:self._fence(context)
            except BaseException as exc:
                if failure is None:failure=exc
            if failure is not None and original is None:raise failure
