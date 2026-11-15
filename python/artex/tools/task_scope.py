"""Opt-in source add_task_scope metadata tool, not authorization discovery.

The host must bind current(scope) and lifecycle_guard(scope) to exact task,
exploration, run, revision AND allowed status (paused tasks are not implicitly
writable). Store parent/archive SQL checks alone cannot authorize a run. Store
approve must independently authorize exact normalized rows; model arguments or
this tool's descriptions confer no authority. No default registration.

Source single/batch/reason/result shape is preserved. Each accepted entry is its
own transaction, as Go's tool allows partial success. Invalid or denied entries
get indexed errors; cancellation, stale scope or uncertain DB outcomes abort.
Already committed entries remain committed on later interruption: never blindly
retry an interrupted invocation. Deduplicated entries still appear in added,
matching source ON CONFLICT behavior. Company names/root promotion unsupported.
"""
from copy import deepcopy
from dataclasses import asdict
import json
import re
from ..agent.graph_context import ContextScope
from ..agent.runtime import Action,Grant,RunStopped,Tool
from ..db.task_scope import TaskScopeStore,ScopeInvalid,ScopeDenied,normalize
from ..db.tool_catalog import CatalogTool

NAME='add_task_scope'
DESCRIPTION=('Add independently approved scope metadata to the current task. Kinds: company (existing numeric ID), root_domain (explicit exact domain, including its subdomains), subdomain (exact hostname), ip, cidr, icp, keyword. Supply an auditable reason. Multiple entries share the reason. This tool never grants its own authority or infers broader company/root scope.')
SCHEMA={'type':'object','properties':{
    'entries':{'type':'array','items':{'type':'object','properties':{'kind':{'type':'string'},'value':{'type':'string'}},'required':['kind','value'],'additionalProperties':False}},
    'kind':{'type':'string'},'value':{'type':'string'},'reason':{'type':'string'}},'additionalProperties':False}

class TaskScopeTools:
    def __init__(self,store,scope,*,gate,current,lifecycle_guard):
        if type(store) is not TaskScopeStore or type(scope) is not ContextScope or store.task_id!=scope.task_id:
            raise ValueError('Exact TaskScopeStore and task context required')
        if any(type(v) is not int or not 0<v<2**63 for v in (scope.task_id,scope.exploration_id,scope.run_id)) or type(scope.revision) is not str or not scope.revision or len(scope.revision)>1024:
            raise ValueError('Valid task/exploration/run/revision required')
        if not all(callable(v) for v in (gate,current,lifecycle_guard)):raise ValueError('Explicit gate, current and lifetime guard required')
        self.store,self.scope=store,deepcopy(scope)
        self._store,self._executor,self._scope=store,store.executor,self.scope
        self.gate,self.current,self.guard=gate,current,lifecycle_guard

    def _fence(self,context):
        if not isinstance(context,dict) or not callable(getattr(context,'check',None)):raise RunStopped('scope_mismatch')
        context.check()
        def exact():
            if self.store is not self._store or self.store.executor is not self._executor or self.scope is not self._scope or self.store.task_id!=self._scope.task_id:raise RunStopped('scope_mismatch')
            for key,value in asdict(self.scope).items():
                if type(context.get(key)) is not type(value) or context[key]!=value:raise RunStopped('scope_mismatch')
        exact()
        if self.current(self.scope) is not True:raise RunStopped('stale_context')
        context.check();exact()

    def validate(self,args):
        if type(args) is not dict or set(args)-set(SCHEMA['properties']):raise ScopeInvalid('Invalid scope arguments')
        for key in ('kind','value','reason'):
            if key in args and type(args[key]) is not str:raise ScopeInvalid('Text fields required')
        entries=args.get('entries',[])
        if type(entries) is not list or len(entries)>self.store.max_batch:raise ScopeInvalid('Bounded entries required')
        for entry in entries:
            if type(entry) is not dict or set(entry)!={'kind','value'} or any(type(v) is not str for v in entry.values()):raise ScopeInvalid('Each entry requires kind/value text')
        try:data=json.dumps(args,ensure_ascii=False,allow_nan=False).encode('utf-8')
        except (ValueError,UnicodeError):raise ScopeInvalid('Invalid scope arguments') from None
        if len(data)>65536:raise ScopeInvalid('Scope arguments too large')
        return True

    def call(self,context,args):
        self.validate(args)
        args=deepcopy(args)
        items=args.get('entries') or [{'kind':args.get('kind',''),'value':args.get('value','')}]
        reason=args.get('reason','');added=[];errors={}
        with self.guard(self.scope):
            self._fence(context)
            # Preserve store authority, adding runtime fences INSIDE its transaction
            # (including after company/grant callbacks), not only around the call.
            def current():
                self._fence(context)
                allowed=self.store.current()
                self._fence(context)
                return allowed
            bound=TaskScopeStore(self.store.executor,self.scope.task_id,lifecycle_guard=self.store.guard,current=current,
                approve=self.store.approve,company_allowed=self.store.company_allowed,max_batch=self.store.max_batch,expected_exploration_id=self._scope.exploration_id)
            for index,item in enumerate(items):
                self._fence(context)
                try:entry=normalize(self.scope.task_id,item['kind'].strip(),item['value'],reason=reason,source='agent')
                except ScopeInvalid:
                    errors[str(index)]='Invalid or unsupported scope entry';continue
                payload=json.dumps({'kind':item['kind'].strip(),'value':item['value'],'reason':reason},sort_keys=True,separators=(',',':'))
                grant=self.gate(context,Action('tool',NAME,payload))
                self._fence(context)
                if not isinstance(grant,Grant) or not all(v is True for v in (grant.permission,grant.in_scope,grant.approved)):
                    errors[str(index)]='Scope permission denied';continue
                try:bound.add_many((entry,))
                except ScopeDenied:
                    self._fence(context)
                    errors[str(index)]='Scope entry not approved or unavailable';continue
                self._fence(context)
                added.append({k:getattr(entry,k) for k in ('kind','domain','net','value','company_id')})
            self._fence(context)
        self._fence(context)
        result={'added':added or None} # Go nil slice encodes null when all entries fail.
        if errors:result['errors']=errors
        return json.dumps(result,ensure_ascii=False,separators=(',',':'))

    def tools(self):return (Tool(NAME,DESCRIPTION,deepcopy(SCHEMA),self.call,self.validate),)

    def catalog(self,*,agents):
        if type(agents) not in (list,tuple) or not 1<=len(agents)<=100 or any(type(a) is not str or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}',a) for a in agents) or len(set(agents))!=len(agents):raise ValueError('Explicit bounded unique agent keys required')
        return (CatalogTool(NAME,True,DESCRIPTION,deepcopy(SCHEMA),list(agents),True,'builtin',{},False),)
