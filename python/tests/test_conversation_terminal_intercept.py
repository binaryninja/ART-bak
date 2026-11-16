"""Actual ChatAgent/JSONL + inert SQL/transport; no tool effects or real database."""
import unittest,tempfile,json
from pathlib import Path
from threading import Event,Thread
from artex.transcripts import TranscriptStore
from artex.chat_transcripts import ChatTranscriptAdapter,META
from artex.agent.chat import ChatAgent,ChatBinding
from artex.agent.runtime import Grant,EffectUncertain,RunStopped
from artex.intercept.durable import ConversationTerminalIntercept
from artex.db.intercept_decisions import InterceptDecisionStore,DecisionScope
from artex.db.tool_intercept_config import PolicyMutation,ToolRuleProposal,PolicyDenied
from artex.manager import CancellationToken
import test_tool_intercept_config as config_fixture
import test_agent_runtime as runtime_fixture
from test_chat_transcripts import MODEL
from contextlib import nullcontext
class DurableConversationTests(unittest.TestCase):
    def make(self,action='allow',callback=None,gate=None,write=None):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.transcript=ChatTranscriptAdapter(TranscriptStore(Path(tmp.name)))
        row=(3,'literal',True,5,'tool_name','string','echo',action,'synthetic reason',False,0,'deny',config_fixture.NOW,config_fixture.NOW)
        self.db,self.config,self.ic,self.policy=config_fixture.fixture([[row],[('["echo"]',)],[(7,)],[(3,)],[(20,)]])
        self.decisions=InterceptDecisionStore(self.db,DecisionScope('offline-fixture','revision',conversation_id=7),read_authorize=lambda *a:True,write_authorize=write or (lambda *a:True),current=lambda *a:True,lifecycle_guard=lambda *a:nullcontext())
        self.owner=ConversationTerminalIntercept(self.policy,self.ic,self.decisions,self.transcript,session_id='conversation-7',model=MODEL,authorize_call=lambda *a:True,authorize_audit=lambda *a:True)
        self.effects=[];self.provider=runtime_fixture.FakeProvider(runtime_fixture.assistant('',[('protocol-id','echo',{'value':'synthetic'})]),runtime_fixture.assistant('done'))
        callback=callback or (lambda *a:self.effects.append('called') or 'inert result')
        self.agent=ChatAgent(self.transcript,lambda m:ChatBinding(MODEL,self.provider),gate=gate or runtime_fixture.grant,tools=[runtime_fixture.tool(callback)],terminal_intercept=self.owner)
        return self.agent
    def run_agent(self,**kw):return self.agent.run('conversation-7','client','synthetic request',model=MODEL,**kw)
    def test_actual_begin_terminal_audit_then_callback_and_no_execution_ids(self):
        self.make();out=self.run_agent()
        self.assertEqual(out.result.reason,'completed');self.assertEqual(self.effects,['called']);self.assertFalse(self.policy._calls)
        insert=next(q for q in self.db.calls if hasattr(q,'sql') and q.sql.startswith('INSERT'))
        audit=json.loads(insert.params[-1]);self.assertEqual(audit['correlation'],'unavailable');self.assertEqual(audit['execution_status'],'unknown');self.assertNotIn('tool_use_id',audit);self.assertNotIn('run_id',audit)
        self.assertEqual(insert.params[6],'allowed');self.assertIsNotNone(self.transcript.finalized_result('conversation-7',out.turn))
    def test_denied_persisted_no_callback_and_ask_explicitly_unsupported(self):
        for action,reason in (('deny','permission_denied'),('ask','intercept_ask_unsupported')):
            self.make(action);out=self.run_agent();self.assertEqual(out.result.reason,reason);self.assertFalse(self.effects)
            inserts=[q for q in self.db.calls if hasattr(q,'sql') and q.sql.startswith('INSERT')]
            self.assertEqual(len(inserts),int(action=='deny'))
    def test_ordinary_gate_denial_prevents_any_interception_sql(self):
        self.make(gate=lambda *a:Grant(False,False,False));out=self.run_agent()
        self.assertEqual(out.result.reason,'permission_denied');self.assertFalse(self.db.calls);self.assertFalse(self.effects)
    def test_post_insert_cancellation_prevents_callback(self):
        self.make();token=CancellationToken()
        self.db.on_query=lambda q:token.cancel() if q.sql.startswith('INSERT') else None
        with self.assertRaises(EffectUncertain):self.run_agent(token=token)
        self.assertFalse(self.effects);self.assertTrue(self.owner.unresolved);self.assertTrue(self.policy._calls)
    def test_post_audit_ordinary_grant_revocation_prevents_callback(self):
        permitted=[True]
        self.make(gate=lambda *a:Grant(permitted[0],True,True))
        self.db.on_query=lambda q:permitted.__setitem__(0,False) if q.sql.startswith('INSERT') else None
        out=self.run_agent();self.assertEqual(out.result.reason,'permission_denied');self.assertFalse(self.effects)
    def test_unknown_commit_retains_pending_turn_and_no_replay(self):
        self.make();self.db.fail_commit=True
        with self.assertRaises(EffectUncertain):self.run_agent()
        self.assertFalse(self.effects);self.assertTrue(self.policy._calls);n=len(self.db.calls)
        self.assertEqual(self.transcript.store.read('conversation-7')[0][META]['phase'],'running')
        with self.assertRaises(EffectUncertain):self.run_agent()
        self.assertEqual(len(self.db.calls),n)
    def test_active_callback_prevents_policy_mutation_and_other_thread(self):
        entered=Event();release=Event();errors=[]
        def callback(*a):entered.set();release.wait(2);return 'inert'
        self.make(callback=callback)
        def run():
            try:self.run_agent()
            except BaseException as e:errors.append(e)
        thread=Thread(target=run);thread.start();self.assertTrue(entered.wait(2))
        try:
            with self.assertRaises(PolicyDenied):self.policy.mutate(PolicyMutation('create',proposal=ToolRuleProposal('new',pattern='echo')),request_authorize=lambda m:True)
        finally:release.set();thread.join(3)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[]);self.assertFalse(self.policy._calls)
    def test_tool_uncertainty_retains_lease_and_pending_transcript(self):
        def callback(*a):raise EffectUncertain('synthetic pending effect')
        self.make(callback=callback)
        with self.assertRaises(EffectUncertain):self.run_agent()
        self.assertTrue(self.policy._calls);self.assertTrue(self.owner.unresolved)
        self.assertEqual(self.transcript.store.read('conversation-7')[0][META]['phase'],'running')
    def test_foreign_session_and_task_scope_rejected_before_begin(self):
        self.make()
        with self.assertRaises(RunStopped):self.agent.run('other','c','x',model=MODEL)
        self.assertFalse(self.db.calls)
        with self.assertRaises(ValueError):
            decisions=InterceptDecisionStore(self.db,DecisionScope('offline-fixture','r',5,9),read_authorize=lambda *a:True,write_authorize=lambda *a:True,current=lambda *a:True,lifecycle_guard=lambda *a:nullcontext())
            ConversationTerminalIntercept(self.policy,self.ic,decisions,self.transcript,session_id='s',model=MODEL,authorize_call=lambda *a:True,authorize_audit=lambda *a:True)
    def test_copied_session_and_retained_call_cannot_be_reused(self):
        import copy
        seen=[]
        def callback(*a):
            session=self.owner._session;seen.append(session)
            with self.assertRaises(RunStopped):copy.copy(session)._issued(session._call)
            with self.assertRaises(RunStopped):
                with session.dispatch(session._runner,*session._call_data[:2]):pass
            return 'inert'
        self.make(callback=callback);self.run_agent()
        with self.assertRaises(RunStopped):seen[0]._pure()
    def test_original_model_mutation_during_audit_prevents_tool(self):
        from dataclasses import replace
        self.make()
        self.db.on_query=lambda q:setattr(self.agent,'resolver',lambda m:ChatBinding(replace(MODEL,identity='changed'),self.provider)) if q.sql.startswith('INSERT') else None
        with self.assertRaises(EffectUncertain):self.run_agent()
        self.assertFalse(self.effects)
    def test_replaced_persistence_method_rejected_before_begin(self):
        self.make();self.decisions.create=lambda *a,**k:999
        with self.assertRaises(RunStopped):self.run_agent()
        self.assertFalse(self.db.calls);self.assertEqual(self.transcript.store.read('conversation-7'),[])
    def test_original_validated_action_cannot_be_renamed_by_gate(self):
        def gate(context,action):
            if action.kind=='tool':object.__setattr__(action,'name','other')
            return Grant(True,True,True)
        self.make(gate=gate);out=self.run_agent()
        self.assertEqual(out.result.reason,'intercept_action_changed');self.assertFalse(self.effects);self.assertFalse(self.db.calls)
    def test_original_unicode_tool_schema_uses_same_snapshot_encoding(self):
        self.make();self.agent.tools[0].input_schema['description']='Synthetic café field'
        self.assertEqual(self.run_agent().result.reason,'completed');self.assertEqual(self.effects,['called'])
