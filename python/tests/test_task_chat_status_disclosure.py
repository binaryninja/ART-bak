import io,json,unittest
from artex.server.core import Router
from artex.server.wsgi import WSGIApplication
from artex.server.runtime_listener import _AuthenticatedView
from artex.server.auth import AuthService,sign_jwt
import test_task_chat_http as fixture

class Tests(unittest.TestCase):
    def make(self):
        f=fixture.TaskChatHTTPTests();f.setUp();self.addCleanup(f.doCleanups)
        f.allowed=True;f.selected=1;f.now=1000.;f.running_calls=0
        f.service.authorize=lambda req,task:f.allowed
        f.service.resolve_task=lambda task:f.selected
        def running(task):f.running_calls+=1;return True
        f.dispatch.running=running
        f.auth=AuthService(None,None,b'k'*32,clock=lambda:f.now)
        f.router=Router(f.auth.authorized);f.service.register(f.router)
        return f
    def call(self,f,headers=lambda:None,wrapped=True):
        env={'REQUEST_METHOD':'GET','PATH_INFO':'/api/tasks/1/chat/status','QUERY_STRING':'',
             'HTTP_AUTHORIZATION':'Bearer '+sign_jwt(b'k'*32,now=1000.),'wsgi.input':io.BytesIO(b'')}
        statuses=[]
        def start(status,values):statuses.append(status);headers()
        app=WSGIApplication(f.router)
        if wrapped:app=_AuthenticatedView(app,lambda:None)
        return statuses,app(env,start)
    def test_success_receipt_does_not_recompute_running(self):
        f=self.make();statuses,body=self.call(f)
        self.assertEqual(json.loads(b''.join(body)),{'running':True})
        self.assertEqual(statuses,['200 OK']);self.assertEqual(f.running_calls,1)
    def test_actual_headers_revoke_task_selection_router_and_jwt(self):
        for kind in ('task','selection','router','jwt','binding'):
            with self.subTest(kind=kind):
                f=self.make()
                def revoke():
                    if kind=='task':f.allowed=False
                    elif kind=='selection':f.selected=2
                    elif kind=='router':f.router.authorize=lambda req:True
                    elif kind=='binding':f.service.dispatcher=object()
                    else:f.now=10**10
                statuses,body=self.call(f,revoke)
                self.assertEqual(b''.join(body),b'');self.assertEqual(statuses,['200 OK'])
                self.assertEqual(f.running_calls,1)
    def test_plain_wsgi_receipt_rejects_after_headers_without_private_error(self):
        f=self.make();statuses,body=self.call(f,lambda:setattr(f,'allowed',False),wrapped=False)
        with self.assertRaisesRegex(RuntimeError,'^Disclosure unavailable$'):next(iter(body))
        body.close();self.assertEqual(statuses,['200 OK'])
    def test_last_selection_observer_revokes_task_or_jwt(self):
        for kind in ('task','jwt'):
            with self.subTest(kind=kind):
                f=self.make();post=[False]
                def resolve(task):
                    if post[0]:
                        if kind=='task':f.allowed=False
                        else:f.now=10**10
                    return 1
                f.service.resolve_task=resolve
                statuses,body=self.call(f,lambda:post.__setitem__(0,True))
                self.assertEqual(b''.join(body),b'');self.assertEqual(statuses,['200 OK'])
