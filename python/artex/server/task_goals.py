"""Opt-in source goal CRUD for idle, drained tasks; no default route wiring.

Source server/goals_api.go accepts text/vulnclass, not goal state. PATCH preserves
state and evidence/lineage edges; state/met input is rejected. Creation atomically
adds an open human goal and required origin-fact spawns edge. Deletion is goal-kind
scoped and requires explicit delete_authorize, including cascade edge/anchor loss.
No other fact/finding/intent node is deleted. No manager completion, model-derived
permission, planner wake or automatic task revival is implemented. Unlike Go's
live/revival handlers, callers must explicitly resume after successful idle edits.

resolve(request,task_id)->GoalBinding; authorize, mutation_authorize and
 delete_authorize(request,action,binding,GoalChange) must return literal True.
The shared lifecycle_guard/current revision and idle/drain callback must fence all
admission/config/archive/deletion/graph writers. Mutations reject running, paused,
queued or undrained tasks. Host must reload goals on later admission. Policy is
not inferred from JWT. Executor is pinned at construction; no global fallback.
The complete list is capped at10000 rows with SQL byte sentinels and a1MiB
response cap; overflow fails instead of silently omitting objectives. Transaction uncertainty is
redacted, not retried. No actual effects occur during service construction.
"""
from dataclasses import dataclass
from datetime import datetime
import json
import re
from urllib.parse import urlsplit
from .core import json_response,error_response
from ..db.core import Query,int64
from ..db.exploration import ExplorationStore,GraphNotFound,GraphViewOverflow


@dataclass(frozen=True)
class GoalBinding:
    task_id:int
    exploration_id:int
    revision:str
    store:ExplorationStore


@dataclass(frozen=True)
class GoalChange:
    goal_id:int|None=None
    text:str=''
    vulnclass:str=''


class GoalAccessDenied(RuntimeError):pass
class GoalRuntimeUnavailable(RuntimeError):pass
class GoalConflict(RuntimeError):pass
class TaskMissing(LookupError):pass


def _id(value):
    if not isinstance(value,str) or not re.fullmatch(r'[1-9][0-9]{0,18}',value):raise ValueError('Invalid ID')
    result=int(value)
    if result>=2**63:raise ValueError('Invalid ID')
    return result


def _pairs(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('Duplicate JSON key')
        result[key]=value
    return result


def _proposal(body,gid):
    data=json.loads(body.decode('utf-8'),object_pairs_hook=_pairs,
                    parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
    if type(data) is not dict or set(data)-{'text','vulnclass'} or 'text' not in data:raise ValueError('Goal text required')
    text,vc=data['text'],data.get('vulnclass','')
    if not isinstance(text,str) or not isinstance(vc,str):raise ValueError('Text fields required')
    text=text.strip();vc=vc.strip()
    if not text or any('\0' in v for v in (text,vc)) or len(text.encode('utf-8'))>16384 or len(vc.encode('utf-8'))>256:raise ValueError('Invalid goal')
    return GoalChange(gid,text,vc)


def _dto(node):
    payload=node.payload
    if node.kind!='goal' or not isinstance(payload,dict):raise GoalRuntimeUnavailable()
    text,vc=payload.get('text',''),payload.get('vulnclass','')
    if not isinstance(text,str) or not isinstance(vc,str):raise GoalRuntimeUnavailable()
    out={'id':str(node.id),'text':text,'state':node.state,'ts':''}
    if vc:out['vulnclass']=vc
    if node.origin:out['origin']=node.origin
    if isinstance(node.created_at,datetime):out['ts']=node.created_at.replace(microsecond=0).isoformat().replace('+00:00','Z')
    return out


class TaskGoalService:
    def __init__(self,executor,*,resolve,authorize,current,lifecycle_guard,
                 mutation_authorize=None,delete_authorize=None,idle=None):
        if any(not callable(f) for f in (resolve,authorize,current,lifecycle_guard)):
            raise ValueError('Explicit scoped lifecycle and authorization required')
        if any(f is not None and not callable(f) for f in (mutation_authorize,delete_authorize,idle)):
            raise ValueError('Invalid mutation policy')
        self._executor=executor
        self.executor,self.resolve,self.authorize,self.current,self.guard=executor,resolve,authorize,current,lifecycle_guard
        self.mutate,self.delete,self.idle=mutation_authorize,delete_authorize,idle

    def register(self,router):
        for method,path,action in (
            ('GET','/api/tasks/{id}/goals','list'),
            ('POST','/api/tasks/{id}/goals','create'),
            ('PATCH','/api/tasks/{id}/goals/{gid}','patch'),
            ('DELETE','/api/tasks/{id}/goals/{gid}','delete'),
        ):
            router.add(method,path,lambda req,params,a=action:self.handle(a,req,params))

    def _check(self,binding,identity):
        def verify():
            if (binding.task_id,binding.exploration_id,binding.revision)!=identity or self.executor is not self._executor or binding.store.executor is not self._executor or binding.store.read_only or binding.store.exploration_id!=identity[1]:
                raise GoalRuntimeUnavailable()
        verify()
        if self.current(binding) is not True:raise GoalConflict()
        verify()

    def _policy(self,callback,request,action,binding,proposal):
        if not callable(callback):raise GoalRuntimeUnavailable()
        try:approved=callback(request,action,binding,proposal)
        except Exception:raise GoalRuntimeUnavailable() from None
        if approved is not True:raise GoalAccessDenied()

    def handle(self,action,request,params):
        try:
            if action not in ('list','create','patch','delete'):raise ValueError('Unsupported action')
            if urlsplit(request.target).query:raise ValueError('Query unsupported')
            task_id=_id(params['id']);gid=_id(params['gid']) if 'gid' in params else None
            if len(request.body)>65536:return error_response(413,'Request body too large')
            if action in ('list','delete'):
                if request.body:raise ValueError('Body unsupported')
                proposal=GoalChange(gid)
            else:proposal=_proposal(request.body,gid)
            if action!='list' and (not callable(self.mutate) or not callable(self.idle)):raise GoalRuntimeUnavailable()
            if action=='delete' and not callable(self.delete):raise GoalRuntimeUnavailable()
            binding=self.resolve(request,task_id)
            if binding is None:raise TaskMissing()
            if not isinstance(binding,GoalBinding) or not isinstance(binding.store,ExplorationStore):raise GoalRuntimeUnavailable()
            if type(binding.task_id) is not int or binding.task_id!=task_id:raise GoalAccessDenied()
            if int64(binding.exploration_id,'exploration ID')<=0:raise GoalRuntimeUnavailable()
            if not isinstance(binding.revision,str) or not binding.revision or '\0' in binding.revision or len(binding.revision.encode())>256:raise GoalRuntimeUnavailable()
            identity=(task_id,binding.exploration_id,binding.revision)
            with self.guard(binding):
                self._check(binding,identity)
                self._policy(self.authorize,request,action,binding,proposal);self._check(binding,identity)
                if action!='list':
                    self._policy(self.mutate,request,action,binding,proposal);self._check(binding,identity)
                    if action=='delete':
                        self._policy(self.delete,request,action,binding,proposal);self._check(binding,identity)
                    if self.idle(binding) is not True:raise GoalConflict()
                    self._check(binding,identity)
                with self._executor.transaction() as tx:
                    rows=tx.fetch_all(Query('''SELECT id,status,paused,queued FROM tasks
WHERE id=$1 AND exploration_id=$2 AND deleted_at IS NULL AND archived_at IS NULL
AND NOT EXISTS (SELECT 1 FROM task_archives WHERE task_id=$1) FOR UPDATE''',identity[:2]))
                    if not rows:raise TaskMissing()
                    if len(rows)!=1 or len(rows[0])!=4 or rows[0][0]!=task_id:raise GoalRuntimeUnavailable()
                    if action!='list' and (rows[0][1] not in ('created','done','failed','timeout') or rows[0][2] is not False or rows[0][3] is not False):raise GoalConflict()
                    self._check(binding,identity)
                    store=ExplorationStore(tx,identity[1])
                    if action=='list':
                        output={'goals':[_dto(row) for row in store.list_goals_bounded()]}
                    else:
                        if action in ('patch','delete'):
                            targets=tx.fetch_all(Query("SELECT id FROM exploration_nodes WHERE id=$1 AND exploration_id=$2 AND kind='goal' FOR UPDATE",(gid,identity[1])))
                            if targets not in ([(gid,)],[[gid]]):raise GraphNotFound('Goal unavailable')
                        self._check(binding,identity)
                        if action=='create':
                            origin=store.origin_fact_id()
                            if type(origin) is not int or origin<=0:raise GoalRuntimeUnavailable()
                            payload={'text':proposal.text}
                            if proposal.vulnclass:payload['vulnclass']=proposal.vulnclass
                            identifier=store.add_goal(payload,'human')
                            if identifier<=0 or not store.link(origin,'spawns',identifier):raise GoalRuntimeUnavailable()
                        elif action=='patch':
                            store.update_goal_payload(gid,proposal.text,proposal.vulnclass)
                            identifier=gid
                        else:
                            store.delete_goal(gid)
                            output={'ok':True}
                        if action!='delete':
                            updated=store.get_goal_bounded(identifier)
                            if updated is None or updated.id!=identifier or updated.kind!='goal':raise GoalRuntimeUnavailable()
                            output=_dto(updated)
                    self._check(binding,identity)
                    response=json_response(200,output)
                    if len(response.body)>1048576:raise GoalRuntimeUnavailable()
                return response
        except GoalAccessDenied:return error_response(403,'Goal access denied')
        except (GoalRuntimeUnavailable,GraphViewOverflow):return error_response(503,'Goal capability unavailable')
        except GoalConflict:return error_response(409,'Task runtime or revision prevents goal changes')
        except TaskMissing:return error_response(404,'task not found')
        except GraphNotFound:return error_response(404,'goal not found')
        except (ValueError,UnicodeError,TypeError):return error_response(400,'Invalid goal request')
        except Exception:return error_response(500,'Goal operation failed; reconcile before retrying mutations')
