"""Source-shaped task constraint handlers with transactional inert SQL fixtures."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime,timezone
import json,unittest
from artex.server.task_constraints import TaskConstraintService,ConstraintBinding
from artex.server.core import Request,Router
from artex.db.constraints import ConstraintStore


class DB:
    def __init__(self):
        self.rows={1:('deny','no changes','human',datetime(2026,1,1,tzinfo=timezone.utc))}
        self.calls=[];self.events=[];self.parent=(5,'created',False,False);self.fail=False;self.hook=None
    @contextmanager
    def transaction(self):
        before=deepcopy(self.rows);self.events.append('begin')
        try:yield self
        except BaseException:self.rows=before;self.events.append('rollback');raise
        else:self.events.append('commit')
    def fetch_all(self,q):
        self.calls.append(q)
        if q.sql.startswith('SELECT id,status'):return [self.parent] if self.parent else []
        if q.sql.startswith('SELECT id,kind,'):
            assert q.params[0]==9
            return [(id,*row,len(self.rows)) for id,row in sorted(self.rows.items(),key=lambda pair:(pair[1][0]=='deny',pair[0]))]
        if q.sql.startswith('SELECT id, kind'):
            assert q.params==(9,)
            return [(id,*row) for id,row in sorted(self.rows.items(),key=lambda pair:(pair[1][0]=='deny',pair[0]))]
        raise AssertionError(q.sql)
    def fetch_one(self,q):
        self.calls.append(q);assert q.params[0]==9
        if self.fail:raise RuntimeError('private SQL')
        id=max(self.rows,default=0)+1;self.rows[id]=(*q.params[1:],datetime(2026,1,1,tzinfo=timezone.utc))
        if self.hook:self.hook()
        return (id,)
    def execute(self,q):
        self.calls.append(q)
        if self.fail:raise RuntimeError('private SQL')
        if q.sql.startswith('UPDATE'):
            kind,text,id,exp=q.params;assert exp==9
            if id not in self.rows:return 0
            old=self.rows[id];self.rows[id]=(kind,text,*old[2:])
        else:
            id,exp=q.params;assert exp==9
            if id not in self.rows:return 0
            del self.rows[id]
        if self.hook:self.hook()
        return 1


class ConstraintHTTPTests(unittest.TestCase):
    def setUp(self):
        self.db=DB();self.store=ConstraintStore(self.db,9)
        self.binding=ConstraintBinding(5,9,'revision',self.store)
        self.valid=True;self.is_idle=True;self.allowed=True;self.deletion=True;self.policies=[];self.resolves=[];self.depth=0
        @contextmanager
        def guard(binding):
            self.depth+=1
            try:yield
            finally:self.depth-=1
        self.guard=guard;self.router=self.make()
    def make(self,**kw):
        def resolve(req,id):self.resolves.append(id);return self.binding
        def policy(req,action,binding,proposal):
            self.assertGreater(self.depth,0);self.policies.append((action,proposal));return self.allowed
        options=dict(resolve=resolve,authorize=policy,current=lambda b:self.valid,lifecycle_guard=self.guard,
                     mutation_authorize=policy,delete_authorize=lambda *a:self.deletion,idle=lambda b:self.is_idle)
        options.update(kw)
        self.service=TaskConstraintService(self.db,**options)
        router=Router(lambda req:req.header('Authorization')=='fixture');self.service.register(router);return router
    def call(self,method='GET',body=None,cid=None,task='5',query='',auth=True,raw=None):
        path='/api/tasks/'+task+'/constraints'+('' if cid is None else '/'+str(cid))+query
        data=raw if raw is not None else b'' if body is None else json.dumps(body).encode()
        return self.router.dispatch(Request(method,path,{'Authorization':'fixture' if auth else ''},data))
    def test_list_and_source_dtos_actual_scoped_store(self):
        self.assertEqual(self.call().json(),{'constraints':[{'id':'1','kind':'deny','text':'no changes','origin':'human','ts':'2026-01-01T00:00:00Z'}]})
        self.assertEqual(self.db.calls[0].params,(5,9));self.assertTrue(all(q.sql.startswith('SELECT') for q in self.db.calls))
        self.assertEqual(self.db.events,['begin','commit'])
    def test_create_patch_delete_wire_and_no_notifications(self):
        reply=self.call('POST',{'kind':' ALLOW ','text':'  read only  '})
        self.assertEqual((reply.status,reply.json()),(200,{'id':'2','kind':'allow','text':'read only','origin':'human'}))
        self.assertEqual(self.policies[-1][1].text,'read only')
        self.assertEqual(self.call('PATCH',{'kind':'deny','text':'stop'},cid=2).json(),{'id':'2','kind':'deny','text':'stop'})
        self.assertEqual(self.call('DELETE',cid=2).json(),{'ok':True})
        self.assertNotIn(2,self.db.rows)
    def test_auth_before_resolver_or_sql(self):
        self.assertEqual(self.call(auth=False).status,401);self.assertEqual(self.resolves,[]);self.assertEqual(self.db.calls,[])
    def test_default_mutation_and_delete_require_extra_policy(self):
        self.router=self.make(mutation_authorize=None,idle=None)
        self.assertEqual(self.call('POST',{'kind':'deny','text':'x'}).status,503)
        self.router=self.make(delete_authorize=None)
        self.assertEqual(self.call('DELETE',cid=1).status,503)
        self.assertEqual(self.db.calls,[])
    def test_running_or_not_drained_conflicts_without_mutation(self):
        for parent in ((5,'running',False,False),(5,'running',True,False),(5,'created',False,True)):
            self.db.parent=parent
            self.assertEqual(self.call('POST',{'kind':'deny','text':'x'}).status,409)
        self.db.parent=(5,'created',False,False);self.is_idle=False
        self.assertEqual(self.call('DELETE',cid=1).status,409)
        self.assertEqual(len(self.db.rows),1)
    def test_exact_task_exploration_executor_no_fallback(self):
        self.binding=ConstraintBinding(6,9,'revision',self.store)
        self.assertEqual(self.call().status,403)
        self.binding=ConstraintBinding(5,10,'revision',self.store)
        self.assertEqual(self.call().status,503)
        self.binding=ConstraintBinding(5,9,'revision',ConstraintStore(DB(),9))
        self.assertEqual(self.call().status,503)
        self.assertEqual(self.db.calls,[])
    def test_stale_after_sql_rolls_back(self):
        self.db.hook=lambda:setattr(self,'valid',False)
        response=self.call('POST',{'kind':'deny','text':'x'})
        self.assertEqual(response.status,409);self.assertEqual(len(self.db.rows),1);self.assertEqual(self.db.events[-1],'rollback')
    def test_policy_cannot_rebind_store_to_another_exploration(self):
        def policy(*a):self.store.exploration_id=10;return True
        self.router=self.make(mutation_authorize=policy)
        self.assertEqual(self.call('POST',{'kind':'deny','text':'x'}).status,503);self.assertEqual(self.db.calls,[])
    def test_denial_and_nonliteral_true_before_writes(self):
        for value in (False,1,'yes'):
            self.allowed=value
            self.assertEqual(self.call('POST',{'kind':'deny','text':'x'}).status,403)
        self.allowed=True;self.deletion=False
        self.assertEqual(self.call('DELETE',cid=1).status,403);self.assertEqual(self.db.calls,[])
    def test_invalid_json_fields_ids_and_bounds_no_sql(self):
        for raw in (b'{"kind":"deny","kind":"allow","text":"x"}',b'{"kind":null,"text":"x"}',b'{"text":"x"}',b'{"kind":"deny","text":" "}',b'{"kind":"deny","text":"x","task_id":6}',b'[]'):
            self.assertEqual(self.call('POST',raw=raw).status,400)
        self.assertEqual(self.call('PATCH',{'kind':'deny','text':'x'},cid='0').status,400)
        self.assertEqual(self.call(query='?task=6').status,400)
        self.assertEqual(self.call('DELETE',cid=1,raw=b'{}').status,400)
        self.assertEqual(self.call('POST',raw=b'x'*65537).status,413)
        self.assertEqual(self.db.calls,[])
    def test_missing_and_redacted_failure(self):
        self.assertEqual(self.call('PATCH',{'kind':'deny','text':'x'},cid=99).status,404)
        self.db.parent=None;self.assertEqual(self.call().status,404)
        self.db.parent=(5,'created',False,False);self.db.fail=True
        reply=self.call('DELETE',cid=1)
        self.assertEqual(reply.status,500);self.assertNotIn(b'private',reply.body);self.assertIn(1,self.db.rows)
