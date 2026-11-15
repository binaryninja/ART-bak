import unittest
from contextlib import contextmanager, nullcontext
from artex.db.task_scope import TaskScopeStore, TaskScope, normalize, ScopeInvalid, ScopeDenied

class DB:
    def __init__(self):self.rows=[];self.parent=(1,None);self.archive=[];self.company=[(7,)];self.calls=[];self.fail=False
    @contextmanager
    def transaction(self):
        old=list(self.rows)
        try:yield self
        except BaseException:self.rows=old;raise
    def fetch_one(self,q):
        self.calls.append(q)
        return (*self.parent,2) if self.parent is not None and 'exploration_id' in q.sql else self.parent
    def fetch_all(self,q):
        self.calls.append(q)
        return self.company if 'companies' in q.sql else self.archive
    def execute(self,q):
        self.calls.append(q)
        if self.fail and self.rows:raise RuntimeError('injected')
        if q.params not in self.rows:self.rows.append(q.params)

class Tests(unittest.TestCase):
    def setUp(self):
        self.db=DB();self.live=True;self.allowed=set()
        self.store=TaskScopeStore(self.db,1,lifecycle_guard=nullcontext,current=lambda:self.live,approve=lambda e:e in self.allowed,company_allowed=lambda task,company:task==1 and company==7)
    def allow(self,k,v):
        e=normalize(1,k,v);self.allowed.add(e);return e
    def test_normalize_source_kinds(self):
        cases=[('subdomain',' API.Example.COM.:443 ','subdomain','api.example.com'),('root_domain','api.example.com','root_domain','api.example.com'),('cidr','127.0.0.1:80','ip','127.0.0.1/32'),('ip','2001:db8::/32','cidr','2001:db8::/32'),('ip','[::1]:80','ip','::1/128'),('icp',' A\u2003B ','icp','ab'),('keyword',' A\t B ','keyword','a b')]
        for k,v,want,value in cases:
            with self.subTest(k=k,v=v):
                e=normalize(1,k,v);self.assertEqual(e.kind,want);self.assertEqual(e.domain or e.net or e.value,value)
    def test_no_implicit_root_grant(self):
        self.allow('root_domain','example.com')
        with self.assertRaises(ScopeDenied):self.store.add_agent_scope('root_domain','api.example.com')
        self.assertEqual(self.db.rows,[])
    def test_batch_persistence_and_idempotence(self):
        entries=[self.allow('subdomain','a.example.com'),self.allow('ip','127.0.0.1')]
        self.assertEqual(self.store.add_many(entries),tuple(entries));self.store.add_many(entries)
        self.assertEqual(len(self.db.rows),2)
        self.assertIn('FOR UPDATE',self.db.calls[0].sql)
    def test_denied_second_rolls_back(self):
        with self.assertRaises(ScopeDenied):self.store.add_many([self.allow('keyword','one'),normalize(1,'keyword','two')])
        self.assertEqual(self.db.rows,[])
    def test_database_error_rolls_back(self):
        self.db.fail=True
        with self.assertRaises(RuntimeError):self.store.add_many([self.allow('keyword','one'),self.allow('keyword','two')])
        self.assertEqual(self.db.rows,[])
    def test_company_explicit_scope_and_existing(self):
        e=self.allow('company','7');self.store.add_many([e]);self.assertEqual(self.db.rows[0][2],7)
        with self.assertRaises(ScopeDenied):self.store.add_many([self.allow('company','8')])
        self.db.company=[]
        with self.assertRaises(ScopeDenied):self.store.add_many([e])
    def test_late_grant_change(self):
        def approve(e):self.live=False;return True
        self.store.approve=approve
        with self.assertRaises(ScopeDenied):self.store.add_many([normalize(1,'keyword','one')])
        self.assertEqual(self.db.rows,[])
    def test_missing_deleted_archived_parent(self):
        e=self.allow('keyword','one')
        for parent,archive in [(None,[]),((1,'deleted'),[]),((1,None),[('archived',)]),((2,None),[])]:
            self.db.parent=parent;self.db.archive=archive
            with self.assertRaises(ScopeDenied):self.store.add_many([e])
        self.assertEqual(self.db.rows,[])
    def test_malformed_or_noncanonical_before_sql(self):
        for entry in [TaskScope(2,'keyword',value='x'),TaskScope(1,'ip',net='127.0.0.1/24'),TaskScope(1,'keyword',value='X'),TaskScope(1,'subdomain',domain='a.example',value='extra')]:
            with self.assertRaises(ScopeInvalid):self.store.add_many([entry])
        self.assertEqual(self.db.calls,[])
    def test_invalid_inputs(self):
        for kind,value in [('domain','example.com'),('company','Example Inc'),('cidr','10.0.0.1/24'),('subdomain','https://x.example'),('ip','fe80::1%eth0'),('keyword','\x00'),('subdomain','*.example.com')]:
            with self.subTest(kind=kind,value=value),self.assertRaises(ScopeInvalid):normalize(1,kind,value)
        with self.assertRaises(ScopeInvalid):normalize(True,'keyword','x')
        with self.assertRaises(ScopeInvalid):self.store.add_many([])

if __name__=='__main__':unittest.main()
