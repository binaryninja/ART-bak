"""Real bounded task dispatch with inert providers and temp transcript fixtures."""
from contextlib import contextmanager
from dataclasses import replace
from threading import RLock,Event
import tempfile,json,unittest
from artex.server.core import Request,Router
from artex.server.task_chat import TaskChatService
from artex.task_chat import TaskChatCore,TaskChatScope,scope_identity
from artex.task_chat_dispatch import TaskChatDispatcher
from artex.agent.chat import ChatBinding
from artex.agent.runtime import Tool
from artex.transcripts import TranscriptStore
from artex.chat_transcripts import ChatTranscriptAdapter
from test_chat_runtime import MODEL,ALLOW,Provider,text

class Sink:
    task_id=1
    exploration_id=9
    def __init__(self):self.rows=[];self.fail=False
    def append_many(self,items):
        if self.fail:raise RuntimeError('fixture failure')
        ids=[]
        for item in items:self.rows.append(item);ids.append(len(self.rows))
        return ids

class TaskChatHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.transcript=ChatTranscriptAdapter(TranscriptStore(self.temp.name))
        self.scope=TaskChatScope(1,9,0,3,'revision',MODEL,'Task main interface')
        self.provider=Provider([text('done')]);self.sink=Sink();self.live=True;self.lock=RLock()
        @contextmanager
        def guard(scope):
            with self.lock:yield
        self.core=TaskChatCore(resolve=lambda id,segment:self.scope,current=lambda scope:self.live and scope==self.scope,
            lifecycle_guard=guard,transcript=self.transcript,provider_resolver=lambda scope,model:ChatBinding(model,self.provider),gate=ALLOW)
        def sink(scope):
            self.sink.scope_identity=scope_identity(scope)
            return self.sink
        self.dispatch=TaskChatDispatcher(self.core,sink,single_process=True)
        self.addCleanup(lambda:self.dispatch.close(cancel=True,timeout=2))
        self.service=TaskChatService(resolve_task=lambda id:1 if id in (None,1) else None,
            authorize=lambda req,id:True,dispatcher=self.dispatch)
        self.router=Router(lambda request:request.header('Authorization')=='fixture');self.service.register(self.router)
    def request(self,body=None,query='?task=1'):
        return Request('POST','/api/chat'+query,{'Authorization':'fixture','Idempotency-Key':'client'},
                       json.dumps(body if body is not None else {'message':'hello'}).encode())
    def status(self):return self.router.dispatch(Request('GET','/api/tasks/1/chat/status',{'Authorization':'fixture'}))
    def stop(self):return self.router.dispatch(Request('POST','/api/tasks/1/chat/stop',{'Authorization':'fixture'}))
    def test_actual_async202_task_projection_and_canonical_session(self):
        response=self.router.dispatch(self.request())
        self.assertEqual(response.status,202);self.assertEqual(response.json(),{'status':'accepted','mode':'llm'})
        drained=self.dispatch.close(timeout=2);self.assertTrue(drained.drained)
        self.assertEqual([r.kind for r in self.sink.rows],['user','result'])
        self.assertTrue(all(r.worker=='mainagent' and r.main_seg==0 for r in self.sink.rows))
        self.assertEqual(self.transcript.store.read('exp9-main')[-1]['message']['content'][0]['text'],'done')
        self.assertFalse(self.status().json()['running'])
    def test_selected_task_and_requested_segment(self):
        self.scope=replace(self.scope,segment=2)
        response=self.router.dispatch(self.request({'message':'hello','seg':2},query=''))
        self.assertEqual(response.status,202);self.assertTrue(self.dispatch.close(timeout=2).drained)
        self.assertTrue(all(r.main_seg==2 for r in self.sink.rows))
        self.assertEqual(len(self.transcript.store.read('exp9-main-s2')),2)
    def test_auth_and_unsupported_reject_before_activity(self):
        request=self.request()
        self.assertEqual(self.router.dispatch(replace(request,headers={})).status,401)
        for body in ({'message':'hello','attachments':[{'path':'f'}]}, {'message':'@[Finding#1 x]'},
                     {'message':'hello','seg':True},{'message':'hello','session':'bad'}):
            self.assertIn(self.router.dispatch(self.request(body)).status,(400,503))
        self.assertFalse(self.sink.rows)
    def test_invalid_tool_preflight_before_activity(self):
        self.core.tools=(Tool('invalid','',{},None,lambda _:True),)
        self.assertEqual(self.router.dispatch(self.request()).status,400);self.assertFalse(self.sink.rows)
    def test_deleted_scope_prevents_admission(self):
        self.live=False
        self.assertEqual(self.router.dispatch(self.request()).status,503)
        self.assertFalse(self.sink.rows);self.assertFalse(self.provider.calls)
    def test_stop_inflight_without_resuming_task_workers(self):
        started=Event();release=Event()
        def hook():
            started.set()
            if not release.wait(2):raise RuntimeError('fixture timeout')
        self.provider.hook=hook
        try:
            self.assertEqual(self.router.dispatch(self.request()).status,202);self.assertTrue(started.wait(2))
            self.assertTrue(self.status().json()['running'])
            self.assertEqual(self.stop().json()['status'],'stopping')
        finally:release.set()
        self.assertTrue(self.dispatch.close(timeout=2).drained)
        self.assertTrue(self.sink.rows[-1].is_error);self.assertEqual(self.stop().json()['status'],'idle')
    def test_user_persistence_uncertain_not202_and_retains_busy(self):
        self.sink.fail=True
        response=self.router.dispatch(self.request())
        self.assertEqual(response.status,503);self.assertFalse(response.json()['persisted'])
        self.assertTrue(self.status().json()['running']);self.assertFalse(self.provider.calls)
    def test_fixed_scope_rejected_after_revision_change(self):
        scope=self.dispatch.prepare(1)
        self.scope=replace(scope,revision='new')
        from artex.agent.runtime import RunStopped
        with self.assertRaises(RunStopped):self.dispatch.submit(scope,'client','hello')
        self.assertFalse(self.sink.rows)
    def test_changed_durable_scope_rejected_before_new_user_activity(self):
        scope=self.dispatch.prepare(1)
        receipt=self.dispatch.submit(scope,'client','hello')
        receipt.future.result(timeout=2)
        previous=len(self.sink.rows)
        self.scope=replace(self.scope,run_id=4,revision='different-run')
        from artex.transcripts import TranscriptConflict
        with self.assertRaises(TranscriptConflict):
            self.dispatch.submit(self.dispatch.prepare(1),'client','hello')
        self.assertEqual(len(self.sink.rows),previous)
        self.assertEqual(len(self.provider.calls),1)
    def test_wrong_segment_bound_sink_rejected(self):
        from artex.chat_dispatch import DispatchUnavailable
        self.sink.scope_identity=scope_identity(replace(self.scope,segment=1))
        self.dispatch.activity_store=lambda scope:self.sink
        with self.assertRaises(DispatchUnavailable):self.dispatch.submit(self.dispatch.prepare(1),'client','hello')
        self.assertFalse(self.sink.rows)

    def test_no_task_and_invalid_query(self):
        self.assertEqual(self.router.dispatch(self.request(query='?task=2')).status,404)
        self.assertEqual(self.router.dispatch(self.request(query='?task=1&task=2')).status,400)
        self.assertEqual(self.router.dispatch(self.request(query='?task=1&extra=x')).status,400)

if __name__=='__main__':unittest.main()
