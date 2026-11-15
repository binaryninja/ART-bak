"""Opt-in source-shaped task constraint CRUD; no default routes or permissions.

Resolver(request, task_id) returns ConstraintBinding or None. authorize(request,
action, binding, proposal) controls reads; mutations additionally need
mutation_authorize with the same signature and idle(binding)->literal True.
DELETE also needs delete_authorize. Proposal is immutable normalized kind/text
and constraint ID, not model-supplied authority. Hosts must approve exact changes,
including removal of a deny constraint; JWT/read rights are not mutation approval.

current(binding) and lifecycle_guard(binding) must share revision, task deletion,
archive, profile/config and all runtime admission authority. The guard spans
policies, parent row lock and the entire transaction. Mutation requires both
confirmed host idle/drained state AND a non-running, non-queued database task.
Running tasks (even paused) fail closed; live constraint replacement is not
implemented. The host must rebuild constraint context on future admission.
Source does not notify the planner on these edits; neither does this module.

ConstraintStore executes actual scoped CRUD. Only exact IDs, no global fallback.
No real deletion or runtime is invoked by construction. Transaction/commit errors
are redacted failures, never retried; uncertain effects require reconciliation.
"""
from dataclasses import dataclass
from datetime import datetime
import json
import re
from urllib.parse import urlsplit
from .core import json_response,error_response
from ..db.core import Query,int64
from ..db.constraints import ConstraintStore,ConstraintNotFound,ConstraintReadOverflow


@dataclass(frozen=True)
class ConstraintBinding:
    task_id:int
    exploration_id:int
    revision:str
    store:ConstraintStore


@dataclass(frozen=True)
class ConstraintChange:
    constraint_id:int|None=None
    kind:str=''
    text:str=''


class ConstraintAccessDenied(RuntimeError):pass
class ConstraintRuntimeUnavailable(RuntimeError):pass
class ConstraintConflict(RuntimeError):pass
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


def _proposal(body,cid):
    data=json.loads(body.decode('utf-8'),object_pairs_hook=_pairs,
                    parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
    if type(data) is not dict or set(data)!={'kind','text'}:raise ValueError('kind and text required')
    kind,text=data['kind'],data['text']
    if not isinstance(kind,str) or not isinstance(text,str):raise ValueError('Text fields required')
    kind=kind.strip().lower();text=text.strip()
    if kind not in ('allow','deny') or not text or '\0' in text or len(text.encode('utf-8'))>16384:raise ValueError('Invalid constraint')
    return ConstraintChange(cid,kind,text)


class TaskConstraintService:
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
            ('GET','/api/tasks/{id}/constraints','list'),
            ('POST','/api/tasks/{id}/constraints','create'),
            ('PATCH','/api/tasks/{id}/constraints/{cid}','patch'),
            ('DELETE','/api/tasks/{id}/constraints/{cid}','delete'),
        ):
            router.add(method,path,lambda req,params,a=action:self.handle(a,req,params))

    def _check(self,binding,identity):
        def verify():
            if (binding.task_id,binding.exploration_id,binding.revision)!=identity or self.executor is not self._executor or binding.store.executor is not self._executor or binding.store.exploration_id!=identity[1]:
                raise ConstraintRuntimeUnavailable()
        verify()
        if self.current(binding) is not True:raise ConstraintConflict()
        verify()

    def _policy(self,callback,request,action,binding,proposal):
        if not callable(callback):raise ConstraintRuntimeUnavailable()
        try:approved=callback(request,action,binding,proposal)
        except Exception:raise ConstraintRuntimeUnavailable() from None
        if approved is not True:raise ConstraintAccessDenied()

    def handle(self,action,request,params):
        try:
            if action not in ('list','create','patch','delete'):raise ValueError('Unsupported action')
            if urlsplit(request.target).query:raise ValueError('Query unsupported')
            task_id=_id(params['id']);cid=_id(params['cid']) if 'cid' in params else None
            if len(request.body)>65536:return error_response(413,'Request body too large')
            if action in ('list','delete'):
                if request.body:raise ValueError('Body unsupported')
                proposal=ConstraintChange(cid)
            else:proposal=_proposal(request.body,cid)
            if action!='list' and (not callable(self.mutate) or not callable(self.idle)):raise ConstraintRuntimeUnavailable()
            if action=='delete' and not callable(self.delete):raise ConstraintRuntimeUnavailable()
            binding=self.resolve(request,task_id)
            if binding is None:raise TaskMissing()
            if not isinstance(binding,ConstraintBinding) or not isinstance(binding.store,ConstraintStore):raise ConstraintRuntimeUnavailable()
            if type(binding.task_id) is not int or binding.task_id!=task_id:raise ConstraintAccessDenied()
            if int64(binding.exploration_id,'exploration ID')<=0:raise ConstraintRuntimeUnavailable()
            if not isinstance(binding.revision,str) or not binding.revision or '\0' in binding.revision or len(binding.revision.encode())>256:raise ConstraintRuntimeUnavailable()
            identity=(task_id,binding.exploration_id,binding.revision)
            with self.guard(binding):
                self._check(binding,identity)
                self._policy(self.authorize,request,action,binding,proposal);self._check(binding,identity)
                if action!='list':
                    self._policy(self.mutate,request,action,binding,proposal);self._check(binding,identity)
                    if action=='delete':
                        self._policy(self.delete,request,action,binding,proposal);self._check(binding,identity)
                    if self.idle(binding) is not True:raise ConstraintConflict()
                    self._check(binding,identity)
                with self.executor.transaction() as tx:
                    rows=tx.fetch_all(Query('''SELECT id,status,paused,queued FROM tasks
WHERE id=$1 AND exploration_id=$2 AND deleted_at IS NULL AND archived_at IS NULL
AND NOT EXISTS (SELECT 1 FROM task_archives WHERE task_id=$1) FOR UPDATE''',identity[:2]))
                    if not rows:raise TaskMissing()
                    if len(rows)!=1 or len(rows[0])!=4 or rows[0][0]!=task_id:raise ConstraintRuntimeUnavailable()
                    if action!='list' and (rows[0][1] not in ('created','done','failed','timeout') or rows[0][2] is not False or rows[0][3] is not False):raise ConstraintConflict()
                    self._check(binding,identity)
                    store=ConstraintStore(tx,identity[1])
                    if action=='list':
                        result=[]
                        for row in store.list_bounded():
                            item={'id':str(row.id),'kind':row.kind,'text':row.text}
                            if row.origin:item['origin']=row.origin
                            if isinstance(row.created_at,datetime):item['ts']=row.created_at.replace(microsecond=0).isoformat().replace('+00:00','Z')
                            result.append(item)
                        output={'constraints':result}
                    elif action=='create':
                        identifier=store.add(proposal.kind,proposal.text,'human')
                        if identifier<=0:raise ConstraintRuntimeUnavailable()
                        output={'id':str(identifier),'kind':proposal.kind,'text':proposal.text,'origin':'human'}
                    elif action=='patch':
                        store.update(cid,proposal.kind,proposal.text)
                        output={'id':str(cid),'kind':proposal.kind,'text':proposal.text}
                    else:
                        store.delete(cid);output={'ok':True}
                    self._check(binding,identity)
                    response=json_response(200,output)
                    if len(response.body)>1048576:raise ConstraintRuntimeUnavailable()
                return response
        except ConstraintAccessDenied:return error_response(403,'Constraint access denied')
        except (ConstraintRuntimeUnavailable,ConstraintReadOverflow):return error_response(503,'Constraint capability unavailable')
        except ConstraintConflict:return error_response(409,'Task runtime or revision prevents constraint changes')
        except TaskMissing:return error_response(404,'task not found')
        except ConstraintNotFound:return error_response(404,'constraint not found')
        except (ValueError,UnicodeError,TypeError):return error_response(400,'Invalid constraint request')
        except Exception:return error_response(500,'Constraint operation failed; reconcile before retrying mutations')
