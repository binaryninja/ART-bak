"""Explicit scoped capability-selection metadata, not execution authorization.

Source db/config.go: enabled rows enumerate visibility, true upsert/false delete.
MCP disable removes all per-tool rows for one agent/server, not just default ''.
Reads retain duplicate agent IDs when source per-tool rows yield duplicates.
Last-write-wins, no invented client CAS. Exact guarded transactions, bounded rows.
Unlike source orphan acceptance, MCP/agent must exist on writes. Skill identity
has no DB FK; host policies must validate known authorized skills. No filesystem
lookup, connection, seed or runtime grant. No startup seeding that could resurrect
source's deleted/off skill rows. Explicit host guard serializes policy writers.
"""
from dataclasses import dataclass
import re
from .core import Query
class VisibilityDenied(PermissionError):pass
class VisibilityUnavailable(RuntimeError):pass
class VisibilityMissing(LookupError):pass
class VisibilityUncertain(RuntimeError):pass
@dataclass(frozen=True)
class VisibilityScope:
    name:str
    revision:str
@dataclass(frozen=True)
class VisibilitySelection:
    kind:str
    resource_id:int|None=None
    skill_name:str=''
    agent_id:int|None=None
    visible:bool|None=None

def positive(value):
    if type(value) is not int or not 0<value<2**63:raise ValueError('Positive ID required')
    return value

def validate(p,write=False):
    if type(p) is not VisibilitySelection:raise ValueError('Exact selector required')
    if p.kind=='mcp':
        positive(p.resource_id)
        if p.skill_name:raise ValueError('Mixed selector')
    elif p.kind=='skill':
        if p.resource_id is not None or type(p.skill_name) is not str or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',p.skill_name) or p.skill_name in ('.','..'):raise ValueError('Invalid skill name')
    else:raise ValueError('Only MCP or skill visibility supported')
    if write:
        positive(p.agent_id)
        if type(p.visible) is not bool:raise ValueError('Literal visibility required')
    elif p.agent_id is not None or p.visible is not None:raise ValueError('Invalid read selector')

class VisibilityStore:
    def __init__(self,executor,scope,*,authorize,current,lifecycle_guard,mutation_authorize=None):
        if type(scope) is not VisibilityScope or any(type(s) is not str or not s or len(s.encode())>256 or '\0' in s for s in (scope.name,scope.revision)):raise ValueError('Explicit bounded scope required')
        if any(not callable(f) for f in (authorize,current,lifecycle_guard)) or mutation_authorize is not None and not callable(mutation_authorize):raise ValueError('Explicit visibility policies required')
        self.executor,self.scope=executor,scope;self._executor,self._scope=executor,scope
        self.authorize,self.current,self.guard,self.mutate=authorize,current,lifecycle_guard,mutation_authorize
        self._guard=lifecycle_guard
    def _check(self):
        if self.executor is not self._executor or self.scope is not self._scope or self.guard is not self._guard:raise VisibilityUnavailable()
        if self.current(self._scope) is not True:raise VisibilityDenied()
        if self.executor is not self._executor or self.scope is not self._scope or self.guard is not self._guard:raise VisibilityUnavailable()
    def _allow(self,action,p,request_authorize):
        self._check()
        for policy in (self.authorize,request_authorize,self.mutate if action=='toggle' else self.authorize):
            if policy is None:raise VisibilityUnavailable()
            if policy(self._scope,action,p) is not True:raise VisibilityDenied()
            self._check()
    def read(self,p,*,request_authorize):
        validate(p)
        with self._guard(self._scope):
            self._allow('read',p,request_authorize)
            if p.kind=='mcp':q=Query('SELECT agent_id FROM agent_visibility WHERE resource_kind=$1 AND resource_id=$2 AND enabled ORDER BY agent_id,mcp_tool_name LIMIT 1001',(p.kind,p.resource_id))
            else:q=Query('SELECT agent_id FROM agent_skill_visibility WHERE skill_name=$1 AND enabled ORDER BY agent_id LIMIT 1001',(p.skill_name,))
            rows=self._executor.fetch_all(q)
            if len(rows)>1000:raise VisibilityUnavailable('Visibility view exceeds bound')
            ids=[positive(r[0]) for r in rows];self._allow('read',p,request_authorize);return ids
    def toggle(self,p,*,request_authorize):
        validate(p,True)
        attempted=False
        try:
            with self._guard(self._scope):
                self._allow('toggle',p,request_authorize)
                with self._executor.transaction() as tx:
                    if tx.fetch_all(Query('SELECT id FROM agents WHERE id=$1 FOR KEY SHARE',(p.agent_id,)))!=[(p.agent_id,)]:raise VisibilityMissing()
                    if p.kind=='mcp':
                        if tx.fetch_all(Query('SELECT id FROM mcp_servers WHERE id=$1 FOR KEY SHARE',(p.resource_id,)))!=[(p.resource_id,)]:raise VisibilityMissing()
                        if p.visible:q=Query("INSERT INTO agent_visibility(agent_id,resource_kind,resource_id,enabled) VALUES ($1,$2,$3,true) ON CONFLICT (agent_id,resource_kind,resource_id,mcp_tool_name) DO UPDATE SET enabled=true",(p.agent_id,p.kind,p.resource_id))
                        else:
                            rows=tx.fetch_all(Query('SELECT agent_id FROM agent_visibility WHERE agent_id=$1 AND resource_kind=$2 AND resource_id=$3 LIMIT 1001 FOR UPDATE',(p.agent_id,p.kind,p.resource_id)))
                            if len(rows)>1000:raise VisibilityUnavailable('Visibility mutation exceeds bound')
                            q=Query('DELETE FROM agent_visibility WHERE agent_id=$1 AND resource_kind=$2 AND resource_id=$3',(p.agent_id,p.kind,p.resource_id))
                    elif p.visible:q=Query('INSERT INTO agent_skill_visibility(agent_id,skill_name,enabled) VALUES ($1,$2,true) ON CONFLICT (agent_id,skill_name) DO UPDATE SET enabled=true',(p.agent_id,p.skill_name))
                    else:q=Query('DELETE FROM agent_skill_visibility WHERE agent_id=$1 AND skill_name=$2',(p.agent_id,p.skill_name))
                    self._allow('toggle',p,request_authorize)
                    attempted=True
                    count=tx.execute(q)
                    if type(count) is not int or not 0<=count<=(1000 if p.kind=='mcp' and not p.visible else 1):raise VisibilityUnavailable('Unknown mutation outcome')
                    self._allow('toggle',p,request_authorize)
                self._check()
        except BaseException:
            if attempted:raise VisibilityUncertain('Visibility mutation outcome requires reconciliation') from None
            raise
