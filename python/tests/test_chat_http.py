"""Frontend-shaped async HTTP with deterministic queue and inert model callbacks."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from copy import deepcopy
import json
import unittest
from artex.server.chat import ChatService,ChatResources
from artex.server.core import Request,Router
from artex.db.conversations import Conversation
from artex.db.conversation_activity import Activity
from artex.agent.chat import ChatBinding,ChatBusy
from artex.agent.runtime import Grant,RunStopped
from artex.transcripts import TranscriptStore
from artex.chat_transcripts import ChatTranscriptAdapter
from test_chat_runtime import MODEL,Provider,text,Transcript

class Conversations:
    def __init__(self):self.value=Conversation(7,'assistant','title',None,None,None,None)
    def get(self,id):return self.value if self.value and self.value.id==id else None

class Activities:
    conversation_id=7
    def __init__(self):self.rows=[];self.queries=[]
    def append(self,item):
        item=deepcopy(item);item.id=len(self.rows)+1;self.rows.append(item);return item.id
    def append_many_and_touch(self,items,title_if_default=None):
        return [self.append(item) for item in items]
    def list(self,since_id=0,limit=500):
        self.queries.append(('list',since_id,limit));items=[x for x in self.rows if x.id>since_id][:limit]
        return items,items[-1].id if items else since_id
    def page(self,before=0,limit=200):
        self.queries.append(('page',before,limit));items=[x for x in self.rows if not before or x.id<before]
        return items[-limit:],len(items)>limit
    def detail(self,id):return next((x.detail for x in self.rows if x.id==id),'')

class Token:
    cancelled=False
    def check(self):
        if self.cancelled:raise RunStopped('cancelled')

class Queue:
    def __init__(self):self.jobs={};self.receipts={};self.results=[]
    def running(self,id):return id in self.jobs
    def cancel(self,id):
        if id not in self.jobs:return False
        self.jobs[id][0].cancelled=True;return True
    def submit(self,id,client,message,*,activities,run,worker):
        key=(id,client)
        if key in self.receipts:
            if self.receipts[key]!=message:raise ChatBusy()
            return
        if id in self.jobs:raise ChatBusy()
        self.receipts[key]=message;self.jobs[id]=(Token(),run,activities,worker)
        activities.append(Activity(worker=worker,kind='user',summary=message[:200],detail=message))
    def drain(self):
        for id,(token,run,activities,worker) in list(self.jobs.items()):
            try:
                outcome=run(token);self.results.append(outcome)
                activities.append(Activity(worker=worker,kind='result',summary=outcome.result.text,detail=outcome.result.text))
            except Exception as exc:
                self.results.append(exc);activities.append(Activity(worker=worker,kind='result',is_error=True,summary='Run failed'))
            finally:self.jobs.pop(id)

class ChatHTTPTests(unittest.TestCase):
    def setUp(self):
        self.store=Conversations();self.tx=Transcript();self.provider=Provider([text('answer')])
        self.activities=Activities();self.queue=Queue()
        self.resources=ChatResources(self.tx,MODEL,lambda m:ChatBinding(m,self.provider),
                                    lambda *a:Grant(True,True,True),lambda session,turn:self.tx.commits[-1])
        self.service=ChatService(self.store,lambda request,conv:self.resources,
                                 dispatcher=self.queue,activities=lambda id:self.activities,authorize_conversation=lambda request,conv:True)
        self.router=Router(lambda request:request.header('Authorization')=='fixture');self.service.register(self.router)
    def request(self,body=None,*,id='7',client='key',stop=False,auth=True,method='POST',query=''):
        path='/api/conversations/'+id+('/stop' if stop else '/messages')+query
        return Request(method,path,{'Authorization':'fixture' if auth else '', 'Idempotency-Key':client},
                       json.dumps(body if body is not None else {'message':'hello'}).encode())
    def test_task_service_composes_without_placeholder_collision(self):
        from types import SimpleNamespace
        from artex.server.task_chat import TaskChatService
        request=Request('GET','/api/tasks/1/chat/status',{'Authorization':'fixture'})
        self.assertEqual(self.router.dispatch(request).status,404)
        dispatcher=SimpleNamespace(prepare=lambda *a,**kw:None,submit=lambda *a:None,
                                   running=lambda task:False,cancel=lambda task:False)
        TaskChatService(resolve_task=lambda task:1,authorize=lambda req,task:True,
                        dispatcher=dispatcher).register(self.router)
        self.assertEqual(self.router.dispatch(request).json(),{'running':False})
        self.assertEqual(self.router.dispatch(replace(request,headers={})).status,401)

    def test_202_registers_busy_and_user_before_model_execution(self):
        self.assertEqual(self.router.dispatch(self.request(auth=False)).status,401)
        response=self.router.dispatch(self.request());self.assertEqual(response.status,202)
        self.assertEqual(response.json(),{'status':'accepted'});self.assertTrue(self.service.running(7))
        self.assertEqual(self.activities.rows[0].kind,'user');self.assertFalse(self.provider.calls)
        self.queue.drain();self.assertIn('conv-7',self.tx.history);self.assertFalse(self.service.running(7))
        self.assertEqual(self.activities.rows[-1].summary,'answer')
    def test_frontend_no_idempotency_header_and_polling(self):
        self.assertEqual(self.router.dispatch(self.request(client='')).status,202)
        result=self.router.dispatch(self.request(method='GET',query='?since=0')).json()
        self.assertTrue(result['running']);self.assertFalse(result['hasMore']);self.assertEqual(result['cursor'],1)
        self.assertEqual(self.activities.queries[-1],('list',0,1000))
        self.queue.drain()
        result=self.router.dispatch(self.request(method='GET',query='?since=1')).json()
        self.assertFalse(result['running']);self.assertEqual(result['items'][0]['kind'],'result')
    def test_history_and_detail_contract(self):
        for i in range(4):self.activities.append(Activity(kind='text',summary=str(i),detail='detail'+str(i)))
        result=self.router.dispatch(self.request(method='GET',query='?before=4&limit=2')).json()
        self.assertEqual([x['seq'] for x in result['items']],[2,3]);self.assertTrue(result['hasMore'])
        self.assertEqual(result['cursor'],3);self.assertNotIn('detail',result['items'][0])
        req=Request('GET','/api/conversations/7/messages/2',{'Authorization':'fixture'})
        self.assertEqual(self.router.dispatch(req).json(),{'detail':'detail1'})
    def test_task_route_does_not_misroute(self):
        response=self.router.dispatch(Request('POST','/api/chat',{'Authorization':'fixture'},b'{"message":"hello"}'))
        self.assertEqual(response.status,404);self.assertFalse(self.provider.calls)
    def test_unsupported_inputs_fail_before_admission(self):
        for body in ({'message':'hello','attachments':[{'path':'file'}]}, {'message':'@[Finding#3 name]'},
                     {'message':'hello','seg':2},{'message':'hello','session_id':'other'}):
            self.assertIn(self.router.dispatch(self.request(body)).status,(400,503))
        self.assertFalse(self.activities.rows);self.assertFalse(self.queue.jobs)
    def test_generation_change_before_worker_prevents_model(self):
        self.assertEqual(self.router.dispatch(self.request()).status,202)
        self.store.value=replace(self.store.value,agent_key='different');self.queue.drain()
        self.assertFalse(self.provider.calls);self.assertTrue(self.activities.rows[-1].is_error)
    def test_stop_before_worker_starts(self):
        self.router.dispatch(self.request())
        self.assertEqual(self.router.dispatch(self.request(stop=True)).json()['status'],'stopping')
        self.queue.drain();self.assertFalse(self.provider.calls)
        self.assertEqual(self.router.dispatch(self.request(stop=True)).json()['status'],'idle')
    def test_busy_and_receipt_dedup(self):
        self.router.dispatch(self.request());self.assertEqual(self.router.dispatch(self.request()).status,202)
        self.assertEqual(len(self.activities.rows),1)
        self.assertEqual(self.router.dispatch(self.request(client='other')).status,409)
        self.queue.drain();self.router.dispatch(self.request());self.assertEqual(len(self.provider.calls),1)
    def test_invalid_queries_and_body(self):
        for query in ('?limit=10001','?since=1&since=2','?unknown=1','?before=bad'):
            self.assertEqual(self.router.dispatch(self.request(method='GET',query=query)).status,400)
        request=self.request()
        self.assertEqual(self.router.dispatch(replace(request,body=b'{"message":"a","message":"b"}')).status,400)
        self.assertEqual(self.router.dispatch(replace(request,body=b'x'*1048577)).status,413)
    def test_invalid_runtime_preflight_no_user_activity(self):
        from artex.agent.runtime import Tool
        original=self.resources
        for resources in (replace(original,tools=(Tool('bad','',{},None,lambda _:True),)),
                          replace(original,limits=None)):
            self.resources=resources
            self.assertEqual(self.router.dispatch(self.request()).status,400)
        self.resources=replace(original,model=replace(MODEL,streaming=True))
        self.assertEqual(self.router.dispatch(self.request()).status,503)  # provider has no stream method
        self.assertFalse(self.activities.rows);self.assertFalse(self.queue.jobs)
    def test_offline_history_and_stop_need_no_provider(self):
        self.service.resolve=lambda *args:None
        self.assertEqual(self.router.dispatch(self.request(method='GET')).status,200)
        self.assertEqual(self.router.dispatch(self.request(stop=True)).status,200)
        self.assertEqual(self.router.dispatch(self.request()).status,503)
    def test_real_dispatch_error_statuses(self):
        from artex.chat_dispatch import DispatchConflict,DispatchUnavailable,DispatchPersistenceError
        for error,status in ((DispatchConflict(),409),(DispatchUnavailable(),503),(DispatchPersistenceError(),503)):
            def submit(*args,**kwargs):raise error
            self.queue.submit=submit
            response=self.router.dispatch(self.request())
            self.assertEqual(response.status,status)

    def test_actual_local_transcript_commit_and_durable_replay(self):
        with TemporaryDirectory() as root:
            storage=TranscriptStore(Path(root));adapter=ChatTranscriptAdapter(storage)
            self.resources=replace(self.resources,transcript=adapter,durable_result=adapter.finalized_result)
            self.assertEqual(self.router.dispatch(self.request()).status,202);self.queue.drain()
            self.assertEqual(storage.read('conv-7')[-1]['message']['content'][0]['text'],'answer')
            # New dispatcher receipt ledger simulates process restart; transcript
            # prevents model replay and durable loader supplies actual reply.
            self.queue.receipts.clear()
            self.router.dispatch(self.request());self.queue.drain()
            self.assertEqual(len(self.provider.calls),1);self.assertEqual(self.activities.rows[-1].summary,'answer')
    def test_real_dispatcher_and_real_transcript_frontend_flow(self):
        from artex.chat_dispatch import ChatDispatcher
        from threading import Event
        started=Event();release=Event()
        def hook():
            started.set()
            if not release.wait(2):raise RuntimeError('fixture release timeout')
        self.provider.hook=hook
        dispatcher=ChatDispatcher(workers=1,capacity=1,single_process=True)
        try:
            with TemporaryDirectory() as root:
                adapter=ChatTranscriptAdapter(TranscriptStore(Path(root)))
                self.resources=replace(self.resources,transcript=adapter,durable_result=adapter.finalized_result)
                self.service.dispatcher=dispatcher
                response=self.router.dispatch(self.request())
                self.assertEqual(response.status,202);self.assertTrue(started.wait(2))
                poll=self.router.dispatch(self.request(method='GET',query='?since=0')).json()
                self.assertTrue(poll['running']);self.assertEqual(poll['items'][0]['seq'],1)
                self.assertIn('ts',poll['items'][0]);self.assertNotIn('created_at',poll['items'][0])
                self.assertEqual(self.router.dispatch(self.request(stop=True)).json()['status'],'stopping')
                release.set()
                drained=dispatcher.close(timeout=2)
                self.assertTrue(drained.drained)
                poll=self.router.dispatch(self.request(method='GET',query='?since=1')).json()
                self.assertFalse(poll['running']);self.assertEqual(poll['items'][-1]['kind'],'result')
                self.assertEqual(adapter.store.read('conv-7')[-1]['message']['content'][0]['text'],'answer')
        finally:
            release.set();dispatcher.close(cancel=True,timeout=2)

    def test_persistence_failure_is_async_error_not_completion(self):
        self.tx.fail=True;self.router.dispatch(self.request());self.queue.drain()
        self.assertTrue(self.activities.rows[-1].is_error);self.assertFalse(self.service.running(7))

if __name__=='__main__':unittest.main()
