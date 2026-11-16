import io,json,unittest
from contextlib import nullcontext
import test_intercept_read_setup as transport
import test_postgres as driver
from test_tool_intercept_config import NOW
from artex.db.postgres import PostgresExecutor
from artex.db.tool_intercept_config import ToolInterceptConfigStore,tools_digest
from artex.intercept import Interceptor
from artex.intercept.policy_owner import InterceptPolicyOwner
from artex.server.tool_intercept_mutation import ToolInterceptMutationService
from artex.server.tool_intercept_setup import bind_tool_intercept_mutations
from artex.server.auth import sign_jwt
class Cursor(driver.Cursor):
    def execute(self,sql,params=()):
        super().execute(sql,params)
        if self.connection.hook:self.connection.hook(sql)
        if sql.startswith('SELECT updated_at'):self.connection.rows=[(NOW,)]
        elif 'RETURNING id,created_at' in sql:self.connection.rows=[(4,NOW,NOW)]
        elif 'RETURNING id' in sql:self.connection.rows=[(4,)]
        elif 'FROM settings' in sql:self.connection.rows=[]
class Tests(unittest.TestCase):
    def setUp(self):
        self.t=transport.Tests();self.t.setUp();self.addCleanup(self.t.doCleanups)
        self.conn=driver.Connection();self.conn.hook=None
        self.db=PostgresExecutor(self.conn,cursor_factory=Cursor,owns_connection=True)
        self.store=ToolInterceptConfigStore(self.db);self.ic=Interceptor(self.store)
        self.current=lambda:True;self.idle=True;self.allowed=True;self.request_allowed=True;self.seen=[]
        self.owner=InterceptPolicyOwner(self.store,(self.ic,),workspace='test',authorize=lambda m:True,current=lambda:self.current(),idle=lambda ics:self.idle,lifecycle_guard=lambda:nullcontext())
        self.service=ToolInterceptMutationService(self.owner,authorize=lambda r,m:self.request_allowed)
        self.bundle=self.bind()
    def bind(self):return bind_tool_intercept_mutations(self.t.original,mutation_service=self.service,authorize_global=lambda r,m:self.seen.append(m) or self.allowed)
    def request(self,path='/api/intercept/rules',method='POST',body=None,headers=None,token=True,start=None):
        if body is None:body={'name':'literal','pattern':'Bash'}
        data=json.dumps(body).encode() if body is not False else b'';status=[]
        env={'PATH_INFO':path,'REQUEST_METHOD':method,'QUERY_STRING':'','CONTENT_LENGTH':str(len(data)),'wsgi.input':io.BytesIO(data)}
        if token:env['HTTP_AUTHORIZATION']='Bearer '+sign_jwt(self.t.key,now=1000.)
        for k,v in (headers or {}).items():env['HTTP_'+k.upper().replace('-','_')]=v
        def response(s,h):
            status.append(int(s.split()[0]))
            if start:start()
        chunks=self.bundle.application(env,response)
        return status[0],b''.join(chunks)
    def writes(self):return [e for e in self.conn.events if isinstance(e,tuple) and e[0]=='execute' and e[1].startswith(('INSERT','UPDATE','DELETE'))]
    def test_inert_jwt_create_and_same_mutation(self):
        self.assertEqual(self.conn.events,[]);self.assertEqual(self.seen,[])
        self.assertEqual(self.request(token=False)[0],401);self.assertFalse(self.writes())
        status,body=self.request();self.assertEqual(status,200);self.assertEqual(json.loads(body)['pattern'],'Bash')
        self.assertEqual(len(self.writes()),1);self.assertEqual(len({id(m) for m in self.seen}),1);self.assertEqual(self.owner.generation,1)
    def test_all_cas_routes(self):
        for operation,path,method,body in (('update','/api/intercept/rules/4','PUT',{'name':'n','pattern':'Edit'}),('toggle','/api/intercept/rules/4/toggle','POST',{'enabled':True}),('delete','/api/intercept/rules/4','DELETE',False),('tools','/api/intercept/tool-config','PUT',{'enabled_tools':['Bash','Bash']})):
            headers={'X-Rule-Updated-At':NOW.isoformat(),'X-Intercept-Tools-Digest':tools_digest(None)}
            self.assertEqual(self.request(path,method,body,headers)[0],200)
        self.assertTrue(any(isinstance(e,tuple) and e[1]=='LOCK TABLE settings IN SHARE ROW EXCLUSIVE MODE' for e in self.conn.events))
    def test_missing_cas_regex_and_pending(self):
        self.assertEqual(self.request('/api/intercept/rules/4','DELETE',False)[0],400)
        self.assertEqual(self.request(body={'name':'n','pattern':'.*','match_type':'regex'})[0],400)
        self.ic._pending[3]=object();self.assertEqual(self.request()[0],403);self.assertFalse(self.writes())
    def test_final_current_revokes_before_write(self):
        calls=[]
        def current():
            calls.append(1)
            if len(calls)==2:self.allowed=False
            return True
        self.current=current
        self.assertEqual(self.request()[0],403);self.assertFalse(self.writes())
    def test_precommit_unknown_retained_no_replay(self):
        self.conn.hook=lambda sql:setattr(self,'allowed',False) if sql.startswith('INSERT') else None
        self.assertEqual(self.request()[0],503);self.assertTrue(self.owner.unresolved);self.assertNotIn('commit',self.conn.events)
        n=len(self.writes());self.allowed=True;self.assertEqual(self.request()[0],503);self.assertEqual(len(self.writes()),n)
    def test_after_headers_unknown_no_replay(self):
        with self.assertRaises(RuntimeError):self.request(start=lambda:setattr(self,'allowed',False))
        self.assertEqual(len(self.writes()),1);self.assertIn('commit',self.conn.events)
        self.allowed=True;self.assertEqual(self.request()[0],503);self.assertEqual(len(self.writes()),1)
        self.assertTrue(self.owner.unresolved);self.assertTrue(self.store._unresolved)
        with self.assertRaises(Exception):self.bind()
    def test_start_response_failure_after_commit_quarantines_shared_owner(self):
        class LostAcknowledgement(BaseException):pass
        def fail():raise LostAcknowledgement()
        with self.assertRaises(LostAcknowledgement):self.request(start=fail)
        self.assertIn('commit',self.conn.events);self.assertEqual(len(self.writes()),1)
        self.assertTrue(self.bundle.application._unresolved)
        self.assertTrue(self.owner.unresolved);self.assertTrue(self.store._unresolved)
        self.assertEqual(self.request()[0],503);self.assertEqual(len(self.writes()),1)
        with self.assertRaises(Exception):self.bind()
    def test_service_postcommit_ack_denial_quarantines_shared_owner(self):
        checks=[]
        def grant(req,selection):
            checks.append(1)
            return len(checks)!=6
        self.bundle=bind_tool_intercept_mutations(self.t.original,mutation_service=self.service,authorize_global=grant)
        status,body=self.request()
        self.assertEqual(status,503);self.assertIs(json.loads(body)['retry'],False)
        self.assertEqual(len(checks),6);self.assertIn('commit',self.conn.events)
        self.assertEqual(len(self.writes()),1);self.assertTrue(self.owner.unresolved)
        self.assertTrue(self.store._unresolved);self.assertTrue(self.bundle.application._unresolved)
        self.assertEqual(self.request()[0],503);self.assertEqual(len(self.writes()),1)
        with self.assertRaises(Exception):self.bind()
    def test_prewrite_denial_and_response_failure_do_not_poison_owner(self):
        self.allowed=False
        def fail():raise RuntimeError('inert header failure')
        with self.assertRaises(RuntimeError):self.request(start=fail)
        self.assertFalse(self.writes());self.assertFalse(self.owner.unresolved)
        self.assertFalse(self.bundle.application._unresolved)
        self.allowed=True;self.assertEqual(self.request()[0],200)
        self.assertEqual(len(self.writes()),1)
    def test_invalid_request_is_recoverable_without_quarantine(self):
        self.assertEqual(self.request(body={'name':'n','pattern':'.*','match_type':'regex'})[0],400)
        self.assertFalse(self.writes());self.assertFalse(self.owner.unresolved)
        self.assertEqual(self.request()[0],200)
    def test_prewrite_cas_conflict_is_recoverable_without_quarantine(self):
        from datetime import timedelta
        headers={'X-Rule-Updated-At':(NOW-timedelta(seconds=1)).isoformat()}
        self.assertEqual(self.request('/api/intercept/rules/4','DELETE',False,headers)[0],409)
        self.assertFalse(self.writes());self.assertFalse(self.owner.unresolved)
        self.assertFalse(self.bundle.application._unresolved)
        self.assertEqual(self.request()[0],200);self.assertEqual(len(self.writes()),1)
    def test_conflict_and_original_read_delegation(self):
        self.t.original.router.add('PUT','/api/i{mid}/rules/{n}',lambda r,p:None)
        before=tuple(self.t.original.router._routes)
        with self.assertRaises(ValueError):self.bind()
        self.assertEqual(tuple(self.t.original.router._routes),before);self.assertFalse(self.writes())
    def test_owned_session_and_method_required(self):
        self.db._owns_connection=False
        with self.assertRaises(ValueError):self.bind()
        self.db._owns_connection=True;self.owner.mutate=lambda *a:None
        with self.assertRaises(ValueError):self.bind()
