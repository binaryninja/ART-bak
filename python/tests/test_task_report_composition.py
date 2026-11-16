import io,unittest
from contextlib import nullcontext
from artex.server.task_read_builder import TaskReadBuilder
from artex.server.task_runtime_read_router import TaskRuntimeReadRouter
from artex.server.application import build_application
from artex.server.runtime_listener import _AuthenticatedView
from artex.server.auth import sign_jwt
from artex.db.task_report import ASSET_TYPES
from test_application import TestCodec
import test_task_scope_composition as fixture

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.db=self.f.db
        self.source=self.request=self.global_allowed=True;self.data_reads=[];self.hook=lambda:None;self.current_hook=lambda:True
        old=self.db.fetch_all
        def fetch(q):
            if q.sql.startswith('SELECT CASE WHEN octet_length(description)'):
                self.data_reads.append(('task',q.params));self.hook();return [('task-'+str(q.params[0]),'goal')]
            if q.sql.startswith('WITH page AS ('):
                self.data_reads.append(('findings',q.params));return []
            if q.sql.startswith('SELECT kinds.kind'):
                self.data_reads.append(('global',q.params));return [(k,3 if k=='ip' else 0) for k in ASSET_TYPES]
            return old(q)
        self.db.fetch_all=fetch;self.bind()
    def bind(self,enabled=True):
        opts=self.f.options()
        # Keep another optional family enabled to detect shared closure capture.
        opts.update(coverage_authorize=lambda *a:self.f.source,coverage_request_authorize=lambda *a:self.f.request,
                    coverage_asset_authorize=lambda *a:True,coverage_connector_authorize=lambda *a:True)
        if enabled:opts.update(report_authorize=lambda *a:self.source,report_request_authorize=self.request_grant,
                               report_global_assets_authorize=lambda *a:self.global_allowed)
        self.builder=TaskReadBuilder(self.db,**opts)
        self.router=TaskRuntimeReadRouter(self.f.f.f.tasks,self.builder,revision='read-r1',authorize_selector=lambda req,id:id not in self.f.f.f.denied,
            current=lambda *a:self.current_hook(),lifecycle_guard=lambda *a:nullcontext())
        self.base=build_application(self.db,b'x'*32,TestCodec());self.app=self.router.bind(self.base)
        self.now=1000.;self.base.router.authorize.__self__.clock=lambda:self.now
    def request_grant(self,request,scope):
        self.bound_request=request
        return self.request
    def call(self,query='task=5',headers=lambda:None):
        statuses=[];env={'REQUEST_METHOD':'GET','PATH_INFO':'/api/report','QUERY_STRING':query,'CONTENT_LENGTH':'0',
            'HTTP_AUTHORIZATION':'Bearer '+sign_jwt(b'x'*32,now=1000.),'wsgi.input':io.BytesIO()}
        def start(status,values):statuses.append(status);headers()
        raw=b''.join(_AuthenticatedView(self.app,lambda:None)(env,start))
        return int(statuses[0].split()[0]),raw
    def test_real_renderer_and_task_selection(self):
        for task in (5,6,5):
            status,body=self.call('task='+str(task));self.assertEqual(status,200);self.assertIn(('task-'+str(task)).encode(),body)
            self.assertIn(('task',(task,self.db.scopes[task])),self.data_reads)
        self.assertEqual(sum(k=='global' for k,p in self.data_reads),3)
    def test_independent_grants_and_missing_capability(self):
        for name in ('source','request','global_allowed'):
            setattr(self,name,False);self.data_reads.clear()
            self.assertEqual(self.call()[0],403);self.assertEqual(self.data_reads,[]);setattr(self,name,True)
        self.bind(False);self.assertEqual(self.call()[0],503)
        with self.assertRaises(ValueError):TaskReadBuilder(self.db,**self.f.options(),report_authorize=lambda *a:True)
    def test_postheader_independent_denial_and_expiry(self):
        for name in ('source','request','global_allowed','jwt','selector'):
            self.source=self.request=self.global_allowed=True;self.now=1000.;self.f.f.f.denied.clear();self.data_reads.clear()
            def revoke():
                if name=='jwt':self.now=10**10
                elif name=='selector':self.f.f.f.denied.add(5)
                else:setattr(self,name,False)
            status,body=self.call(headers=revoke);self.assertEqual(status,200);self.assertEqual(body,b'')
            self.assertEqual(sum(k=='global' for k,p in self.data_reads),1)
    def test_invalid_selectors(self):
        for q in ('','task=05','task=5&task=6','task=5&other=1'):
            self.data_reads.clear();self.assertEqual(self.call(q)[0],400);self.assertEqual(self.data_reads,[])
    def test_policy_tuple_and_cross_family_isolation(self):
        from artex.server.task_runtime_reads import TaskReadScope
        scope=TaskReadScope(5,self.db.scopes[5],'read-r1')
        services=self.builder.build(scope,current=lambda *a:True,lifecycle_guard=lambda *a:nullcontext())
        self.f.source=False;self.f.request=False
        self.assertEqual(self.call()[0],200)
        self.source=False;self.f.source=True;self.f.request=True
        self.assertFalse(services.report._original[2](scope))
        self.builder._report_policies=(lambda *a:True,)*3
        with self.assertRaises(ValueError):self.builder.validate_bindings()

    def test_final_current_observer_revokes_report_grants(self):
        for name in ('source','request','global_allowed'):
            self.source=self.request=self.global_allowed=True;post=[False]
            def current():
                if post[0]:setattr(self,name,False)
                return True
            self.current_hook=current
            status,body=self.call(headers=lambda:post.__setitem__(0,True))
            self.assertEqual(status,200);self.assertEqual(body,b'')
    def test_postsql_grant_loss_and_overflow_refuse(self):
        self.hook=lambda:setattr(self,'global_allowed',False)
        self.assertEqual(self.call()[0],403)
        self.global_allowed=True;self.hook=lambda:None;old=self.db.fetch_all
        def fetch(q):
            if q.sql.startswith('SELECT CASE WHEN octet_length(description)'):return [(None,'goal')]
            return old(q)
        self.db.fetch_all=fetch
        self.assertEqual(self.call()[0],503)

    def test_final_postheader_current_request_mutation_refuses(self):
        post=[False];count=[0]
        def current():
            if post[0]:count[0]+=1
            return True
        self.current_hook=current
        self.assertEqual(self.call(headers=lambda:post.__setitem__(0,True))[0],200)
        final=count[0];self.assertGreater(final,0)
        for mode in ('target','headers'):
            post[0]=False;count[0]=0
            def mutate():
                if post[0]:
                    count[0]+=1
                    if count[0]==final:
                        if mode=='target':object.__setattr__(self.bound_request,'target','/api/report?task=6')
                        else:self.bound_request.headers['X-Changed']='yes'
                return True
            self.current_hook=mutate
            status,body=self.call(headers=lambda:post.__setitem__(0,True))
            self.assertEqual(status,200);self.assertEqual(body,b'')

    def test_final_jwt_clock_revokes_each_report_grant(self):
        post=[False];count=[0]
        def clock():
            if post[0]:count[0]+=1
            return 1000.
        self.base.router.authorize.__self__.clock=clock
        status,body=self.call(headers=lambda:post.__setitem__(0,True))
        self.assertEqual(status,200);self.assertTrue(body);final=count[0]
        for name in ('source','request','global_allowed'):
            self.source=self.request=self.global_allowed=True;post[0]=False;count[0]=0
            def revoke():
                if post[0]:
                    count[0]+=1
                    if count[0]==final:setattr(self,name,False)
                return 1000.
            self.base.router.authorize.__self__.clock=revoke
            status,body=self.call(headers=lambda:post.__setitem__(0,True))
            self.assertEqual(status,200);self.assertEqual(body,b'')
