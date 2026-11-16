import io,json,unittest
from dataclasses import replace
from artex.server.wsgi import WSGIApplication
from artex.server.runtime_listener import _AuthenticatedView
from artex.db.conversation_activity import Activity
import test_chat_http as fixture

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.ChatHTTPTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.source=True;self.outer=True;self.requests=[]
        self.f.service.authorize=lambda r,c:self.source
        original=self.f.router.authorize
        from artex.server.core import Router
        self.router=Router(lambda r:self.outer and original(r));self.f.service.register(self.router)
        dispatch=self.router.dispatch
        def capture(request):self.requests.append(request);return dispatch(request)
        self.router.dispatch=capture
        self.app=WSGIApplication(self.router)
        self.f.activities.append(Activity(worker='assistant',kind='text',summary='private marker',detail='private detail'))
    def call(self,query='',path='/api/conversations/7/messages',header=lambda:None,method='GET'):
        status=[];env={'REQUEST_METHOD':method,'PATH_INFO':path,'QUERY_STRING':query,'CONTENT_LENGTH':'0',
            'wsgi.input':io.BytesIO(),'HTTP_AUTHORIZATION':'fixture'}
        def start(s,h):status.append(s);header()
        body=b''.join(_AuthenticatedView(self.app,lambda:None)(env,start))
        return int(status[0].split()[0]),body
    def test_shapes_and_legacy_query_semantics(self):
        for query in ('','since=0','since=0&before=99','before=0&limit=-1'):
            status,body=self.call(query);self.assertEqual(status,200)
            self.assertEqual(set(json.loads(body)),{'items','cursor','running','hasMore'})
        self.assertEqual(json.loads(self.call(path='/api/conversations/7/messages/1')[1]),{'detail':'private detail'})
        self.assertEqual(self.call(method='HEAD'),(200,b''))
    def test_each_data_callback_revocation_denies(self):
        for method,path,query in (('page','/api/conversations/7/messages',''),('list','/api/conversations/7/messages','since=0'),('detail','/api/conversations/7/messages/1','')):
            for grant in ('source','outer'):
                with self.subTest(method=method,grant=grant):
                    original=getattr(self.f.activities,method)
                    def revoke(*a,original=original,grant=grant):
                        result=original(*a);setattr(self,grant,False);return result
                    setattr(self.f.activities,method,revoke)
                    self.assertEqual(self.call(query,path)[0],403)
                    setattr(self.f.activities,method,original);setattr(self,grant,True)
    def test_actual_header_grants_selection_request_and_methods(self):
        for change in ('source','outer','selection','target','headers','method'):
            with self.subTest(change=change):
                conversation=self.f.store.value;page=self.f.activities.page
                def revoke():
                    if change in ('source','outer'):setattr(self,change,False)
                    elif change=='selection':self.f.store.value=replace(conversation,agent_key='changed')
                    elif change=='target':object.__setattr__(self.requests[-1],'target','/api/conversations/8/messages')
                    elif change=='headers':self.requests[-1].headers['Authorization']='different'
                    else:self.f.activities.page=lambda *a:None
                before=len(self.f.activities.queries);status,body=self.call(header=revoke)
                self.assertEqual((status,body),(200,b''));self.assertEqual(len(self.f.activities.queries)-before,1)
                self.source=self.outer=True;self.f.store.value=conversation;self.f.activities.page=page
    def test_running_callback_revocation_and_error_paths(self):
        original=self.f.queue.running
        def running(i):self.source=False;return original(i)
        self.f.queue.running=running
        self.assertEqual(self.call()[0],403)
        self.f.queue.running=original;self.source=True
        for grant in ('source','outer'):
            original=self.f.activities.page
            def fail(*a,grant=grant):setattr(self,grant,False);raise RuntimeError('private exception')
            self.f.activities.page=fail
            status,body=self.call();self.assertEqual(status,403);self.assertNotIn(b'private',body)
            self.f.activities.page=original;setattr(self,grant,True)
    def test_final_metadata_callback_rechecks_grants(self):
        original=self.f.store.get;post=[False];count=[0];target=[None]
        def get(i):
            result=original(i)
            if post[0]:
                count[0]+=1
                if count[0]==target[0]:self.outer=False
            return result
        self.f.store.get=get
        self.assertEqual(self.call(header=lambda:post.__setitem__(0,True))[0],200)
        target[0]=count[0];self.assertGreater(target[0],0);post[0]=False;count[0]=0
        self.assertEqual(self.call(header=lambda:post.__setitem__(0,True)),(200,b''))
    def test_final_source_callback_pins_transport_and_concrete_executors(self):
        self.f.store.executor=object();self.f.activities.executor=object()
        for change in ('router','store_executor','activity_executor'):
            with self.subTest(change=change):
                original_auth=self.router.authorize;store=self.f.store.executor;activity=self.f.activities.executor
                post=[False];count=[0]
                def grant(r,c):
                    if post[0]:
                        count[0]+=1
                        if count[0]==2:
                            if change=='router':self.router.authorize=lambda r:False
                            elif change=='store_executor':self.f.store.executor=object()
                            else:self.f.activities.executor=object()
                    return True
                self.f.service.authorize=grant
                self.assertEqual(self.call(header=lambda:post.__setitem__(0,True)),(200,b''))
                self.router.authorize=original_auth;self.f.store.executor=store;self.f.activities.executor=activity
    def test_final_source_callback_jwt_resource_pin(self):
        import test_chat_setup as setup
        f=setup.ChatSetupTests();f.setUp();self.addCleanup(f.doCleanups)
        self.assertEqual(f.request('/api/conversations/1/messages',{'message':'private marker'})[0],202)
        self.assertTrue(f.db.activity_done.wait(2))
        owner=f.profiles.application.router.authorize.__self__;clock=owner.clock;post=[False];count=[0]
        def grant(r,c):
            if post[0]:
                count[0]+=1
                if count[0]==2:owner.clock=lambda:0
            return True
        f.chat.service.authorize=grant
        env={'REQUEST_METHOD':'GET','PATH_INFO':'/api/conversations/1/messages','QUERY_STRING':'',
            'CONTENT_LENGTH':'0','wsgi.input':io.BytesIO(),'HTTP_AUTHORIZATION':'Bearer '+f.token}
        status=[]
        def start(s,h):status.append(s);post[0]=True
        body=b''.join(_AuthenticatedView(f.profiles.application,lambda:None)(env,start))
        self.assertTrue(status[0].startswith('200'));self.assertEqual(body,b'');owner.clock=clock
    def test_stop_final_source_cannot_replace_transport_before_action(self):
        calls=[];count=[0]
        self.f.queue.cancel=lambda i:calls.append(i) or True
        def grant(r,c):
            count[0]+=1
            if count[0]==3:self.router.authorize=lambda r:False
            return True
        self.f.service.authorize=grant
        response=self.router.dispatch(self.f.request(stop=True))
        self.assertEqual(response.status,403);self.assertEqual(calls,[])
