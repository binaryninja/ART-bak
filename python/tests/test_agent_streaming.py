from dataclasses import replace
import unittest
from artex.agent.streaming import StreamingCompletion, StreamLimits, StreamProtocolError
from artex.agent.runtime import RunStopped, Tool
from artex.agent.chat import ChatAgent, ChatBinding
from test_chat_runtime import Transcript, MODEL, ALLOW
from test_process import Context

START={'type':'message_start','usage':{'input_tokens':3,'output_tokens':0}}
STOP={'type':'message_stop'}
def end(reason='end_turn', n=2):
    return {'type':'message_delta','stop_reason':reason,'usage':{'output_tokens':n}}
def text(value): return {'type':'text_delta','text':value}
def tool_events(raw='{"x":1}'):
    return [{'type':'tool_use_start','index':0,'tool_id':'call','tool_name':'fixture'},
            {'type':'tool_input_delta','index':0,'tool_id':'call','text':raw}]

class Streaming:
    def __init__(self,*turns): self.turns=list(turns);self.calls=[];self.closed=0;self.hook=None
    def stream(self,context,request):
        self.calls.append(request)
        try:
            for event in self.turns.pop(0):
                if self.hook:self.hook(event)
                if isinstance(event,Exception):raise event
                yield event
        finally:self.closed+=1

class StreamTests(unittest.TestCase):
    def consume(self,events,**kw):
        self.provider=Streaming(events)
        return StreamingCompletion(self.provider,check=lambda:None,**kw).complete(Context(),{'max_tokens':10})
    def test_text_usage_cumulative(self):
        msg,stop,usage=self.consume([START,text('A'),text('B'),end(n=1),end(n=2),STOP])
        self.assertEqual(msg['content'],[{'type':'text','text':'AB'}])
        self.assertEqual((usage['input_tokens'],usage['output_tokens']),(3,2))
        self.assertEqual(self.provider.closed,1)
    def test_thinking_signature_and_tools(self):
        msg,stop,_=self.consume([START,{'type':'thinking_delta','index':1,'text':'reason'},
            {'type':'thinking_signature','index':1,'text':'signature'},*tool_events(),end('tool_use'),STOP])
        self.assertEqual(msg['content'][0]['signature'],'signature')
        self.assertEqual(msg['content'][1]['input'],{'x':1})
    def test_malformed_tool_never_returns_turn(self):
        for raw in ('{"x":1,"x":2}','[1]','{"x":NaN}','{'):
            with self.assertRaises(ValueError):self.consume([START,*tool_events(raw),end('tool_use'),STOP])
            self.assertEqual(self.provider.closed,1)
    def test_incomplete_and_unknown_events(self):
        for events in ([START,text('partial')],[text('no start')],[START,STOP],
                [START,{'type':'unexpected'},STOP],[START,end('unknown'),STOP],
                [START,*tool_events(),*tool_events(),end('tool_use'),STOP]):
            with self.assertRaises(ValueError):self.consume(events)
            self.assertEqual(self.provider.closed,1)
    def test_refusal_sticky(self):
        _,stop,_=self.consume([START,*tool_events(),end('refusal'),end('tool_use'),STOP])
        self.assertEqual(stop,'refusal')
    def test_stream_limits_and_cleanup(self):
        for limits in (StreamLimits(max_bytes=1),StreamLimits(max_events=1),StreamLimits(max_blocks=1)):
            with self.assertRaises(RunStopped):self.consume([START,text('a'),*tool_events(),end(),STOP],limits=limits)
            self.assertEqual(self.provider.closed,1)
    def test_output_budget(self):
        with self.assertRaises(RunStopped):self.consume([START,end(n=11),STOP])
        self.assertEqual(self.provider.closed,1)
    def test_progress_cancel_closes(self):
        context=Context();provider=Streaming([START,text('x'),end(),STOP])
        def progress(event): context.cancelled=True
        with self.assertRaisesRegex(RuntimeError,'cancelled'):
            StreamingCompletion(provider,check=lambda:None,progress=progress).complete(context,{'max_tokens':10})
        self.assertEqual(provider.closed,1)
    def test_chat_real_stream_path_tool_pair(self):
        tx=Transcript();calls=[]
        provider=Streaming([START,*tool_events(),end('tool_use'),STOP],[START,text('done'),end(),STOP])
        tool=Tool('fixture','inert',{'type':'object'},lambda c,a:calls.append(a) or 'ok',lambda a:True)
        agent=ChatAgent(tx,lambda m:ChatBinding(m,provider),gate=ALLOW,tools=[tool])
        result=agent.run('session','client','hello',model=replace(MODEL,streaming=True))
        self.assertEqual(result.result.reason,'completed');self.assertTrue(result.persisted)
        self.assertEqual(calls,[{'x':1}]);self.assertEqual(provider.closed,2)
        self.assertEqual(provider.calls[1]['messages'][-1]['content'][0]['tool_use_id'],'call')
    def test_partial_tool_failure_does_not_execute_or_retry(self):
        for tail in ([],[RuntimeError('transport')],[end('refusal'),STOP]):
            tx=Transcript();calls=[];provider=Streaming([START,*tool_events(),*tail])
            tool=Tool('fixture','inert',{'type':'object'},lambda c,a:calls.append(a),lambda a:True)
            outcome=ChatAgent(tx,lambda m:ChatBinding(m,provider),gate=ALLOW,tools=[tool]).run('s','c','hello',model=replace(MODEL,streaming=True))
            self.assertFalse(calls);self.assertEqual(len(provider.calls),1);self.assertEqual(provider.closed,1)
            self.assertIn(outcome.result.reason,('model_error','refused'))
    def test_generation_change_during_stream_closes_before_tools(self):
        tx=Transcript();provider=Streaming([START,*tool_events(),end('tool_use'),STOP]);calls=[]
        provider.hook=lambda event: tx.clear() if event['type']=='tool_input_delta' else None
        tool=Tool('fixture','inert',{'type':'object'},lambda c,a:calls.append(a),lambda a:True)
        outcome=ChatAgent(tx,lambda m:ChatBinding(m,provider),gate=ALLOW,tools=[tool]).run('s','c','hello',model=replace(MODEL,streaming=True))
        self.assertFalse(calls);self.assertTrue(outcome.stale);self.assertEqual(provider.closed,1)
    def test_profile_rotation_during_tool_approval_never_executes(self):
        tx=Transcript();provider=Streaming([START,*tool_events(),end('tool_use'),STOP]);live=[True];calls=[]
        def resolver(model):
            return ChatBinding(model if live[0] else replace(model,identity='changed'),provider)
        def gate(context, action):
            if action.kind=='tool':live[0]=False
            return ALLOW(context,action)
        tool=Tool('fixture','inert',{'type':'object'},lambda c,a:calls.append(a),lambda a:True)
        result=ChatAgent(tx,resolver,gate=gate,tools=[tool]).run('s','c','hello',model=replace(MODEL,streaming=True))
        self.assertFalse(calls)
        self.assertNotEqual(result.result.reason,'completed')
        self.assertEqual(len(provider.calls),1)

    def test_profile_change_midstream_stops_before_tool(self):
        tx=Transcript();provider=Streaming([START,*tool_events(),end('tool_use'),STOP]);live=[True];calls=[]
        def resolver(model):
            return ChatBinding(model if live[0] else replace(model,identity='changed'),provider)
        provider.hook=lambda event:live.__setitem__(0,False) if event['type']=='tool_input_delta' else None
        tool=Tool('fixture','inert',{'type':'object'},lambda c,a:calls.append(a),lambda a:True)
        result=ChatAgent(tx,resolver,gate=ALLOW,tools=[tool]).run('s','c','hello',model=replace(MODEL,streaming=True))
        self.assertEqual(result.result.reason,'model_error');self.assertFalse(calls);self.assertEqual(provider.closed,1)

    def test_concrete_anthropic_sse(self):
        from artex.providers import Provider,Config
        from test_providers import FakeTransport,sse
        frames=[{'type':'message_start','message':{'model':'fixture','usage':{'input_tokens':3,'cache_read_input_tokens':2}}},
            {'type':'content_block_start','index':0,'content_block':{'type':'text','text':''}},
            {'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':'hello'}},
            {'type':'message_delta','delta':{'stop_reason':'end_turn'},'usage':{'output_tokens':2}},
            {'type':'message_stop'}]
        transport=FakeTransport([sse(*frames)])
        result=StreamingCompletion(Provider(Config('anthropic','fixture'),transport),check=lambda:None).complete(Context(),{'messages':[{'role':'user','content':[{'type':'text','text':'hi'}]}],'max_tokens':10})
        self.assertEqual(result[0]['content'][0]['text'],'hello');self.assertTrue(transport.closed)
        self.assertEqual(result[2]['input_tokens'],5);self.assertEqual(result[2]['cache_read_tokens'],2)

    def test_concrete_openai_sse(self):
        from artex.providers import Provider,Config
        from test_providers import FakeTransport,sse
        transport=FakeTransport([sse({'choices':[{'delta':{'content':'hello'},'finish_reason':'stop'}],
                                      'usage':{'prompt_tokens':3,'completion_tokens':2}},'[DONE]')])
        provider=Provider(Config('openai','fixture'),transport)
        msg,stop,usage=StreamingCompletion(provider,check=lambda:None).complete(Context(),{'messages':[{'role':'user','content':[{'type':'text','text':'hi'}]}],'max_tokens':10})
        self.assertEqual(msg['content'][0]['text'],'hello');self.assertTrue(transport.closed)
        self.assertTrue(transport.calls[0][-1]);self.assertEqual(usage['input_tokens'],3)


class IntegrationTests(unittest.TestCase):
    def test_task_scope_streaming(self):
        from test_task_chat import TaskChatTests
        fixture=TaskChatTests();fixture.setUp()
        fixture.scope=replace(fixture.scope,model=replace(MODEL,streaming=True))
        fixture.provider=Streaming([START,text('task stream'),end(),STOP])
        result=fixture.core().run(1,'client','hello')
        self.assertTrue(result.persisted);self.assertEqual(result.result.text,'task stream')
        self.assertEqual(fixture.provider.closed,1)

    def test_real_profile_assembly_preserves_streaming(self):
        import tempfile
        from artex.server.chat_setup import build_conversation_runtime
        from artex.server.runtime_setup import build_profile_runtime_application
        from artex.db.profiles import LLMProfile
        from artex.health import Registry
        from test_chat_setup import ChatDB
        from test_application import TestCodec
        from test_providers import FakeTransport,sse
        transport=FakeTransport([sse({'choices':[{'delta':{'content':'assembled stream'},'finish_reason':'stop'}]},'[DONE]')])
        db=ChatDB()
        profiles=build_profile_runtime_application(db,b'x'*32,TestCodec(),transport_factory=lambda config:transport,health=Registry())
        key=profiles.profile_coordinator.save(LLMProfile(name='fixture',model='dummy',api_key='inert-fixture',streaming=True))
        profiles.profile_coordinator.set_active(key)
        with tempfile.TemporaryDirectory() as root:
            chat=build_conversation_runtime(db,profiles,transcript_root=root,authorize=lambda *a:True,gate=ALLOW,
                tools=(),system_prompt=lambda conv:'fixture',single_process=True)
            try:
                resources=chat.service.resolve(None,chat.conversations.get(1))
                self.assertTrue(resources.model.streaming)
                result=ChatAgent(resources.transcript,resources.resolver,gate=resources.gate).run('conv-1','client','hello',model=resources.model)
                self.assertTrue(result.persisted);self.assertEqual(result.result.text,'assembled stream')
                self.assertTrue(transport.calls[0][-1]);self.assertTrue(transport.closed)
            finally:chat.close(cancel=True,timeout=3)

if __name__=='__main__':unittest.main()
