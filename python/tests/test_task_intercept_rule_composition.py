import io,json,unittest
from contextlib import nullcontext
from artex.server.task_read_builder import TaskReadBuilder
from artex.server.task_runtime_read_router import TaskRuntimeReadRouter
from artex.server.application import build_application
from artex.server.runtime_listener import _AuthenticatedView
from artex.server.auth import sign_jwt
from test_application import TestCodec
import test_task_intent_assets_composition as fixture

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.db=self.f.db
        self.source=True;self.request=True;self.current_hook=lambda:True;self.hook=lambda:None;self.aggregates=0;self.calls=[];self.items=None
        original=self.db.fetch_all
        def fetch(query):
            if query.sql.startswith('SELECT id FROM tasks WHERE id=') and 'FOR UPDATE' in query.sql:return [(query.params[0],)]
            if query.sql.startswith('WITH selected AS'):
                from datetime import datetime,timezone
                self.aggregates+=1;self.hook()
                return self.items if self.items is not None else [(1,False,'exact_domain','example.test','fixture',False,'allow',datetime(2026,1,1,tzinfo=timezone.utc),datetime(2026,1,1,tzinfo=timezone.utc))]
            return original(query)
        self.db.fetch_all=fetch;self.bind()
    def options(self):
        return dict(request_authorize=lambda *a:False,context_authorize=lambda *a:False,asset_authorize=lambda *a:False,
            runtime_snapshot=lambda *a:None,activity_authorize=lambda *a:False,activity_raw_authorize=lambda *a:False,
            activity_request_authorize=lambda *a:False,stream_authorize=lambda *a:False)
    def bind(self,enabled=True):
        opts=self.options()
        if enabled:
            opts.update(intercept_rule_workspace='test-workspace',intercept_rule_authorize=self.source_grant,intercept_rule_request_authorize=lambda *a:self.request)
        self.builder=TaskReadBuilder(self.db,**opts)
        self.router=TaskRuntimeReadRouter(self.f.f.tasks,self.builder,revision='read-r1',authorize_selector=lambda req,id:id not in self.f.f.denied,
            current=lambda *a:self.current_hook(),lifecycle_guard=lambda *a:nullcontext())
        self.original=build_application(self.db,b'x'*32,TestCodec());self.app=self.router.bind(self.original)
    def source_grant(self,scope):self.calls.append((scope.task_id,scope.exploration_id));return self.source
    def call(self,query='',task=5,auth=True,headers=None):
        statuses=[];env={'REQUEST_METHOD':'GET','PATH_INFO':f'/api/tasks/{task}/intercept-rules','QUERY_STRING':query,'CONTENT_LENGTH':'0','wsgi.input':io.BytesIO()}
        if auth:env['HTTP_AUTHORIZATION']='Bearer '+sign_jwt(b'x'*32,now=1000.)
        self.original.router.authorize.__self__.clock=lambda:1000.
        def start(status,values):statuses.append(status);headers() if headers else None
        raw=b''.join(_AuthenticatedView(self.app,lambda:None)(env,start))
        return int(statuses[0].split()[0]),raw
    def test_real_projection_shape_and_selected_scope(self):
        for task in (5,6,5):
            status,raw=self.call(task=task);self.assertEqual(status,200)
            value=json.loads(raw);self.assertEqual(value['rules'][0]['pattern'],'example.test');self.assertFalse(value['rules'][0]['enabled']);self.assertEqual(value['rules'][0]['action'],'allow')
            self.assertEqual(self.calls[-1],(task,self.db.scopes[task]))
    def test_no_optional_or_partial_grants(self):
        self.bind(False);self.assertEqual(self.call()[0],503);self.assertEqual(self.aggregates,0)
        for option in (dict(intercept_rule_authorize=lambda *a:True),dict(intercept_rule_request_authorize=lambda *a:True)):
            with self.assertRaises(ValueError):TaskReadBuilder(self.db,**self.options(),**option)
    def test_auth_and_denials_before_aggregate(self):
        self.assertEqual(self.call(auth=False)[0],401);self.assertEqual(self.f.f.fetches,[])
        self.f.f.denied.add(5);self.assertEqual(self.call()[0],403);self.assertEqual(self.aggregates,0)
        self.f.f.denied.clear();self.source=False;self.assertEqual(self.call()[0],403);self.assertEqual(self.aggregates,0)
        self.source=True;self.request=False;self.assertEqual(self.call()[0],403);self.assertEqual(self.aggregates,0)
    def test_exact_query_no_global_or_wrong_scope(self):
        for query in ('task=5','task=active','task=05','task=5&task=6','task=5&days=3'):
            self.assertEqual(self.call(query)[0],400)
        self.assertEqual(self.call(task=999)[0],404);self.assertEqual(self.aggregates,0)
    def test_postsql_revocation(self):
        for flag in ('source','request'):
            self.source=self.request=True;self.hook=lambda flag=flag:setattr(self,flag,False)
            status,raw=self.call();self.assertEqual(status,403);self.assertNotIn(b'example.test',raw)
    def test_postheader_revocation_and_expiry(self):
        for kind in ('source','request','selector','jwt'):
            self.source=self.request=True;self.f.f.denied.clear()
            def revoke():
                if kind in ('source','request'):setattr(self,kind,False)
                elif kind=='selector':self.f.f.denied.add(5)
                else:self.original.router.authorize.__self__.clock=lambda:1000000.
            status,raw=self.call(headers=revoke);self.assertEqual(status,200);self.assertEqual(raw,b'')
    def test_final_current_revocation(self):
        post=[False];count=[0];last=[None];kind=[None]
        def current():
            if post[0]:
                count[0]+=1
                if count[0]==last[0]:
                    if kind[0] in ('source','request'):setattr(self,kind[0],False)
                    elif kind[0]=='selector':self.f.f.denied.add(5)
                    elif kind[0]=='jwt':self.original.router.authorize.__self__.clock=lambda:1000000.
            return True
        self.current_hook=current
        def run():
            post[0]=False;count[0]=0
            return self.call(headers=lambda:post.__setitem__(0,True))
        self.assertIn(b'example.test',run()[1]);last[0]=count[0];self.assertGreater(last[0],0)
        for value in ('source','request','selector','jwt'):
            self.source=self.request=True;self.f.f.denied.clear();kind[0]=value
            status,raw=run();self.assertEqual(status,200);self.assertEqual(raw,b'');self.assertEqual(count[0],last[0])
    def test_policy_tuple_rebinding_fails_closed(self):
        self.builder._intercept_rule_policies=(lambda *a:True,lambda *a:True)
        self.assertEqual(self.call()[0],400);self.assertEqual(self.aggregates,0)

    def test_empty_bounds_and_sql_sentinel(self):
        self.items=[];self.assertEqual(json.loads(self.call()[1]),{'rules':[]})
        row=(1,True,'exact_domain',None,None,False,'block',None,None)
        for rows in ([row],[row]*501):
            self.items=rows;self.assertEqual(self.call()[0],503)
    def test_cross_family_callbacks_do_not_share_rule_grants(self):
        from artex.server.task_runtime_reads import TaskReadScope
        from artex.server.core import Request
        for allow_rules in (True,False):
            calls=[]
            def named(name,value):
                def grant(*a):calls.append(name);return value
                return grant
            opts=self.options()
            opts.update(coverage_authorize=named('coverage_source',not allow_rules),coverage_request_authorize=named('coverage_request',not allow_rules),coverage_asset_authorize=lambda *a:True,
                constraint_authorize=named('constraint_source',not allow_rules),constraint_request_authorize=named('constraint_request',not allow_rules),
                intercept_rule_workspace='test',intercept_rule_authorize=named('rule_source',allow_rules),intercept_rule_request_authorize=named('rule_request',allow_rules))
            scope=TaskReadScope(5,9,'r');services=TaskReadBuilder(self.db,**opts).build(scope,current=lambda *a:True,lifecycle_guard=lambda *a:nullcontext())
            for service,family in ((services.coverage,'coverage'),(services.constraints,'constraint')):
                for index,name,args in ((2,'source',(scope,5,9) if family=='coverage' else (scope,)),(3,'request',(Request('GET','/'),scope,None) if family=='coverage' else (Request('GET','/'),scope))):
                    calls.clear();self.assertEqual(service._original[index](*args),not allow_rules);self.assertEqual(calls,[family+'_'+name])
            for index,name,args in ((3,'source',(scope,)),(4,'request',(Request('GET','/'),scope))):
                calls.clear();self.assertEqual(services.intercept_rules._original[index](*args),allow_rules);self.assertEqual(calls,['rule_'+name])
