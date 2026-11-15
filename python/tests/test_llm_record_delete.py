"""Exact-task debug deletion using transaction fakes only; no real data."""
from contextlib import contextmanager
import unittest
from urllib.parse import quote
from artex.db.llm_records import LLMRecordStore,RecordInvalid,RecordUnavailable
from artex.server.llm_records import LLMRecordService
from artex.server.core import Router,Request


class DB:
    def __init__(self):self.calls=[];self.events=[];self.count=3;self.fail=False;self.commit_fail=False
    @contextmanager
    def transaction(self):
        self.events.append('begin')
        try:
            yield self
            if self.commit_fail:raise RuntimeError('private commit detail')
        except BaseException:self.events.append('rollback');raise
        else:self.events.append('commit')
    def execute(self,q):
        self.calls.append(q)
        if self.fail:raise RuntimeError('private database detail')
        return self.count


class DeleteTests(unittest.TestCase):
    def setUp(self):
        self.db=DB();self.store=LLMRecordStore(self.db,task_id='task');self.policies=[];self.resolutions=[]
    def router(self,policy=None,read=True):
        def resolve(req,action,selection):self.resolutions.append(selection);return self.store
        def allow(req,action,selection):self.policies.append((action,selection));return read
        service=LLMRecordService(resolve,authorize=allow,redact=lambda req,row,body:row,delete_authorize=policy)
        router=Router(lambda req:req.header('Authorization')=='fixture');service.register(router);return router
    def request(self,query='task=task',auth=True,body=b''):
        return Request('DELETE','/api/llm/records'+('?' + query if query else ''),{'Authorization':'fixture' if auth else ''},body)
    def test_disabled_default_and_auth_before_policies(self):
        router=self.router()
        self.assertEqual(router.dispatch(self.request()).status,503)
        self.assertEqual(router.dispatch(self.request(auth=False)).status,401)
        self.assertEqual(self.resolutions,[]);self.assertEqual(self.db.calls,[]);self.assertEqual(self.policies,[])
    def test_exact_scoped_delete_and_response(self):
        approvals=[]
        def policy(req,action,selection):approvals.append(selection);return True
        response=self.router(policy).dispatch(self.request('task=%20task%20'))
        self.assertEqual(response.status,200);self.assertEqual(response.json(),{'deleted':3})
        self.assertEqual([s.store_task for s in approvals],[None,'task'])
        self.assertEqual(self.db.calls[0].params,('task',))
        self.assertEqual(self.db.calls[0].sql,"DELETE FROM llm_records WHERE COALESCE(task_id,'') = $1")
        self.assertEqual(self.db.events,['begin','commit'])
    def test_global_store_still_requires_exact_literal_task(self):
        self.store=LLMRecordStore(self.db)
        task="task_%';--"
        self.assertEqual(self.router(lambda *a:True).dispatch(self.request('task='+quote(task))).status,200)
        self.assertEqual(self.db.calls[0].params,(task,));self.assertNotIn(task,self.db.calls[0].sql)
    def test_invalid_selection_never_deletes(self):
        router=self.router(lambda *a:True)
        for query in ('','task=','task=%20','task=task&task=task','task=task&session=x','task=task&model=m',
                      'task='+quote('é'*2049),'task=%00'):
            self.assertEqual(router.dispatch(self.request(query)).status,400,query[:60])
        self.assertEqual(router.dispatch(self.request(body=b'{}')).status,400)
        self.assertEqual(self.db.calls,[]);self.assertEqual(self.resolutions,[])
    def test_wrong_scope_and_nonliteral_approval_reject(self):
        router=self.router(lambda *a:True)
        self.assertEqual(router.dispatch(self.request('task=other')).status,403)
        for value in (1,'yes',None,False):
            self.assertEqual(self.router(lambda *a:value).dispatch(self.request()).status,403)
        self.assertEqual(self.router(lambda *a:True,read=False).dispatch(self.request()).status,403)
        self.assertEqual(self.db.calls,[])
    def test_scoped_second_policy_denial_and_policy_failure(self):
        self.assertEqual(self.router(lambda req,a,s:s.store_task is None).dispatch(self.request()).status,403)
        def broken(*a):raise RuntimeError('private approval detail')
        response=self.router(broken).dispatch(self.request())
        self.assertEqual(response.status,503);self.assertNotIn(b'private',response.body)
        self.assertEqual(self.db.calls,[])
    def test_store_invalid_or_scope_mismatch_before_transaction(self):
        for task in ('',' ','other','task\0','é'*2049,None):
            with self.assertRaises(RecordInvalid):self.store.delete_task(task)
        self.assertEqual(self.db.events,[])
    def test_zero_and_invalid_rowcounts_transactional(self):
        self.db.count=0;self.assertEqual(self.store.delete_task('task'),0)
        for count in (-1,True,None,2**63):
            self.db.count=count
            with self.assertRaises(RecordUnavailable):self.store.delete_task('task')
            self.assertEqual(self.db.events[-1],'rollback')
    def test_database_and_commit_failure_redacted_no_retry(self):
        router=self.router(lambda *a:True)
        for fail,commit_fail in ((True,False),(False,True)):
            self.db.fail=fail;self.db.commit_fail=commit_fail;before=len(self.db.calls)
            response=router.dispatch(self.request())
            self.assertEqual(response.status,500);self.assertNotIn(b'private',response.body)
            self.assertEqual(len(self.db.calls),before+1)
            self.assertEqual(self.db.events[-1],'rollback')
