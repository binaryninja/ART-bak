"""Read-side HTTP contract fixtures; no model calls, threads, or network."""
from dataclasses import replace
from datetime import datetime,timezone
from unittest.mock import Mock
import unittest,json
from types import SimpleNamespace
from artex.db.sidequestions import Parent,Exchange,Model,Snapshot
from artex.server.sidequestions import SideQuestionHTTPService
from artex.server.core import Router,Request
NOW=datetime(2026,11,15,tzinfo=timezone.utc)

class Store:
    def __init__(self,parent):
        self.parent=parent;self.key=parent.key();self.items=[];self.calls=[];self.snap=None;self.curr=None
        self.exchange=Exchange('req-1',1,self.key,1,'client','question','answer','completed','',Model(),NOW,NOW,1)
    def existing(self,client):return None
    def history(self,before,limit):self.calls.append(('history',before,limit));return self.items
    def current(self):return self.curr
    def snapshot(self):return self.snap
    def request(self,key):self.calls.append(('request',key));return self.exchange

class SideHTTPTests(unittest.TestCase):
    def setUp(self):
        self.store=Store(Parent(conversation_id=1));self.resolver=Mock(return_value=self.store)
        self.service=SideQuestionHTTPService(self.resolver,request_resolver=Mock(return_value=self.store))
        self.router=Router(lambda req:req.header('Authorization')=='test');self.service.register(self.router)
    def req(self,path='/api/conversations/1/side-questions',method='GET',auth=True,data=None):
        return self.router.dispatch(Request(method,path,{'Authorization':'test'} if auth else {},json.dumps(data).encode() if data is not None else b''))
    def test_auth_before_resolvers(self):
        for path in ('/api/conversations/1/side-questions','/api/side-questions/req-1/events','/api/side-questions/req-1/cancel'):
            self.assertEqual(self.req(path,auth=False).status,401)
        self.resolver.assert_not_called();self.service.request_resolver.assert_not_called()
    def test_history_page21_cursor20(self):
        self.store.items=[replace(self.store.exchange,ordinal=30-index,id='req-'+str(index)) for index in range(21)]
        data=self.req().json();self.assertEqual(len(data['items']),20);self.assertEqual(data['next_cursor'],11)
        self.assertEqual(self.store.calls,[('history',0,21)])
        self.assertIsNone(data['snapshot']);self.assertIsNone(data['current'])
    def test_snapshot_no_captured_request_leak(self):
        self.store.snap=Snapshot(self.store.parent,1,1,NOW,Model(identity='hash'),{'secret':'prompt'})
        self.assertEqual(self.req().status,503)
        self.service.model_available=lambda model:False
        response=self.req();self.assertEqual(response.status,200);self.assertNotIn(b'secret',response.body)
        self.assertFalse(response.json()['snapshot']['available'])
    def test_parent_routes_exact_scope(self):
        self.store=Store(Parent(task_id=2,exploration_id=20));self.resolver.return_value=self.store
        self.assertEqual(self.req('/api/tasks/2/chat/side-questions').status,200)
        self.assertEqual(self.req('/api/tasks/3/chat/side-questions').status,503)
        self.store=Store(Parent(task_id=2,exploration_id=20,intent_id=7));self.resolver.return_value=self.store
        self.assertEqual(self.req('/api/tasks/2/intents/7/side-questions').status,200)
        self.assertEqual(self.req('/api/tasks/2/intents/8/side-questions').status,503)
    def test_submit_unavailable_never_admits(self):
        self.assertEqual(self.req(method='POST',data={'question':'question','client_request_id':'client'}).status,503);self.assertEqual(self.store.calls,[])
    def test_clear_and_cancel_require_explicit_capability(self):
        self.assertEqual(self.req(method='DELETE').status,503)
        self.service.clear_history=Mock(return_value=True)
        self.assertEqual(self.req(method='DELETE').json(),{'cleared':True})
        path='/api/side-questions/req-1/cancel'
        self.assertEqual(self.req(path,'POST').status,503)
        self.service.cancel_request=Mock(return_value=True)
        self.assertEqual(self.req(path,'POST').json(),{'cancelled':True})
        self.service.cancel_request.assert_called_once_with(self.store,self.store.exchange)
    def test_terminal_sse_exact_event_and_redaction(self):
        self.store.exchange.error='secret traceback'
        response=self.req('/api/side-questions/req-1/events')
        self.assertEqual(response.status,200);body=b''.join(response.body)
        self.assertTrue(body.startswith(b'id: 1\nevent: snapshot\ndata: '));self.assertNotIn(b'secret',body)
        self.assertEqual(body.count(b'event: snapshot'),1)
    def test_live_poll_requires_wait_and_generation_clears(self):
        self.store.exchange.status='running'
        self.assertEqual(self.req('/api/side-questions/req-1/events').status,503)
        def wait(seconds):
            self.assertEqual(seconds,.25);self.store.exchange=replace(self.store.exchange,generation=2);return True
        self.service.wait=wait
        body=b''.join(self.req('/api/side-questions/req-1/events').body)
        self.assertTrue(body.endswith(b'event: cleared\ndata: {}\n\n'))
    def test_terminal_update_and_cancelled_poll(self):
        self.store.exchange.status='running'
        def wait(seconds):self.store.exchange=replace(self.store.exchange,status='completed',sequence=2);return True
        self.service.wait=wait
        body=b''.join(self.req('/api/side-questions/req-1/events').body)
        self.assertEqual(body.count(b'event: snapshot'),2)
        self.store.exchange.status='running';self.service.wait=lambda seconds:False
        self.assertEqual(b''.join(self.req('/api/side-questions/req-1/events').body).count(b'event: snapshot'),1)
    def test_cross_parent_exchange_not_disclosed(self):
        self.store.exchange.session_key='conv-999'
        self.assertEqual(self.req('/api/side-questions/req-1/events').status,503)
    def test_invalid_query_path_and_backend_error(self):
        for path in ('/api/conversations/0/side-questions','/api/conversations/1/side-questions?before=-1','/api/conversations/1/side-questions?task=2'):
            self.assertEqual(self.req(path).status,400)
        self.resolver.side_effect=RuntimeError('secret connection')
        response=self.req();self.assertEqual(response.status,500);self.assertNotIn(b'secret',response.body)

    def submit(self):return self.req(method='POST',data={'question':' question ','client_request_id':'client'})
    def submission_adapters(self):
        self.service.prepare_question=Mock(return_value='prepared context question')
        admission=SimpleNamespace(exchange=replace(self.store.exchange,status='running'),created=True,handle=object())
        self.service.coordinator=Mock();self.service.coordinator.admit.return_value=admission
        self.service.dispatcher=Mock();self.service.dispatcher.coordinator=self.service.coordinator;self.service.dispatcher.dispatch.return_value=True
        return admission
    def test_post_dispatch_owns_admission_before202(self):
        admission=self.submission_adapters();response=self.submit()
        self.assertEqual(response.status,202);self.assertEqual(response.json()['id'],'req-1')
        self.assertEqual(response.json()['question'],'question')
        self.service.coordinator.admit.assert_called_once_with(self.store,'client','question',agent_question='prepared context question')
        self.service.dispatcher.dispatch.assert_called_once_with(admission)
    def test_post_existing_idempotency_no_dispatch(self):
        self.store.existing=lambda client:self.store.exchange
        self.assertEqual(self.submit().status,200)
        self.store.exchange.question='different'
        self.assertEqual(self.submit().status,409)
    def test_post_missing_dispatch_does_not_admit(self):
        self.submission_adapters();self.service.dispatcher=None
        self.assertEqual(self.submit().status,503)
        self.service.coordinator.admit.assert_not_called()
    def test_post_dispatch_rejection_never202(self):
        from artex.sidequestion.runtime import SideUnavailable,SidePersistenceError
        self.submission_adapters();self.service.dispatcher.dispatch.side_effect=SideUnavailable('queue closed')
        self.assertEqual(self.submit().status,503)
        self.service.dispatcher.dispatch.side_effect=SidePersistenceError(self.store.exchange)
        self.assertEqual(self.submit().status,500)
    def test_mismatched_dispatch_coordinator_rejected_before_admit(self):
        self.submission_adapters();self.service.dispatcher.coordinator=object()
        self.assertEqual(self.submit().status,503)
        self.service.coordinator.admit.assert_not_called()

    def test_post_preparation_failure_never_admits(self):
        self.submission_adapters();self.service.prepare_question.side_effect=ValueError('unsupported mention')
        self.assertEqual(self.submit().status,400)
        self.service.coordinator.admit.assert_not_called()

    def test_actual_coordinator_dispatcher_submission_and_original_history(self):
        from artex.sidequestion.runtime import SideQuestionCoordinator,ProviderBinding
        from artex.sidequestion.dispatch import SideQuestionDispatcher
        from test_sidequestion_runtime import Store as RuntimeStore,Provider
        store=RuntimeStore();provider=Provider()
        coordinator=SideQuestionCoordinator(lambda model:ProviderBinding(model,provider))
        dispatcher=SideQuestionDispatcher(coordinator,workers=1,capacity=1)
        captured=[]
        def dispatch(admission):captured.append(admission);return dispatcher.dispatch(admission)
        self.resolver.return_value=store
        self.service.coordinator=coordinator;self.service.dispatcher=SimpleNamespace(dispatch=dispatch,coordinator=coordinator)
        self.service.prepare_question=lambda parent,question:'Prepared mention context: '+question
        try:
            response=self.req('/api/conversations/3/side-questions','POST',data={'question':'original','client_request_id':'client'})
            self.assertEqual(response.status,202);self.assertEqual(response.json()['question'],'original')
            result=dispatcher.receipt(captured[0].handle).result(timeout=2)
            self.assertTrue(result.persisted);self.assertEqual(store.row.status,'completed')
            self.assertEqual(store.row.question,'original')
            self.assertIn('Prepared mention context: original',str(provider.calls[0]))
            again=self.req('/api/conversations/3/side-questions','POST',data={'question':'original','client_request_id':'client'})
            self.assertEqual(again.status,200);self.assertEqual(len(captured),1)
        finally:
            self.assertTrue(dispatcher.close(cancel=True,timeout=2).drained)

    def test_actual_closed_dispatcher_persists_rejection(self):
        from artex.sidequestion.runtime import SideQuestionCoordinator,ProviderBinding
        from artex.sidequestion.dispatch import SideQuestionDispatcher
        from test_sidequestion_runtime import Store as RuntimeStore,Provider
        store=RuntimeStore();provider=Provider()
        coordinator=SideQuestionCoordinator(lambda model:ProviderBinding(model,provider))
        dispatcher=SideQuestionDispatcher(coordinator,workers=1,capacity=1)
        self.assertTrue(dispatcher.close(timeout=2).drained)
        self.resolver.return_value=store
        self.service.coordinator=coordinator;self.service.dispatcher=dispatcher
        self.service.prepare_question=lambda parent,question:question
        response=self.req('/api/conversations/3/side-questions','POST',data={'question':'original','client_request_id':'client'})
        self.assertEqual(response.status,503)
        self.assertNotEqual(store.row.status,'running');self.assertEqual(provider.calls,[])

if __name__=='__main__':unittest.main()
