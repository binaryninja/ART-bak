"""Frontend-equivalent bounded HTTP sequence, synthetic SQL/runtime only."""
import io,json,unittest
from contextlib import nullcontext
from artex.server.task_runtime_reads import TaskRuntimeReads,TaskReadScope
from artex.server.stats import RuntimeStats,TaskRuntimeStats
from artex.server.auth import sign_jwt
import test_task_runtime_application as fixture

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.allowed=True;self.grants=[];db=self.f.f.db
        old_all=db.fetch_all;old_one=db.fetch_one
        def all(q):
            if q.sql=='SELECT type, COUNT(*) FROM assets GROUP BY type':return []
            if q.sql.startswith('SELECT exploration_id FROM tasks WHERE'):return [(9,)]
            if q.sql.startswith('SELECT kind, count(*)'):return [('fact',1)]
            if q.sql.startswith('SELECT id,name,format,'):return []
            return old_all(q)
        def one(q):
            if q.sql.startswith("SELECT count(*) FILTER (WHERE state='running')"):return (0,)
            if q.sql.startswith("SELECT count(*), count(*) FILTER (WHERE state='met')"):return (0,0)
            return old_one(q)
        db.fetch_all=all;db.fetch_one=one
        self.reads=TaskRuntimeReads(db,TaskReadScope(5,9,'r1'),authorize=lambda req,s,op:self.grants.append(op) or self.allowed,
            current=lambda s:True,lifecycle_guard=lambda s:nullcontext(),runtime_snapshot=lambda selector:RuntimeStats(True,TaskRuntimeStats('5','inert','inert',False,False,False,0,0)))
    def request(self,path,query='',auth=True,method='GET'):
        statuses=[];env={'REQUEST_METHOD':method,'PATH_INFO':path,'QUERY_STRING':query,'CONTENT_LENGTH':'0','wsgi.input':io.BytesIO()}
        if auth:env['HTTP_AUTHORIZATION']='Bearer '+sign_jwt(b'x'*32)
        data=b''.join(self.f.bundle.app(env,lambda status,headers:statuses.append(status)))
        return int(statuses[0].split()[0]),json.loads(data)
    def test_actual_task_stats_intents_profiles_sequence(self):
        self.f.build(read_services=self.reads)
        for path,query in (('/api/tasks/5',''),('/api/stats','task=5'),('/api/exploration/intents','task=5'),('/api/llm/profiles','')):
            with self.subTest(path=path):self.assertEqual(self.request(path,query)[0],200)
        self.assertIn('stats_global',self.grants)
        self.assertEqual(self.f.p.calls,[])
    def test_auth_scope_and_duplicate_selector_before_reads(self):
        self.f.build(read_services=self.reads)
        self.assertEqual(self.request('/api/stats','task=5',False)[0],401)
        self.assertEqual(self.grants,[])
        for q in ('task=6','task=5&task=5','','task=05'):
            self.assertEqual(self.request('/api/stats',q)[0],400)
        self.assertEqual(self.grants,[])
    def test_final_grant_revocation_hides_metadata(self):
        self.f.build(read_services=self.reads)
        old=self.reads.profiles.list
        def read():
            rows=old();self.allowed=False;return rows
        self.reads.profiles.list=read
        status,data=self.request('/api/llm/profiles')
        self.assertEqual(status,403);self.assertNotIn('profiles',data)
    def test_foreign_executor_prestartup_and_no_mutation_enablement(self):
        original=self.reads.executor;self.reads.executor=object()
        with self.assertRaises(ValueError):self.f.build(read_services=self.reads)
        self.reads.executor=original;self.f.build(read_services=self.reads)
        self.assertEqual(self.request('/api/llm/profiles',method='POST')[0],503)
    def test_findings_own_guard_not_nested_and_source_dto_preserved(self):
        import test_task_findings as finding
        from artex.server.application import build_application
        from test_application import TestCodec
        f=finding.Tests();f.make()
        def forbidden(scope):raise AssertionError('read helper must not enter findings guard')
        reads=TaskRuntimeReads(f.f.db,TaskReadScope(5,9,'r1'),authorize=lambda *a:True,current=lambda s:True,
            lifecycle_guard=forbidden,runtime_snapshot=lambda s:None,findings_service=f.service)
        app=reads.bind(build_application(f.f.db,b'x'*32,TestCodec()))
        status=[];env={'REQUEST_METHOD':'GET','PATH_INFO':'/api/exploration/findings','QUERY_STRING':'task=5','CONTENT_LENGTH':'0','wsgi.input':io.BytesIO(), 'HTTP_AUTHORIZATION':'Bearer '+sign_jwt(b'x'*32)}
        data=json.loads(b''.join(app(env,lambda s,h:status.append(s))))
        self.assertTrue(status[0].startswith('200'));self.assertEqual(data[0]['finding_id'],'21')
        self.assertEqual(data[0]['assets'][0]['id'],'7')

    def test_findings_foreign_scope_rejected_before_binding(self):
        import test_task_findings as finding
        f=finding.Tests();f.make()
        reads=TaskRuntimeReads(f.f.db,TaskReadScope(6,9,'r1'),authorize=lambda *a:True,current=lambda s:True,
            lifecycle_guard=lambda s:nullcontext(),runtime_snapshot=lambda s:None,findings_service=f.service)
        with self.assertRaises(PermissionError):reads.validate_bindings()
        self.assertEqual(f.f.db.calls,[])
