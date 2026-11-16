import unittest
from dataclasses import replace
from threading import Event
from artex.server.core import Router
from test_chat_setup import ChatSetupTests

class Tests(unittest.TestCase):
    def fixture(self):
        f=ChatSetupTests();f.setUp();self.addCleanup(f.doCleanups)
        outer=[True];original=f.profiles.application.router.authorize
        router=Router(lambda r:outer[0] and original(r));f.chat.service.register(router)
        f.profiles.application.router=router
        return f,outer
    def test_three_pre_admission_callbacks_revoke_original_router(self):
        for seam in ('resources','activities','provider'):
            with self.subTest(seam=seam):
                f,outer=self.fixture();svc=f.chat.service
                if seam=='activities':
                    original=svc.activities
                    def factory(i,original=original,outer=outer):
                        value=original(i);outer[0]=False;return value
                    svc.activities=factory
                else:
                    original=svc.resolve
                    def resolve(req,c,original=original,outer=outer,seam=seam):
                        value=original(req,c)
                        if seam=='resources':outer[0]=False;return value
                        resolver=value.resolver
                        def binding(m):
                            result=resolver(m);outer[0]=False;return result
                        return replace(value,resolver=binding)
                    svc.resolve=resolve
                self.assertEqual(f.request('/api/conversations/1/messages',{'message':'hello'})[0],403)
                self.assertEqual(f.calls,[]);self.assertEqual(f.db.activities,[])
                self.assertEqual(f.chat.transcript.store.read('conv-1'),[])
    def test_committed_user_revocation_retains_uncertainty(self):
        f,outer=self.fixture();original=f.chat.service.activities
        def factory(i):
            store=original(i);append=store.append_many_and_touch
            def write(items,**kwargs):
                result=append(items,**kwargs)
                if items[0].kind=='user':outer[0]=False
                return result
            store.append_many_and_touch=write;return store
        f.chat.service.activities=factory
        self.assertEqual(f.request('/api/conversations/1/messages',{'message':'hello'})[0],202)
        self.assertTrue(f.db.activity_done.wait(2));self.assertEqual(f.calls,[])
        self.assertEqual(f.db.activities[0][2],'user');self.assertTrue(f.chat.dispatcher.running(1))
        self.assertFalse(f.chat.close(timeout=1).drained)
    def test_committed_user_acknowledgement_error_not_rollback(self):
        f,outer=self.fixture();original=f.chat.service.activities
        def factory(i):
            store=original(i);append=store.append_many_and_touch
            def write(items,**kwargs):
                append(items,**kwargs);raise RuntimeError('private acknowledgement')
            store.append_many_and_touch=write;return store
        f.chat.service.activities=factory
        status,body=f.request('/api/conversations/1/messages',{'message':'hello'})
        self.assertEqual(status,503);self.assertNotIn('private',str(body))
        self.assertEqual([a[2] for a in f.db.activities],['user']);self.assertEqual(f.calls,[])
        self.assertFalse(f.chat.close(timeout=1).drained)
    def test_transcript_begin_revocation_prevents_provider_after_partial_write(self):
        f,outer=self.fixture();original=f.chat.transcript.begin
        def begin(*a):result=original(*a);outer[0]=False;return result
        f.chat.transcript.begin=begin
        self.assertEqual(f.request('/api/conversations/1/messages',{'message':'hello'})[0],202)
        self.assertTrue(f.db.activity_done.wait(2));self.assertEqual(f.calls,[])
        self.assertEqual(len(f.chat.transcript.store.read('conv-1')),1)
        self.assertFalse(f.chat.close(timeout=1).drained)
    def test_stop_rechecks_router_after_selection_without_cancellation(self):
        f,outer=self.fixture();entered=Event();release=Event();self.addCleanup(release.set)
        f.hook=lambda c:(entered.set(),release.wait(3))
        self.assertEqual(f.request('/api/conversations/1/messages',{'message':'hello'})[0],202)
        self.assertTrue(entered.wait(1));token=f.chat.dispatcher._busy[1].token
        original=f.chat.conversations.get
        def get(i):result=original(i);outer[0]=False;return result
        f.chat.conversations.get=get
        self.assertEqual(f.request('/api/conversations/1/stop',{})[0],403)
        self.assertFalse(token.cancelled)
        f.chat.conversations.get=original;outer[0]=True;release.set()
        self.assertTrue(f.db.activity_done.wait(2))
    def test_request_and_authority_replacement_denied_before_user_write(self):
        for change in ('request','router','source'):
            with self.subTest(change=change):
                f,outer=self.fixture();service=f.chat.service;original=service.resolve
                def resolve(req,c,original=original,change=change,f=f):
                    result=original(req,c)
                    if change=='request':object.__setattr__(req,'target','/api/conversations/2/messages')
                    elif change=='router':f.profiles.application.router.authorize=lambda r:True
                    else:f.allowed=False
                    return result
                service.resolve=resolve
                status,_=f.request('/api/conversations/1/messages',{'message':'hello'})
                self.assertIn(status,(403,503));self.assertEqual(f.db.activities,[]);self.assertEqual(f.calls,[])
    def test_transcript_commit_revocation_preserves_completed_durable_effect(self):
        f,outer=self.fixture();original=f.chat.transcript.commit
        def commit(*a):result=original(*a);outer[0]=False;return result
        f.chat.transcript.commit=commit
        self.assertEqual(f.request('/api/conversations/1/messages',{'message':'hello'})[0],202)
        self.assertTrue(f.db.activity_done.wait(2));self.assertEqual(len(f.calls),1)
        self.assertEqual(len(f.chat.transcript.store.read('conv-1')),2)
        self.assertFalse(f.chat.close(timeout=1).drained)

    def test_final_runtime_clock_revocation_prevents_tool_callback(self):
        from test_chat_http import ChatHTTPTests
        from test_chat_runtime import Provider,use,text
        from artex.agent.runtime import Tool,Grant
        from artex.chat_dispatch import ChatDispatcher
        f=ChatHTTPTests();f.setUp();self.addCleanup(f.doCleanups)
        queue=ChatDispatcher(single_process=True);self.addCleanup(lambda:queue.close(cancel=True,timeout=2))
        f.service.dispatcher=queue;outer=[True];armed=[False];effects=[]
        router=Router(lambda r:outer[0]);f.service.register(router)
        provider=Provider([use(),text('answer')])
        from artex.agent.chat import ChatBinding
        def gate(c,a):
            if a.kind=='tool':armed[0]=True
            return Grant(True,True,True)
        def clock():
            if armed[0]:outer[0]=False
            return 0
        f.resources=replace(f.resources,resolver=lambda m:ChatBinding(m,provider),gate=gate,clock=clock,
            tools=(Tool('fixture','fixture',{},lambda *a:effects.append('executed') or 'ok',lambda a:True),))
        self.assertEqual(router.dispatch(f.request()).status,202)
        self.assertFalse(queue.close(timeout=2).drained)
        self.assertEqual(effects,[]);self.assertEqual(len(provider.calls),1);self.assertEqual(f.tx.commits,[])
    def test_tool_callback_effect_then_revocation_is_retained(self):
        from test_chat_http import ChatHTTPTests
        from test_chat_runtime import Provider,use,text
        from artex.agent.runtime import Tool
        from artex.agent.chat import ChatBinding
        from artex.chat_dispatch import ChatDispatcher
        f=ChatHTTPTests();f.setUp();self.addCleanup(f.doCleanups)
        queue=ChatDispatcher(single_process=True);self.addCleanup(lambda:queue.close(cancel=True,timeout=2))
        f.service.dispatcher=queue;outer=[True];effects=[]
        router=Router(lambda r:outer[0]);f.service.register(router)
        def tool(*a):effects.append('executed');outer[0]=False;return 'ok'
        provider=Provider([use(),text('answer')])
        f.resources=replace(f.resources,resolver=lambda m:ChatBinding(m,provider),tools=(Tool('fixture','fixture',{},tool,lambda a:True),))
        self.assertEqual(router.dispatch(f.request()).status,202)
        self.assertFalse(queue.close(timeout=2).drained)
        self.assertEqual(effects,['executed']);self.assertEqual(f.tx.commits,[])
    def stream_probe(self,threshold=None,revoke_next=False):
        from test_chat_http import ChatHTTPTests
        from test_agent_streaming import START,STOP,end,text
        from artex.agent.chat import ChatBinding
        from artex.chat_dispatch import ChatDispatcher
        f=ChatHTTPTests();f.setUp();self.addCleanup(f.doCleanups)
        queue=ChatDispatcher(single_process=True);self.addCleanup(lambda:queue.close(cancel=True,timeout=2))
        f.service.dispatcher=queue;outer=[True];count=[0];events=[];closed=[]
        def clock():
            count[0]+=1
            if count[0]==threshold:outer[0]=False
            return 0
        class Iterator:
            def __init__(self):self.events=iter([START,text('answer'),end(),STOP])
            def __iter__(self):return self
            def __next__(self):
                events.append(('next',count[0]))
                if revoke_next:outer[0]=False
                return next(self.events)
            def close(self):closed.append(True);outer[0]=True
        class Provider:
            def stream(self,*a):events.append(('entry',count[0]));return Iterator()
        provider=Provider()
        f.resources=replace(f.resources,model=replace(f.resources.model,streaming=True),resolver=lambda m:ChatBinding(m,provider),clock=clock)
        router=Router(lambda r:outer[0]);f.service.register(router)
        self.assertEqual(router.dispatch(f.request()).status,202)
        drain=queue.close(timeout=2)
        return events,closed,drain,f.tx
    def test_stream_entry_and_each_advance_fenced_after_final_clock(self):
        events,closed,drain,tx=self.stream_probe()
        self.assertTrue(drain.drained);self.assertTrue(tx.commits)
        entry=events[0][1];second_next=[n for kind,n in events if kind=='next'][1]
        effects,closed,drain,tx=self.stream_probe(entry)
        self.assertEqual(effects,[]);self.assertEqual(closed,[]);self.assertFalse(drain.drained);self.assertEqual(tx.commits,[])
        effects,closed,drain,tx=self.stream_probe(second_next)
        self.assertEqual(len([e for e in effects if e[0]=='next']),1)
        self.assertEqual(closed,[True]);self.assertFalse(drain.drained);self.assertEqual(tx.commits,[])
    def test_stream_effect_revocation_sticky_even_cleanup_restores_grant(self):
        events,closed,drain,tx=self.stream_probe(revoke_next=True)
        self.assertEqual([e[0] for e in events],['entry','next']);self.assertEqual(closed,[True])
        self.assertFalse(drain.drained);self.assertEqual(tx.commits,[])
    def test_nonstream_effect_revocation_stays_uncertain(self):
        from test_chat_http import ChatHTTPTests
        from test_chat_runtime import Provider,text
        from artex.agent.chat import ChatBinding
        from artex.chat_dispatch import ChatDispatcher
        f=ChatHTTPTests();f.setUp();self.addCleanup(f.doCleanups)
        queue=ChatDispatcher(single_process=True);self.addCleanup(lambda:queue.close(cancel=True,timeout=2))
        f.service.dispatcher=queue;outer=[True];effect=[False]
        provider=Provider([text('answer')])
        def hook():effect[0]=True;outer[0]=False
        provider.hook=hook
        def clock():
            if effect[0]:outer[0]=True
            return 0
        f.resources=replace(f.resources,resolver=lambda m:ChatBinding(m,provider),clock=clock)
        router=Router(lambda r:outer[0]);f.service.register(router)
        self.assertEqual(router.dispatch(f.request()).status,202)
        self.assertFalse(queue.close(timeout=2).drained)
        self.assertEqual(len(provider.calls),1);self.assertEqual(f.tx.commits,[])

    def test_every_stream_clock_authority_loss_stays_uncertain(self):
        events,closed,drain,tx=self.stream_probe()
        self.assertTrue(drain.drained)
        for tick in range(events[0][1],events[-1][1]+1):
            with self.subTest(tick=tick):
                effects,closed,drain,tx=self.stream_probe(tick)
                self.assertFalse(drain.drained);self.assertEqual(tx.commits,[])
                if effects:self.assertEqual(closed,[True])
