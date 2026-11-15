"""Executable catalog bridge over explicit host-owned callbacks only.

Source agent/assembly.go +toolcatalog.go: enabled/agent binding, model-facing
schema/description overrides and scalar defaults. Here every exposed handler
requires a catalog row (stricter than source pass-through defaults), same schema
structure as code, and fresh host Grant at dispatch. Stored command/script/http
exec is never evaluated, imported or promoted into permission. Such kinds are
unsupported; a host must register an independently approved native callback.

Deferred tools require explicit unlock; no SearchExtraTools/ExecuteExtraTool
aliases are invented. Unlock is discovery only, NOT execution approval. Reload
is explicit and rejected while any callback is active; old Tool handles are
revoked by local epoch. drain stops new calls and waits, does not preempt callbacks
or undo effects. Only a subsequent successful reload reopens admission.

current(revision) and lifecycle_guard(revision) must share the actual catalog,
policy, binding and scope change authority. Guard is held through callback, so
revocation waits for admitted effects. This is not a sandbox or distributed lease.
Process/filesystem handlers retain their own permission and containment checks.
No source discovery, defaults, threads, commands or network are started here.
"""
from copy import deepcopy
from dataclasses import dataclass
import json,math,time
from threading import Condition,RLock
from ..agent.runtime import Tool,Action,Grant
from ..db.tool_catalog import CatalogTool,validate,value_json,_structure

class RegistryUnavailable(RuntimeError):pass
class RegistryConflict(RuntimeError):pass
class RegistryDenied(RuntimeError):pass

@dataclass(frozen=True)
class RegistryDrain:
    drained:bool
    active:int

class ToolRegistry:
    def __init__(self,agent_key,*,handlers,current,lifecycle_guard,authorize):
        if not isinstance(agent_key,str) or not agent_key:raise ValueError('Explicit agent key required')
        if any(not callable(c) for c in (current,lifecycle_guard,authorize)):raise ValueError('Explicit revision and authorization adapters required')
        self.agent_key=agent_key;self.current=current;self.guard=lifecycle_guard;self.authorize=authorize
        self._handlers={}
        for tool in handlers:
            if not isinstance(tool,Tool) or tool.name in self._handlers or not callable(tool.callback) or not callable(tool.validate):raise ValueError('Unique explicit Tool handlers required')
            self._handlers[tool.name]=Tool(tool.name,tool.description,value_json(tool.input_schema),tool.callback,tool.validate)
        self._condition=Condition(RLock());self._epoch=0;self._revision=None;self._entries={};self._unlocked=set();self._active=0;self._paused=True

    def reload(self,records,revision):
        if not isinstance(revision,str) or not revision or len(revision)>256:raise ValueError('Explicit bounded catalog revision required')
        records=list(records)
        if len(records)>10000:raise ValueError('Catalog exceeds bound')
        entries={};seen=set()
        for row in records:
            validate(row,custom=False)
            if row.key in seen:raise ValueError('Duplicate catalog key')
            seen.add(row.key)
            if not row.enabled or self.agent_key not in row.agents:continue
            if row.kind!='builtin' or row.exec:raise RegistryUnavailable('Stored executable specifications are unsupported')
            handler=self._handlers.get(row.key)
            if handler is None:raise RegistryUnavailable('Enabled bound tool lacks explicit handler')
            schema=value_json(row.schema)
            if _structure(schema)!=_structure(handler.input_schema):raise RegistryUnavailable('Catalog schema structure differs from handler')
            entries[row.key]=(deepcopy(row),handler)
        with self._condition:
            if self._active:raise RegistryConflict('Active calls must drain before reload')
        with self.guard(revision):
            if self.current(revision) is not True:raise RegistryConflict('Catalog revision changed')
            with self._condition:
                if self._active:raise RegistryConflict('Active calls must drain before reload')
                self._entries=entries;self._revision=revision;self._epoch+=1;self._unlocked=set();self._paused=False

    def unlock(self,name,revision):
        with self.guard(revision):
            if self.current(revision) is not True:raise RegistryConflict('Catalog revision changed')
            with self._condition:
                if self._paused or revision!=self._revision or name not in self._entries:raise RegistryUnavailable('Tool discovery unavailable')
                self._unlocked.add(name)

    def tools(self):
        with self._condition:
            if self._paused:return ()
            epoch=self._epoch
            return tuple(Tool(name,row.description,deepcopy(row.schema),
                              lambda context,args,name=name:self._dispatch(epoch,name,context,args),
                              lambda args,name=name:self._validate(epoch,name,args))
                         for name,(row,handler) in self._entries.items() if not row.deferred or name in self._unlocked)

    def _entry(self,epoch,name):
        if self._paused or epoch!=self._epoch or name not in self._entries:raise RegistryUnavailable('Tool handle revoked')
        row,handler=self._entries[name]
        if row.deferred and name not in self._unlocked:raise RegistryUnavailable('Deferred tool locked')
        return row,handler

    @staticmethod
    def _arguments(row,handler,args):
        args=value_json(args)
        if not isinstance(args,dict):raise ValueError('Tool arguments must be an object')
        for name,schema in row.schema.get('properties',{}).items():
            if isinstance(schema,dict) and schema.get('type') in ('string','integer','number','boolean') and schema.get('default') is not None and (name not in args or args[name] is None or args[name]==''):
                args[name]=deepcopy(schema['default'])
        if handler.validate(deepcopy(args)) is False:raise ValueError('Invalid tool arguments')
        return args

    def _validate(self,epoch,name,args):
        with self._condition:row,handler=self._entry(epoch,name)
        self._arguments(row,handler,args);return True

    def _dispatch(self,epoch,name,context,args):
        check=getattr(context,'check',None)
        if not callable(check):raise RegistryDenied('Cancellation-aware context required')
        with self._condition:
            row,handler=self._entry(epoch,name);revision=self._revision;self._active+=1
        try:
            with self.guard(revision):
                check()
                if self.current(revision) is not True:raise RegistryConflict('Tool policy revision changed')
                arguments=self._arguments(row,handler,args)
                action=Action('tool',name,json.dumps(arguments,allow_nan=False,separators=(',',':')))
                grant=self.authorize(context,action)
                if not isinstance(grant,Grant) or any(flag is not True for flag in (grant.permission,grant.in_scope,grant.approved)):raise RegistryDenied('Tool execution denied')
                check()
                if self.current(revision) is not True:raise RegistryConflict('Tool policy changed during approval')
                return handler.callback(context,action.arguments())
        finally:
            with self._condition:self._active-=1;self._condition.notify_all()

    def drain(self,timeout=5.0):
        if type(timeout) not in (int,float) or not math.isfinite(timeout) or timeout<0:raise ValueError('Finite nonnegative timeout required')
        deadline=time.monotonic()+timeout
        with self._condition:
            self._paused=True
            while self._active:
                remaining=deadline-time.monotonic()
                if remaining<=0:break
                self._condition.wait(remaining)
            return RegistryDrain(not self._active,self._active)
