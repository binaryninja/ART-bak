"""Inert end-to-end chat: no model, tools with effects, filesystem or DB."""
from copy import deepcopy
from dataclasses import replace
import unittest
from artex.agent.chat import ChatAgent,ChatBinding,TranscriptTurn,ChatBusy,ChatPersistenceError,ChatUnavailable
from artex.agent.runtime import Grant,Tool,RunStopped
from artex.sidequestion import Model

MODEL=Model(model='dummy',identity='config-v1',streaming=False)
ALLOW=lambda context,action:Grant(True,True,True)


class Transcript:
    def __init__(self):self.history={};self.turns={};self.rev=0;self.commits=[];self.fail=False
    def begin(self,session,client,user):
        key=(session,client)
        if key in self.turns:
            old=self.turns[key]
            if old.messages[-1]!=user:raise ValueError('Changed request')
            return replace(old,created=False)
        history=deepcopy(self.history.get(session,[]))+[deepcopy(user)]
        turn=TranscriptTurn(client,self.rev,len(self.turns)+1,history)
        self.turns[key]=turn;self.history[session]=history
        return deepcopy(turn)
    def current(self,session,turn):return turn.revision==self.rev
    def commit(self,session,turn,result):
        if self.fail:raise RuntimeError('secret storage error')
        if not self.current(session,turn):return False
        self.history[session]=deepcopy(result.messages);self.commits.append(deepcopy(result))
        self.turns[(session,turn.id)]=replace(turn,finalized=True)
        return True
    def clear(self):self.rev+=1;self.history={};self.turns={}


class Provider:
    def __init__(self,responses):self.responses=list(responses);self.calls=[];self.hook=None
    def complete(self,context,request):
        self.calls.append(deepcopy(request))
        if self.hook:self.hook()
        value=self.responses.pop(0)
        if isinstance(value,Exception):raise value
        return value


def text(value):return {'role':'assistant','content':[{'type':'text','text':value}]},'end_turn',{'input_tokens':2,'output_tokens':1}
def use():return {'role':'assistant','content':[{'type':'tool_use','id':'call1','name':'fixture','input':{'x':1}}]},'tool_use',{'input_tokens':2,'output_tokens':1}


class ChatTests(unittest.TestCase):
    def make(self,responses,**kwargs):
        self.tx=Transcript();self.provider=Provider(responses)
        self.agent=ChatAgent(self.tx,lambda m:ChatBinding(m,self.provider),gate=kwargs.pop('gate',ALLOW),**kwargs)
        return self.agent

    def test_external_queued_cancellation_prevents_admission(self):
        class Token:cancelled=True
        agent=self.make([text('unused')])
        with self.assertRaises(RunStopped):agent.run('conversation','client','hello',model=MODEL,token=Token())
        self.assertEqual(self.tx.history,{})
        self.assertEqual(self.provider.calls,[])

    def test_external_cancellation_during_provider_retains_usage(self):
        class Token:cancelled=False
        token=Token();agent=self.make([text('partial')])
        self.provider.hook=lambda:setattr(token,'cancelled',True)
        outcome=agent.run('conversation','client','hello',model=MODEL,token=token)
        self.assertEqual(outcome.result.reason,'cancelled')
        self.assertEqual(outcome.result.usage['output_tokens'],1)
        self.assertTrue(outcome.persisted)

    def test_invalid_external_token_before_admission(self):
        agent=self.make([])
        with self.assertRaises(ValueError):agent.run('conversation','client','hello',model=MODEL,token=object())
        self.assertEqual(self.tx.history,{})

    def test_two_turn_durable_history_and_replay_idempotence(self):
        agent=self.make([text('first'),text('second')])
        first=agent.run('conversation','client1','hello',model=MODEL)
        second=agent.run('conversation','client2','followup',model=MODEL)
        self.assertTrue(first.persisted);self.assertEqual(second.result.text,'second')
        self.assertEqual(len(self.provider.calls[1]['messages']),3)
        self.assertEqual(len(self.tx.history['conversation']),4)
        reused=agent.run('conversation','client2','followup',model=MODEL)
        self.assertTrue(reused.reused);self.assertTrue(reused.persisted);self.assertIsNone(reused.result);self.assertEqual(len(self.provider.calls),2)

    def test_tool_pairs_committed_atomically_and_all_boundaries_gated(self):
        calls=[];gates=[];captures=[]
        tool=Tool('fixture','inert',{'type':'object'},lambda ctx,args:calls.append(args) or 'fixture-result',lambda args:args=={'x':1})
        agent=self.make([use(),text('done')],tools=[tool],gate=lambda ctx,action:gates.append(action.kind) or Grant(True,True,True),capture=captures.append)
        result=agent.run('conversation','client','hello',model=MODEL)
        self.assertEqual(gates,['model','tool','model']);self.assertEqual(calls,[{'x':1}])
        self.assertEqual(len(self.tx.commits),1);self.assertEqual(len(result.result.messages),4)
        self.assertEqual(result.result.messages[2]['content'][0]['tool_use_id'],'call1')
        self.assertEqual([c.version for c in captures],[1,2]);self.assertEqual(captures[0].model.identity,'config-v1')
        self.assertEqual(captures[1].request,self.provider.calls[1])

    def test_denied_gate_no_provider_and_durable_stop(self):
        agent=self.make([],gate=lambda *a:Grant())
        result=agent.run('conversation','client','hello',model=MODEL)
        self.assertEqual(result.result.reason,'permission_denied');self.assertTrue(result.persisted)
        self.assertEqual(self.provider.calls,[])

    def test_cancel_during_provider_retains_answer_usage(self):
        agent=self.make([text('partial')]);self.provider.hook=lambda:agent.cancel('conversation')
        result=agent.run('conversation','client','hello',model=MODEL)
        self.assertEqual(result.result.reason,'cancelled');self.assertEqual(result.result.text,'partial')
        self.assertEqual(result.result.usage['output_tokens'],1);self.assertTrue(result.persisted)
        self.assertFalse(agent.cancel('conversation'))

    def test_cancel_tool_turn_preserves_unexecuted_pair(self):
        tool=Tool('fixture','inert',{},lambda *a:self.fail('must not run'),lambda _:True)
        agent=self.make([use()],tools=[tool]);self.provider.hook=lambda:agent.cancel('conversation')
        result=agent.run('conversation','client','hello',model=MODEL)
        self.assertEqual(result.result.reason,'cancelled')
        self.assertEqual(result.result.messages[-1]['content'][0]['tool_use_id'],'call1')
        self.assertTrue(result.result.messages[-1]['content'][0]['is_error'])

    def test_clear_during_approval_prevents_provider_and_resurrection(self):
        agent=self.make([],gate=lambda *a:self.tx.clear() or Grant(True,True,True))
        result=agent.run('conversation','client','hello',model=MODEL)
        self.assertFalse(result.persisted);self.assertTrue(result.stale);self.assertEqual(self.tx.history,{})
        self.assertEqual(self.provider.calls,[])

    def test_persistence_failure_exposes_no_false_success_and_releases_slot(self):
        agent=self.make([text('first'),text('second')]);self.tx.fail=True
        with self.assertRaises(ChatPersistenceError) as error:agent.run('conversation','client','hello',model=MODEL)
        self.assertEqual(error.exception.result.text,'first');self.assertNotIn('secret',str(error.exception))
        self.tx.fail=False
        self.assertTrue(agent.run('another','next','hello',model=MODEL).persisted)

    def test_reentrant_same_conversation_busy(self):
        agent=self.make([text('done')])
        def nested():
            with self.assertRaises(ChatBusy):agent.run('conversation','other','hello',model=MODEL)
        self.provider.hook=nested
        self.assertTrue(agent.run('conversation','client','hello',model=MODEL).persisted)

    def test_profile_change_in_capture_prevents_actual_provider(self):
        changed=[False];provider=Provider([]);tx=Transcript()
        def resolver(model):return ChatBinding(replace(model,identity='changed') if changed[0] else model,provider)
        agent=ChatAgent(tx,resolver,gate=ALLOW,capture=lambda _:changed.__setitem__(0,True))
        result=agent.run('conversation','client','hello',model=MODEL)
        self.assertEqual(result.result.reason,'model_error');self.assertEqual(provider.calls,[])
        self.assertTrue(result.persisted)

    def test_clear_after_model_blocks_tool_and_no_partial_pair_commit(self):
        tool=Tool('fixture','inert',{},lambda *a:self.fail('cleared tool must not run'),lambda _:True)
        agent=self.make([use()],tools=[tool]);self.provider.hook=self.tx.clear
        result=agent.run('conversation','client','hello',model=MODEL)
        self.assertTrue(result.stale);self.assertFalse(result.persisted)
        self.assertEqual(result.result.messages[-1]['content'][0]['tool_use_id'],'call1')
        self.assertEqual(self.tx.commits,[])

    def test_bad_adapters_and_unsupported_streaming(self):
        agent=self.make([])
        with self.assertRaises(ValueError):agent.run('conversation','client','hello',model=replace(MODEL,streaming=True))
        self.assertEqual(self.tx.history,{})
        with self.assertRaises(ValueError):ChatAgent(self.tx,lambda _:None,gate=None)
        self.tx.begin=lambda *a:TranscriptTurn('id',0,1,[])
        with self.assertRaises(ChatPersistenceError):agent.run('conversation','client','hello',model=MODEL)
        self.assertEqual(self.provider.calls,[])


if __name__=='__main__':unittest.main()
