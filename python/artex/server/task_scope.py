"""Opt-in own-task scope metadata HTTP. No default installation or test authority.

Explicit source inheritance is not implemented: GET fails closed if a relation
exists. Writes require idle/drained host authority and a SQL idle parent lock.
Host lifecycle guard must serialize all task scope/config/relationship writers.
Normalized entry approval is separate from endpoint write permission; source is
always manual, never client provenance. No hostname promotion or name lookup.
"""
from contextlib import nullcontext
from dataclasses import dataclass
import json
from urllib.parse import urlsplit
from .core import json_response,error_response
from .task_goals import _id,_pairs
from ..db.core import Query
from ..db.task_scope import TaskScopeStore,normalize,ScopeInvalid,ScopeDenied,ScopeUnavailable

@dataclass(frozen=True)
class ScopeBinding:
    task_id:int
    exploration_id:int
    revision:str
    store:TaskScopeStore

class TaskScopeService:
    def __init__(self,executor,*,resolve,authorize,current,lifecycle_guard,
                 write_authorize=None,entry_authorize=None,delete_authorize=None,idle=None):
        if any(not callable(f) for f in (resolve,authorize,current,lifecycle_guard)):
            raise ValueError('Explicit scoped authority required')
        if any(f is not None and not callable(f) for f in (write_authorize,entry_authorize,delete_authorize,idle)):
            raise ValueError('Invalid policy')
        self._executor=executor
        self.resolve,self.authorize,self.current,self.guard=resolve,authorize,current,lifecycle_guard
        self.write,self.entry,self.delete,self.idle=write_authorize,entry_authorize,delete_authorize,idle
    def register(self,router):
        for method,path,action in (('GET','/api/tasks/{id}/scope','list'),('POST','/api/tasks/{id}/scope','add'),('DELETE','/api/tasks/{id}/scope/{sid}','delete')):
            router.add(method,path,lambda req,params,a=action:self.handle(a,req,params))
    def handle(self,action,request,params):
        try:
            task=_id(params['id']);sid=_id(params['sid']) if action=='delete' else None
            if action not in ('list','add','delete') or urlsplit(request.target).query:raise ScopeInvalid()
            if len(request.body)>16384:return error_response(413,'Request body too large')
            proposal=None
            if action=='add':
                body=json.loads(request.body.decode(),object_pairs_hook=_pairs)
                if type(body) is not dict or set(body)-{'kind','value','reason'}:raise ScopeInvalid()
                proposal=normalize(task,body.get('kind'),body.get('value'),reason=body.get('reason',''),source='manual')
            elif request.body:raise ScopeInvalid()
            binding=self.resolve(request,task)
            if binding is None:return error_response(404,'task not found')
            if type(binding) is not ScopeBinding or not isinstance(binding.store,TaskScopeStore):raise ScopeUnavailable()
            identity=(task,binding.exploration_id,binding.revision)
            if type(identity[1]) is not int or not 0<identity[1]<2**63 or type(identity[2]) is not str or not identity[2] or len(identity[2].encode())>256:raise ScopeUnavailable()
            original_store=binding.store
            def check():
                if binding.task_id!=task or (binding.task_id,binding.exploration_id,binding.revision)!=identity or binding.store is not original_store or original_store.executor is not self._executor or original_store.task_id!=task or original_store.expected_exploration_id!=identity[1]:raise ScopeUnavailable()
                if self.current(binding) is not True:raise ScopeDenied()
                if original_store.executor is not self._executor or original_store.task_id!=task or original_store.expected_exploration_id!=identity[1]:raise ScopeUnavailable()
                return True
            def policy(callback,*args):
                if callback is None:raise ScopeUnavailable()
                if callback(*args) is not True:raise ScopeDenied()
                check();return True
            with self.guard(binding):
                check();policy(self.authorize,request,action,binding)
                if action!='list':
                    policy(self.write,request,action,binding)
                    if self.idle is None:raise ScopeUnavailable()
                    if self.idle(binding) is not True:return error_response(409,'Task runtime prevents scope changes')
                    check()
                with self._executor.transaction() as tx:
                    rows=tx.fetch_all(Query('''SELECT id,status,paused,queued FROM tasks
WHERE id=$1 AND exploration_id=$2 AND deleted_at IS NULL AND archived_at IS NULL
AND NOT EXISTS (SELECT 1 FROM task_archives WHERE task_id=$1) FOR UPDATE''',identity[:2]))
                    if not rows:return error_response(404,'task not found')
                    if len(rows)!=1 or len(rows[0])!=4 or rows[0][0]!=task:raise ScopeUnavailable()
                    if action!='list' and (rows[0][1] not in ('created','done','failed','timeout') or rows[0][2] is not False or rows[0][3] is not False):return error_response(409,'Task runtime prevents scope changes')
                    check()
                    store=TaskScopeStore(tx,task,lifecycle_guard=nullcontext,current=check,
                        approve=lambda entry:policy(self.entry,request,binding,entry),
                        company_allowed=original_store.company_allowed,expected_exploration_id=identity[1])
                    if action=='list':output={'scope':store.list_bounded()}
                    elif action=='add':
                        store.add_many((proposal,));output={'id':0,**proposal.to_dict()}
                    else:
                        if not store.delete(sid,approve_delete=lambda *args:policy(self.delete,request,binding,sid)):
                            return error_response(404,'scope row not found')
                        output={'ok':True}
                    check();response=json_response(200,output)
                return response
        except ScopeDenied:return error_response(403,'Scope access denied')
        except ScopeUnavailable:return error_response(503,'Scope capability unavailable')
        except (ValueError,TypeError,UnicodeError):return error_response(400,'Invalid scope request')
        except Exception:return error_response(500,'Scope operation failed; reconcile before retrying mutations')
