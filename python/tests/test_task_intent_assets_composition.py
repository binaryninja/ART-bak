import io,json,unittest
from contextlib import contextmanager,nullcontext
from artex.server.task_read_builder import TaskReadBuilder
from artex.server.task_runtime_read_router import TaskRuntimeReadRouter
from artex.server.application import build_application
from artex.server.auth import sign_jwt
from test_application import TestCodec
import test_task_runtime_read_router as base

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=base.Tests();self.f.setUp();self.db=self.f.db
        self.source=True;self.request=True;self.inherited=[];self.source_calls=[];self.asset_reads=0;self.hook=lambda:None;self.current_hook=lambda:True
        original=self.db.fetch_all
        def read(q):
            if q.sql.startswith('SELECT id,exploration_id,COALESCE(coverage_enabled'):
                return [(q.params[0],q.params[1],True)]
            if q.sql.startswith('SELECT source.id,source.exploration_id,source.archived_at'):return self.inherited
            if q.sql.startswith('WITH context AS'):
                self.asset_reads+=1;self.hook()
                return [(7,8,'ip','192.0.2.1','anchor','fixture',None,q.params[1],False)]
            return original(q)
        self.db.fetch_all=read
        @contextmanager
        def transaction():yield self.db
        self.db.transaction=transaction
        self.bind()
    def builder(self,**options):
        return TaskReadBuilder(self.db,request_authorize=lambda *a:False,context_authorize=lambda *a:False,
            asset_authorize=lambda *a:False,runtime_snapshot=lambda *a:None,activity_authorize=lambda *a:False,
            activity_raw_authorize=lambda *a:False,activity_request_authorize=lambda *a:False,stream_authorize=lambda *a:False,**options)
    def approve(self,scope,task,exp):
        self.source_calls.append((scope.task_id,task,exp));return self.source and task==scope.task_id
    def bind(self,enabled=True):
        options=dict(intent_assets_authorize=self.approve,intent_assets_request_authorize=lambda *a:self.request) if enabled else {}
        self.b=self.builder(**options)
        router=TaskRuntimeReadRouter(self.f.tasks,self.b,revision='intent-assets-r1',authorize_selector=lambda req,id:id not in self.f.denied,
            current=lambda *a:self.current_hook(),lifecycle_guard=lambda *a:nullcontext())
        self.original=build_application(self.db,b'x'*32,TestCodec())
        self.app=router.bind(self.original)
    def call(self,path='/api/tasks/5/intent-assets',auth=True,method='GET'):
        status=[];basepath,_,query=path.partition('?')
        env={'REQUEST_METHOD':method,'PATH_INFO':basepath,'QUERY_STRING':query,'CONTENT_LENGTH':'0','wsgi.input':io.BytesIO()}
        if auth:env['HTTP_AUTHORIZATION']='Bearer '+sign_jwt(b'x'*32)
        raw=b''.join(self.app(env,lambda s,h:status.append(s)))
        return int(status[0].split()[0]),json.loads(raw) if raw else None
    def test_selected_task_navigation_independent_grants(self):
        for task in (5,6,5):
            status,body=self.call(f'/api/tasks/{task}/intent-assets')
            self.assertEqual(status,200);self.assertEqual(body['assets'][0]['source_task_id'],task)
            self.assertFalse(body['assets'][0]['inherited']);self.assertEqual(body['assets'][0]['label'],'192.0.2.1')
        self.assertTrue(all(selected==task for selected,task,exp in self.source_calls))
    def test_omitted_and_half_configured_never_fallback_success(self):
        self.bind(False);self.assertEqual(self.call()[0],503);self.assertEqual(self.asset_reads,0)
        with self.assertRaises(ValueError):self.builder(intent_assets_authorize=lambda *a:True)
        with self.assertRaises(ValueError):self.builder(intent_assets_request_authorize=lambda *a:True)
    def test_jwt_selector_source_and_request_denials(self):
        self.assertEqual(self.call(auth=False)[0],401);self.assertEqual(self.asset_reads,0);self.assertEqual(self.f.fetches,[])
        self.f.denied.add(5);self.assertEqual(self.call()[0],403);self.assertEqual(self.f.fetches,[])
        self.f.denied.clear();self.request=False;self.assertEqual(self.call()[0],403);self.assertEqual(self.asset_reads,0)
        self.request=True;self.source=False;self.assertEqual(self.call()[0],403);self.assertEqual(self.asset_reads,0)
    def test_inherited_source_needs_own_context_grant(self):
        self.inherited=[(6,10,None,False)]
        status,body=self.call();self.assertEqual(status,403);self.assertEqual(self.asset_reads,0)
        self.assertIn((5,6,10),self.source_calls);self.assertNotIn('assets',body)
    def test_final_fetch_revocation_withholds_metadata(self):
        for flag in ('source','request'):
            setattr(self,flag,True);self.hook=lambda flag=flag:setattr(self,flag,False)
            status,body=self.call();self.assertEqual(status,403);self.assertNotIn('192.0.2.1',json.dumps(body))
            setattr(self,flag,True)
    def test_exact_selector_query_and_method_scope(self):
        for path in ('/api/tasks/05/intent-assets','/api/tasks/5/intent-assets?task=6','/api/tasks/5/intent-assets?x=1'):
            self.assertEqual(self.call(path)[0],400)
        self.assertEqual(self.call('/api/tasks/999/intent-assets')[0],404)
        self.assertNotEqual(self.call(method='POST')[0],200);self.assertEqual(self.asset_reads,0)
    def test_policy_tuple_rebinding_fails_before_asset_read(self):
        self.b._intent_asset_policies=(lambda *a:True,lambda *a:True)
        self.assertEqual(self.call()[0],400);self.assertEqual(self.asset_reads,0)

    def test_postheader_source_and_request_revocation_withhold_body(self):
        from artex.server.runtime_listener import _AuthenticatedView
        for flag in ('source','request'):
            setattr(self,flag,True);status=[]
            env={'REQUEST_METHOD':'GET','PATH_INFO':'/api/tasks/5/intent-assets','QUERY_STRING':'','CONTENT_LENGTH':'0','wsgi.input':io.BytesIO(),
                 'HTTP_AUTHORIZATION':'Bearer '+sign_jwt(b'x'*32)}
            def headers(s,h):status.append(s);setattr(self,flag,False)
            body=b''.join(_AuthenticatedView(self.app,lambda:None)(env,headers))
            self.assertTrue(status[0].startswith('200'));self.assertEqual(body,b'')
            setattr(self,flag,True)

    def test_direct_wsgi_receipt_runs_after_actual_headers(self):
        status=[]
        env={'REQUEST_METHOD':'GET','PATH_INFO':'/api/tasks/5/intent-assets','QUERY_STRING':'','CONTENT_LENGTH':'0','wsgi.input':io.BytesIO(),
             'HTTP_AUTHORIZATION':'Bearer '+sign_jwt(b'x'*32)}
        def headers(s,h):status.append(s);self.source=False
        result=self.app(env,headers)
        with self.assertRaisesRegex(RuntimeError,'Disclosure unavailable'):b''.join(result)
        self.assertTrue(status[0].startswith('200'))

    def test_postheader_selector_and_jwt_revocation_withhold_body(self):
        from artex.server.runtime_listener import _AuthenticatedView
        auth=self.original.router.authorize.__self__;now=[1000.];auth.clock=lambda:now[0]
        for kind in ('selector','jwt'):
            self.f.denied.clear();status=[];now[0]=1000.
            env={'REQUEST_METHOD':'GET','PATH_INFO':'/api/tasks/5/intent-assets','QUERY_STRING':'','CONTENT_LENGTH':'0','wsgi.input':io.BytesIO(),
                 'HTTP_AUTHORIZATION':'Bearer '+sign_jwt(b'x'*32,now=1000.)}
            def headers(s,h):
                status.append(s)
                if kind=='selector':self.f.denied.add(5)
                else:now[0]=1000000.
            body=b''.join(_AuthenticatedView(self.app,lambda:None)(env,headers))
            self.assertEqual(body,b'');self.assertTrue(status[0].startswith('200'))

    def test_last_postheader_current_sql_cannot_revoke_observed_grants(self):
        from artex.server.runtime_listener import _AuthenticatedView
        original=self.db.fetch_all;counter=[0];post=[False];last=[None];flag=[None]
        def read(query):
            result=original(query)
            if post[0] and query.sql.startswith('SELECT exploration_id FROM tasks'):
                counter[0]+=1
                if flag[0] is not None and counter[0]==last[0]:setattr(self,flag[0],False)
            return result
        self.db.fetch_all=read
        def invoke():
            counter[0]=0;post[0]=False;statuses=[]
            env={'REQUEST_METHOD':'GET','PATH_INFO':'/api/tasks/5/intent-assets','QUERY_STRING':'','CONTENT_LENGTH':'0','wsgi.input':io.BytesIO(),
                 'HTTP_AUTHORIZATION':'Bearer '+sign_jwt(b'x'*32)}
            def headers(status,headers):statuses.append(status);post[0]=True
            body=b''.join(_AuthenticatedView(self.app,lambda:None)(env,headers))
            self.assertTrue(statuses[0].startswith('200'));return body
        self.assertIn(b'192.0.2.1',invoke());last[0]=counter[0];self.assertGreater(last[0],0)
        for name in ('source','request'):
            self.source=self.request=True;flag[0]=name
            self.assertEqual(invoke(),b'');self.assertEqual(counter[0],last[0])

    def test_last_external_current_cannot_revoke_selected_task_grant(self):
        from artex.server.runtime_listener import _AuthenticatedView
        post=[False];count=[0];last=[None];revoke=[False]
        def current():
            if post[0]:
                count[0]+=1
                if revoke[0] and count[0]==last[0]:self.f.denied.add(5)
            return True
        self.current_hook=current
        def invoke():
            post[0]=False;count[0]=0;statuses=[]
            env={'REQUEST_METHOD':'GET','PATH_INFO':'/api/tasks/5/intent-assets','QUERY_STRING':'','CONTENT_LENGTH':'0','wsgi.input':io.BytesIO(),
                 'HTTP_AUTHORIZATION':'Bearer '+sign_jwt(b'x'*32)}
            def headers(status,headers):statuses.append(status);post[0]=True
            body=b''.join(_AuthenticatedView(self.app,lambda:None)(env,headers))
            self.assertTrue(statuses[0].startswith('200'));return body
        self.assertIn(b'192.0.2.1',invoke());last[0]=count[0];self.assertGreater(last[0],0)
        revoke[0]=True
        self.assertEqual(invoke(),b'');self.assertEqual(count[0],last[0])
