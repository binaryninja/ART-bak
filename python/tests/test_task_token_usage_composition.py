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
        self.source=True;self.request=True;self.current_hook=lambda:True;self.hook=lambda:None;self.aggregates=0;self.calls=[]
        original=self.db.fetch_all
        def fetch(query):
            if query.sql.startswith('SELECT id FROM tasks WHERE id=') and 'FOR SHARE' in query.sql:return [(query.params[0],)]
            if 'FROM activity' in query.sql:
                self.aggregates+=1;self.hook()
                if 'GROUP BY worker' in query.sql:return [('worker#7',11,3,2,1)]
                if 'GROUP BY session_key' in query.sql:return [('intent:7',11,3,2,1)]
                return [(11,3,2,1)]
            return original(query)
        self.db.fetch_all=fetch;self.bind()
    def options(self):
        return dict(request_authorize=lambda *a:False,context_authorize=lambda *a:False,asset_authorize=lambda *a:False,
            runtime_snapshot=lambda *a:None,activity_authorize=lambda *a:False,activity_raw_authorize=lambda *a:False,
            activity_request_authorize=lambda *a:False,stream_authorize=lambda *a:False)
    def bind(self,enabled=True):
        opts=self.options()
        if enabled:
            opts.update(token_usage_workspace='synthetic-workspace',token_usage_authorize=self.source_grant,
                token_usage_request_authorize=lambda *a:self.request)
        self.builder=TaskReadBuilder(self.db,**opts)
        self.router=TaskRuntimeReadRouter(self.f.f.tasks,self.builder,revision='read-r1',authorize_selector=lambda req,id:id not in self.f.f.denied,
            current=lambda *a:self.current_hook(),lifecycle_guard=lambda *a:nullcontext())
        self.original=build_application(self.db,b'x'*32,TestCodec());self.app=self.router.bind(self.original)
    def source_grant(self,scope,operation):self.calls.append((scope.task_id,scope.exploration_id,operation));return self.source
    def call(self,query='task=5',auth=True,headers=None):
        statuses=[];env={'REQUEST_METHOD':'GET','PATH_INFO':'/api/exploration/tokens','QUERY_STRING':query,'CONTENT_LENGTH':'0','wsgi.input':io.BytesIO()}
        if auth:env['HTTP_AUTHORIZATION']='Bearer '+sign_jwt(b'x'*32,now=1000.)
        self.original.router.authorize.__self__.clock=lambda:1000.
        def start(status,values):statuses.append(status);headers() if headers else None
        raw=b''.join(_AuthenticatedView(self.app,lambda:None)(env,start))
        return int(statuses[0].split()[0]),raw
    def test_real_projection_shape_and_selected_scope(self):
        for task in (5,6,5):
            status,raw=self.call('task='+str(task));self.assertEqual(status,200)
            value=json.loads(raw);self.assertEqual(value['total'],dict(input_tokens=11,output_tokens=3,cache_read_tokens=2,cache_write_tokens=1))
            self.assertEqual(value['sessions'][0]['session'],'intent:7');self.assertNotIn('cost',value)
            self.assertEqual(self.calls[-1],(task,self.db.scopes[task],'exploration'))
    def test_no_optional_or_partial_grants(self):
        self.bind(False);self.assertEqual(self.call()[0],503);self.assertEqual(self.aggregates,0)
        for option in (dict(token_usage_workspace='x'),dict(token_usage_authorize=lambda *a:True),dict(token_usage_request_authorize=lambda *a:True)):
            with self.assertRaises(ValueError):TaskReadBuilder(self.db,**self.options(),**option)
    def test_auth_and_denials_before_aggregate(self):
        self.assertEqual(self.call(auth=False)[0],401);self.assertEqual(self.f.f.fetches,[])
        self.f.f.denied.add(5);self.assertEqual(self.call()[0],403);self.assertEqual(self.aggregates,0)
        self.f.f.denied.clear();self.source=False;self.assertEqual(self.call()[0],403);self.assertEqual(self.aggregates,0)
        self.source=True;self.request=False;self.assertEqual(self.call()[0],403);self.assertEqual(self.aggregates,0)
    def test_exact_query_no_global_or_wrong_scope(self):
        for query in ('','task=active','task=05','task=5&task=6','task=5&days=3'):
            self.assertEqual(self.call(query)[0],400)
        self.assertEqual(self.call('task=999')[0],404);self.assertEqual(self.aggregates,0)
    def test_postsql_revocation(self):
        for flag in ('source','request'):
            self.source=self.request=True;self.hook=lambda flag=flag:setattr(self,flag,False)
            status,raw=self.call();self.assertEqual(status,403);self.assertNotIn(b'input_tokens',raw)
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
        self.assertIn(b'input_tokens',run()[1]);last[0]=count[0];self.assertGreater(last[0],0)
        for value in ('source','request','selector','jwt'):
            self.source=self.request=True;self.f.f.denied.clear();kind[0]=value
            status,raw=run();self.assertEqual(status,200);self.assertEqual(raw,b'');self.assertEqual(count[0],last[0])
    def test_policy_tuple_rebinding_fails_closed(self):
        self.builder._token_policies=('foreign',lambda *a:True,lambda *a:True)
        self.assertEqual(self.call()[0],400);self.assertEqual(self.aggregates,0)
