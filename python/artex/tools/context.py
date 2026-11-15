"""Explicit local read-only graph tools; no SQL or default registration.

Source names/schemas: tools.go graph_overview({}) and tools_digest.go
expand_digest({id: int64}). GraphContext supplies the actual bounded transaction
view. Source expand_digest uses writeExpTool despite performing reads; this
adapter still requires a fresh explicit Grant on EVERY read. It never inherits
write authority, enables tools from descriptions, or resolves other tasks.

Scope metadata must exactly match the frozen ContextScope on each call, matching
PlannerScope.identity's task/exploration/run/revision fields. Current/cancel
fences surround the gate and returned data; GraphContext also owns its lifecycle
transaction barrier. A host must construct a new adapter for a new run/revision.
Read failures/oversize output fail closed, never return partial graph summaries.

GraphContext's unavailable sections remain explicit: inherited task union,
asset/site coverage, traffic references and worker budgets are NOT supplied.
Only persisted local digests expand. catalog(agents=...) returns explicit native
metadata suitable for PlannerCycle.additional_catalog; tools() supplies matching
additional_tools. Neither call registers, seeds, grants or starts anything.
"""
from copy import deepcopy
from dataclasses import asdict
import json
import re
from ..agent.graph_context import ContextScope,GraphContext
from ..agent.runtime import Action,Grant,RunStopped,Tool
from ..db.core import int64
from ..db.tool_catalog import CatalogTool


class ContextToolError(ValueError):pass

_DESCRIPTIONS={
    'graph_overview':'Read the bounded local exploration graph overview: goals, frontier, findings, facts and strategic hints. Unsupported inherited/asset/traffic/budget sections are explicitly identified.',
    'expand_digest':'Expand a persisted local cold digest into its compact member list (id/summary/state/confidence). Use node_detail(member_id), if separately available and authorized, for full evidence.',
}
_SCHEMAS={
    'graph_overview':{'type':'object','properties':{},'additionalProperties':False},
    'expand_digest':{'type':'object','properties':{'id':{'type':'integer','description':'Local digest node ID from cold_digests.'}},'required':['id'],'additionalProperties':False},
}


class GraphContextTools:
    def __init__(self,graph,scope,*,gate,current,max_output_bytes=1048576):
        if not isinstance(graph,GraphContext) or not isinstance(scope,ContextScope) or graph.scope!=scope:
            raise ValueError('Explicit matching local GraphContext and scope required')
        if not callable(gate) or not callable(current):raise ValueError('Fresh grant and lifecycle authority required')
        if type(max_output_bytes) is not int or not 1<=max_output_bytes<=16777216:
            raise ValueError('Invalid context tool output bound')
        self.graph,self.scope,self.gate,self.current=graph,deepcopy(scope),gate,current
        self.max_output_bytes=max_output_bytes

    def _fence(self,context):
        check=getattr(context,'check',None)
        if not callable(check) or not isinstance(context,dict):raise ContextToolError('Scoped runtime context required')
        check()
        for key,value in asdict(self.scope).items():
            if type(context.get(key)) is not type(value) or context[key]!=value:
                raise RunStopped('scope_mismatch')
        if self.graph.scope!=self.scope or self.current(self.scope) is not True:
            raise RunStopped('stale_context')
        check()
        if self.graph.scope!=self.scope:raise RunStopped('stale_context')
        for key,value in asdict(self.scope).items():
            if type(context.get(key)) is not type(value) or context[key]!=value:
                raise RunStopped('scope_mismatch')

    @staticmethod
    def validate(name,args):
        if name not in _SCHEMAS or type(args) is not dict:raise ContextToolError('Unknown graph tool or arguments')
        if name=='graph_overview':
            if args:raise ContextToolError('graph_overview accepts no arguments')
        else:
            if set(args)!={'id'} or int64(args['id'],'digest ID')<=0:
                raise ContextToolError('Exactly one positive local digest ID required')
        return True

    def call(self,name,context,args):
        self.validate(name,args)
        encoded=json.dumps(args,sort_keys=True,separators=(',',':'),allow_nan=False)
        # Canonical immutable snapshot prevents gate/caller mutation of arguments.
        immutable=json.loads(encoded)
        self._fence(context)
        grant=self.gate(context,Action('tool',name,encoded))
        self._fence(context)
        if not isinstance(grant,Grant) or not all(v is True for v in (grant.permission,grant.in_scope,grant.approved)):
            raise RunStopped('permission_denied')
        result=self.graph.snapshot() if name=='graph_overview' else self.graph.expand_digest(immutable['id'])
        self._fence(context)
        output=json.dumps(result,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
        if len(output.encode('utf-8'))>self.max_output_bytes:raise ContextToolError('Graph result exceeds tool output bound')
        self._fence(context)
        return output

    def tools(self):
        return tuple(Tool(name,_DESCRIPTIONS[name],deepcopy(_SCHEMAS[name]),
            lambda context,args,name=name:self.call(name,context,args),
            lambda args,name=name:self.validate(name,args)) for name in _SCHEMAS)

    def catalog(self,*,agents):
        if not isinstance(agents,(list,tuple)):raise ValueError('Explicit agent key sequence required')
        agents=tuple(agents)
        if not agents or len(agents)>100 or any(not isinstance(a,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}',a) for a in agents) or len(set(agents))!=len(agents):
            raise ValueError('Explicit bounded unique agent keys required')
        return tuple(CatalogTool(name,True,_DESCRIPTIONS[name],deepcopy(_SCHEMAS[name]),list(agents),True,'builtin',{},False) for name in _SCHEMAS)
