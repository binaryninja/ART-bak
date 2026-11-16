"""Explicit optional read routes with real JWT/WSGI and inert metadata DB."""
import io,json,unittest
from contextlib import nullcontext
from artex.server.auth import AuthService,sign_jwt
from artex.server.core import Router,json_response
from artex.server.wsgi import WSGIApplication
from artex.server.intercept_config_read import InterceptConfigReader,InterceptConfigReadService
from artex.db.tool_intercept_read import ToolInterceptReader
from artex.server.tool_intercept_read import ToolInterceptReadService
from artex.server.intercept_read_setup import bind_intercept_reads
from test_application import TestCodec
from test_tool_intercept_read import row

class DB:
    def __init__(self):self.calls=[];self.hook=None
    def fetch_all(self,q):
        self.calls.append(q)
        if self.hook:self.hook()
        return [('["private-tool"]',)] if 'FROM settings' in q.sql else [row()]

class Tests(unittest.TestCase):
    def setUp(self):
        self.db=DB();self.allowed=True;self.global_allowed=True;self.grants=[]
        self.key=b'i'*32;self.auth=AuthService(object(),TestCodec(),self.key,clock=lambda:1000.)
        self.original=WSGIApplication(self.auth.router())
        self.config=InterceptConfigReadService(InterceptConfigReader(self.db),authorize=lambda r:self.allowed,current=lambda:True,lifecycle_guard=lambda:nullcontext())
        self.rules=ToolInterceptReadService(ToolInterceptReader(self.db),authorize=lambda r:self.allowed,current=lambda:True,lifecycle_guard=lambda:nullcontext())
        self.bundle=self.bind()
    def bind(self):
        return bind_intercept_reads(self.original,config_service=self.config,rules_service=self.rules,
            authorize_global=lambda req,kind:self.grants.append(kind) or self.global_allowed)
    def request(self,path='/api/intercept/tool-config',method='GET',token=True,start=None,application=None,body=b''):
        state=[]
        env={'REQUEST_METHOD':method,'PATH_INFO':path,'QUERY_STRING':'','CONTENT_LENGTH':str(len(body)),'wsgi.input':io.BytesIO(body)}
        if token:env['HTTP_AUTHORIZATION']='Bearer '+sign_jwt(self.key,now=1000.)
        def response(status,headers):
            state.append((status,headers))
            if start:start()
        chunks=(application or self.bundle.application)(env,response)
        return int(state[0][0].split()[0]),b''.join(chunks)
    def test_inert_optional_original_untouched_and_both_routes_readable(self):
        self.assertEqual(self.db.calls,[]);self.assertEqual(self.grants,[])
        self.assertEqual(self.request(application=self.original)[0],404)
        self.assertEqual(self.request()[0],200)
        status,body=self.request('/api/intercept/rules')
        self.assertEqual(status,200);self.assertEqual(json.loads(body)['rules'][0]['id'],9)
        self.assertTrue(all(q.sql.startswith(('SELECT','WITH')) for q in self.db.calls))
    def test_jwt_denied_before_global_grants_or_sql(self):
        self.assertEqual(self.request(token=False)[0],401)
        self.assertEqual(self.grants,[]);self.assertEqual(self.db.calls,[])
    def test_global_grant_independent_from_service_grant(self):
        self.global_allowed=False
        self.assertEqual(self.request()[0],403);self.assertEqual(self.db.calls,[])
        self.global_allowed=True;self.allowed=False
        self.assertEqual(self.request()[0],403);self.assertEqual(self.db.calls,[])
    def test_second_route_conflict_does_not_mount_first(self):
        original=tuple(self.original.router._routes)
        self.original.router.add('GET','/api/intercept/rules',lambda r,p:json_response(200,{}))
        before=tuple(self.original.router._routes)
        with self.assertRaises(ValueError):self.bind()
        self.assertEqual(tuple(self.original.router._routes),before)
        self.assertEqual(len(before),len(original)+1);self.assertEqual(self.db.calls,[]);self.assertEqual(self.grants,[])
    def test_dynamic_conflict_denied(self):
        self.original.router.add('GET','/api/intercept/{kind}',lambda r,p:json_response(200,{}))
        with self.assertRaises(ValueError):self.bind()
        self.assertEqual(self.db.calls,[])
    def test_no_mutations_or_head_widening(self):
        for method in ('POST','PUT','DELETE'):
            self.assertEqual(self.request(method=method)[0],405)
        status,body=self.request(method='HEAD')
        self.assertEqual(status,400);self.assertEqual(body,b'');self.assertEqual(self.db.calls,[])
    def test_original_auth_swap_after_sql_discards_body(self):
        self.db.hook=lambda:setattr(self.original.router,'authorize',lambda r:True)
        status,body=self.request()
        self.assertEqual(status,503);self.assertNotIn(b'private-tool',body)
    def test_jwt_clock_swap_after_sql_discards_body(self):
        self.db.hook=lambda:setattr(self.auth,'clock',lambda:1000.)
        status,body=self.request();self.assertEqual(status,503);self.assertNotIn(b'private-tool',body)
    def test_start_response_revocation_aborts_before_body(self):
        for revoke in (lambda:setattr(self,'global_allowed',False),lambda:setattr(self,'allowed',False)):
            self.global_allowed=True;self.allowed=True
            with self.assertRaisesRegex(RuntimeError,'disclosure revoked'):
                self.request(start=revoke)
    def test_unrelated_path_uses_original_application(self):
        status,body=self.request('/api/health',token=False)
        self.assertEqual(status,200);self.assertTrue(json.loads(body)['ok']);self.assertEqual(self.db.calls,[])
    def test_foreign_executor_and_generic_auth_refused(self):
        self.rules.reader.executor=DB()
        with self.assertRaises(ValueError):self.bind()
        self.rules.reader.executor=self.db
        self.original.router=Router(lambda r:True)
        with self.assertRaises(ValueError):self.bind()
    def test_original_reader_method_rebind_refused(self):
        self.config.reader.read=lambda:['unexpected']
        with self.assertRaises(ValueError):self.bind()
