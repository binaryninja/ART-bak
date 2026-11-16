import io,unittest
from artex.server.core import Router,Request
from artex.server.wsgi import WSGIApplication
from artex.server.runtime_listener import _AuthenticatedView
from artex.server.conversations import ConversationService
from artex.db.conversations import ConversationStore
from test_conversations import Executor

class Tests(unittest.TestCase):
    def setUp(self):
        self.db=Executor();self.allowed=True;self.runs=0;self.requests=[]
        def running(i):self.runs+=1;return True
        self.service=ConversationService(ConversationStore(self.db),running=running)
        self.router=Router(lambda r:self.allowed);self.service.register(self.router)
        dispatch=self.router.dispatch
        def capture(r):self.requests.append(r);return dispatch(r)
        self.router.dispatch=capture;self.app=WSGIApplication(self.router)
    def call(self,header=lambda:None):
        status=[]
        def start(s,h):status.append(s);header()
        env={'REQUEST_METHOD':'GET','PATH_INFO':'/api/conversations','QUERY_STRING':'','CONTENT_LENGTH':'0','wsgi.input':io.BytesIO()}
        body=b''.join(_AuthenticatedView(self.app,lambda:None)(env,start))
        return int(status[0].split()[0]),body
    def test_snapshot_once_and_header_denial(self):
        status,body=self.call();self.assertEqual(status,200);self.assertIn(b'Title',body)
        self.assertEqual((len(self.db.calls),self.runs),(1,1))
        self.assertEqual(self.call(lambda:setattr(self,'allowed',False)),(200,b''))
        self.assertEqual((len(self.db.calls),self.runs),(2,2))
    def test_observers_revoke_and_errors_are_reauthorized(self):
        original=self.db.fetch_all
        def fetch(q):rows=original(q);self.allowed=False;return rows
        self.db.fetch_all=fetch
        self.assertEqual(self.call()[0],403)
        self.allowed=True;self.db.fetch_all=original
        def running(i):self.allowed=False;return True
        self.service.running=running;self.assertEqual(self.call()[0],403)
        self.allowed=True
        def fail(q):self.allowed=False;raise RuntimeError('PRIVATE')
        self.db.fetch_all=fail
        status,body=self.call();self.assertEqual(status,403);self.assertNotIn(b'PRIVATE',body)
    def test_header_pins(self):
        for change in ('target','headers','store','executor','list','running','router'):
            with self.subTest(change=change):
                self.setUp()
                def mutate():
                    if change=='target':object.__setattr__(self.requests[-1],'target','/api/conversations?x=1')
                    elif change=='headers':self.requests[-1].headers['X']='changed'
                    elif change=='store':self.service.store=ConversationStore(self.db)
                    elif change=='executor':self.service.store.executor=Executor()
                    elif change=='list':self.service.store.list=lambda:[]
                    elif change=='running':self.service.running=lambda i:False
                    else:self.router.authorize=lambda r:True
                self.assertEqual(self.call(mutate),(200,b''))
    def test_original_router_is_bound_at_registration(self):
        self.router.authorize=lambda r:True
        self.assertEqual(self.call()[0],403);self.assertEqual(self.db.calls,[])
    def test_no_running_inference(self):
        self.service.running=None;self.assertEqual(self.call()[0],503)
        self.db.rows=[];self.assertEqual(self.call()[0],200)
