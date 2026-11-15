"""Inert scoped metadata HTTP; no targets or actual database."""
from contextlib import contextmanager,nullcontext
import json,unittest
from artex.server.core import Router,Request
from artex.server.task_scope import TaskScopeService,ScopeBinding
from artex.db.task_scope import TaskScopeStore,ScopeUnavailable
class DB:
    def __init__(self):self.calls=[];self.writes=[];self.parent=(5,'created',False,False);self.relations=[];self.items=[];self.count=1
    @contextmanager
    def transaction(self):
        old=list(self.writes)
        try:yield self
        except BaseException:self.writes=old;raise
    def fetch_all(self,q):
        self.calls.append(q)
        if q.sql.startswith('SELECT id,status'):return [self.parent]
        if 'FROM task_relations' in q.sql:return self.relations
        if 'AS bounded_scope' in q.sql:return self.items
        if 'FROM task_archives' in q.sql:return []
        if 'FROM companies' in q.sql:return [(q.params[0],)]
        raise AssertionError(q.sql)
    def fetch_one(self,q):self.calls.append(q);return (5,None,9)
    def execute(self,q):self.calls.append(q);self.writes.append(q);return self.count
class Tests(unittest.TestCase):
    def setUp(self):
        self.db=DB();self.valid=True;self.entries=[];self.allow=True
        self.store=TaskScopeStore(self.db,5,lifecycle_guard=nullcontext,current=lambda:True,approve=lambda e:True,expected_exploration_id=9)
        self.binding=ScopeBinding(5,9,'r',self.store);self.router=self.make()
    def make(self,**kw):
        opts=dict(resolve=lambda *a:self.binding,authorize=lambda *a:True,current=lambda b:self.valid,lifecycle_guard=lambda b:nullcontext(),write_authorize=lambda *a:True,entry_authorize=lambda *a:self.entries.append(a[-1]) or self.allow,delete_authorize=lambda *a:True,idle=lambda b:True)
        opts.update(kw);self.service=TaskScopeService(self.db,**opts);r=Router(lambda req:req.header('Authorization')=='yes');self.service.register(r);return r
    def call(self,method='GET',body=None,sid=None,auth='yes'):
        return self.router.dispatch(Request(method,'/api/tasks/5/scope'+('' if sid is None else '/'+str(sid)),{'Authorization':auth},b'' if body is None else json.dumps(body).encode()))
    def test_auth_before_sql(self):
        self.assertEqual(self.call(auth='no').status,401);self.assertEqual(self.db.calls,[])
    def test_add_normalized_exact_manual_not_test_authority(self):
        r=self.call('POST',{'kind':'root_domain','value':'API.Example.com.'})
        self.assertEqual(r.status,200);self.assertEqual(r.json()['domain'],'api.example.com');self.assertEqual(r.json()['source'],'manual');self.assertEqual(r.json()['id'],0)
        self.assertEqual(self.entries[0].domain,'api.example.com');self.assertEqual(len(self.db.writes),1)
    def test_missing_or_denied_entry_permission_no_write(self):
        self.router=self.make(entry_authorize=None)
        self.assertEqual(self.call('POST',{'kind':'ip','value':'127.0.0.1'}).status,503)
        self.router=self.make();self.allow=False
        self.assertEqual(self.call('POST',{'kind':'keyword','value':'x'}).status,403);self.assertEqual(self.db.writes,[])
    def test_source_state_and_company_name_injection_rejected(self):
        for b in ({'kind':'keyword','value':'x','source':'agent'},{'kind':'company','value':'Example Inc'},{'kind':'cidr','value':'10.0.0.1/24'}):self.assertEqual(self.call('POST',b).status,400)
        self.assertEqual(self.db.writes,[])
    def test_idle_and_sql_status_required(self):
        self.db.parent=(5,'running',False,False)
        self.assertEqual(self.call('DELETE',sid=2).status,409);self.assertEqual(self.db.writes,[])
    def test_delete_exact_task_and_extra_grant(self):
        self.router=self.make(delete_authorize=None);self.assertEqual(self.call('DELETE',sid=2).status,503)
        self.router=self.make();self.assertEqual(self.call('DELETE',sid=2).json(),{'ok':True})
        self.assertEqual(self.db.writes[-1].params,(2,5))
        self.db.count=0;self.assertEqual(self.call('DELETE',sid=2).status,404)
    def test_bounded_list_and_inherited_fail_closed(self):
        self.db.items=[({'id':2,'task_id':5,'kind':'keyword','value':'x','source':'manual'},1)]
        self.assertEqual(self.call().json()['scope'][0]['id'],2)
        self.db.relations=[(1,)];self.assertEqual(self.call().status,503)
        self.db.relations=[];self.db.items=[(None,1)];self.assertEqual(self.call().status,503)
    def test_scope_executor_mutation_blocks_sql(self):
        def policy(*a):self.store.executor=DB();return True
        self.router=self.make(authorize=policy)
        self.assertEqual(self.call().status,503);self.assertEqual(self.db.calls,[])
    def test_stale_after_insert_rolls_back(self):
        execute=self.db.execute
        def changed(q):result=execute(q);self.valid=False;return result
        self.db.execute=changed
        self.assertEqual(self.call('POST',{'kind':'keyword','value':'x'}).status,403);self.assertEqual(self.db.writes,[])
