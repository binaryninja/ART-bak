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
        self.source=True;self.request=True;self.current_hook=lambda:True;self.hook=lambda:None;self.aggregates=0;self.calls=[];self.profile=True;self.env_grant=True;self.chain=True;self.active=7;self.environment_calls=0
        original=self.db.fetch_all
        def fetch(query):
            if query.sql.startswith('SELECT id FROM tasks WHERE id=') and 'FOR SHARE' in query.sql:return [(query.params[0],)]
            if query.sql.startswith("SELECT id, COALESCE(name,'')") and 'FROM tasks' in query.sql:
                from test_tasks_store import ROW
                row=list(ROW);row[0]=query.params[0];row[6]=self.db.scopes[row[0]];return [tuple(row)]
            if query.sql.startswith('SELECT t.id, t.llm_profile_id'):
                task=query.params[0][0]
                return [(task,7,self.active,3,7,0,'ready','',None)] if self.chain else [(task,None,None,3,None,None,None,None,None)]
            if query.sql.startswith('SELECT task_id, source_task_id') or query.sql.startswith('SELECT task_id, company_id'):return []
            if 'FROM agents WHERE key=' in query.sql:return []
            if query.sql.startswith('SELECT id FROM llm_profiles WHERE is_default'):return []
            if 'FROM llm_profiles WHERE id=' in query.sql:
                from test_profiles import ROW
                self.aggregates+=1;self.hook();return [ROW]
            return original(query)
        self.db.fetch_all=fetch;self.bind()
    def options(self):
        return dict(request_authorize=lambda *a:False,context_authorize=lambda *a:False,asset_authorize=lambda *a:False,
            runtime_snapshot=lambda *a:None,activity_authorize=lambda *a:False,activity_raw_authorize=lambda *a:False,
            activity_request_authorize=lambda *a:False,stream_authorize=lambda *a:False)
    def bind(self,enabled=True):
        opts=self.options()
        if enabled:
            opts.update(resolution_profile_authorize=lambda *a:self.profile,resolution_environment_authorize=lambda *a:self.env_grant,resolution_configuration_valid=lambda p:True,resolution_environment=self.env,resolution_authorize=self.source_grant,resolution_request_authorize=lambda *a:self.request)
        self.builder=TaskReadBuilder(self.db,**opts)
        self.router=TaskRuntimeReadRouter(self.f.f.tasks,self.builder,revision='read-r1',authorize_selector=lambda req,id:id not in self.f.f.denied,
            current=lambda *a:self.current_hook(),lifecycle_guard=lambda *a:nullcontext())
        self.original=build_application(self.db,b'x'*32,TestCodec());self.app=self.router.bind(self.original)
    def env(self,*args):
        from artex.server.task_profiles import EnvironmentSelection
        self.environment_calls+=1
        return EnvironmentSelection('explicit','openai','synthetic-env',True)
    def source_grant(self,scope):self.calls.append((scope.task_id,scope.exploration_id));return self.source
    def call(self,query='',task=5,auth=True,headers=None):
        statuses=[];env={'REQUEST_METHOD':'GET','PATH_INFO':f'/api/tasks/{task}/llm/resolution','QUERY_STRING':query,'CONTENT_LENGTH':'0','wsgi.input':io.BytesIO()}
        if auth:env['HTTP_AUTHORIZATION']='Bearer '+sign_jwt(b'x'*32,now=1000.)
        self.original.router.authorize.__self__.clock=lambda:1000.
        def start(status,values):statuses.append(status);headers() if headers else None
        raw=b''.join(_AuthenticatedView(self.app,lambda:None)(env,start))
        return int(statuses[0].split()[0]),raw
    def test_real_projection_shape_and_selected_scope(self):
        for task in (5,6,5):
            status,raw=self.call(task=task);self.assertEqual(status,200)
            value=json.loads(raw);self.assertEqual(value['planner']['source'],'task_chain');self.assertTrue(value['planner']['available']);self.assertNotIn(b'dummy-fixture-key',raw);self.assertNotIn(b'https:',raw)
            self.assertEqual(self.calls[-1],(task,self.db.scopes[task]))
    def test_no_optional_or_partial_grants(self):
        self.bind(False);self.assertEqual(self.call()[0],503);self.assertEqual(self.aggregates,0)
        for option in (dict(resolution_authorize=lambda *a:True),dict(resolution_request_authorize=lambda *a:True)):
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
        for flag in ('source','request','profile'):
            self.source=self.request=self.profile=True;self.hook=lambda flag=flag:setattr(self,flag,False)
            status,raw=self.call();self.assertEqual(status,403);self.assertNotIn(b'task_chain',raw)
    def test_postheader_revocation_and_expiry(self):
        for kind in ('source','request','profile','selector','jwt'):
            self.source=self.request=self.profile=True;self.f.f.denied.clear()
            def revoke():
                if kind in ('source','request','profile'):setattr(self,kind,False)
                elif kind=='selector':self.f.f.denied.add(5)
                else:self.original.router.authorize.__self__.clock=lambda:1000000.
            status,raw=self.call(headers=revoke);self.assertEqual(status,200);self.assertEqual(raw,b'')
    def test_final_current_revocation(self):
        post=[False];count=[0];last=[None];kind=[None]
        def current():
            if post[0]:
                count[0]+=1
                if count[0]==last[0]:
                    if kind[0] in ('source','request','profile'):setattr(self,kind[0],False)
                    elif kind[0]=='selector':self.f.f.denied.add(5)
                    elif kind[0]=='jwt':self.original.router.authorize.__self__.clock=lambda:1000000.
            return True
        self.current_hook=current
        def run():
            post[0]=False;count[0]=0
            return self.call(headers=lambda:post.__setitem__(0,True))
        self.assertIn(b'task_chain',run()[1]);last[0]=count[0];self.assertGreater(last[0],0)
        for value in ('source','request','profile','selector','jwt'):
            self.source=self.request=self.profile=True;self.f.f.denied.clear();kind[0]=value
            status,raw=run();self.assertEqual(status,200);self.assertEqual(raw,b'');self.assertEqual(count[0],last[0])
    def test_policy_tuple_rebinding_fails_closed(self):
        self.builder._resolution_policies=(lambda *a:True,lambda *a:True)
        self.assertEqual(self.call()[0],400);self.assertEqual(self.aggregates,0)

    def test_profile_denial_before_private_fetch(self):
        self.profile=False;self.assertEqual(self.call()[0],403);self.assertEqual(self.aggregates,0)
    def test_exhausted_chain_never_environment(self):
        self.active=None;status,raw=self.call();self.assertEqual(status,200)
        self.assertEqual(self.environment_calls,0);self.assertFalse(json.loads(raw)['planner']['available'])
    def test_explicit_environment_and_final_grant(self):
        self.chain=False;status,raw=self.call();self.assertEqual(status,200)
        self.assertEqual(json.loads(raw)['planner']['source'],'environment')
        status,raw=self.call(headers=lambda:setattr(self,'env_grant',False));self.assertEqual(status,200);self.assertEqual(raw,b'')
    def test_resolution_callbacks_do_not_replace_other_grants(self):
        from artex.server.task_runtime_reads import TaskReadScope
        scope=TaskReadScope(5,9,'r')
        for value in (True,False):
            seen=[]
            def policy(name,result):
                def f(*a):seen.append(name);return result
                return f
            opts=self.options();opts.update(coverage_authorize=policy('coverage',not value),coverage_request_authorize=lambda *a:True,coverage_asset_authorize=lambda *a:True,
                constraint_authorize=policy('constraint',not value),constraint_request_authorize=lambda *a:True,
                resolution_authorize=policy('resolution',value),resolution_request_authorize=lambda *a:True,resolution_profile_authorize=lambda *a:True,
                resolution_environment_authorize=lambda *a:True,resolution_configuration_valid=lambda *a:True,resolution_environment=lambda *a:None)
            services=TaskReadBuilder(self.db,**opts).build(scope,current=lambda *a:True,lifecycle_guard=lambda *a:nullcontext())
            for name,fn,args,expected in (('coverage',services.coverage._original[2],(scope,5,9),not value),('constraint',services.constraints._original[2],(scope,),not value),('resolution',services.resolution._original[2],(scope,),value)):
                seen.clear();self.assertEqual(fn(*args),expected);self.assertEqual(seen,[name])
