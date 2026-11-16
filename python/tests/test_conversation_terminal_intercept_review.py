"""Independent actual conversation dispatch boundary regressions."""
import unittest
from unittest.mock import patch
from artex.agent.runtime import Action,Grant,RunStopped
from artex.intercept.durable import ConversationTerminalIntercept
import test_conversation_terminal_intercept as base
class Review(unittest.TestCase):
    def fixture(self,**kw):
        f=base.DurableConversationTests();self.addCleanup(f.doCleanups);f.make(**kw);return f
    def test_manual_dispatch_before_validated_action_refused(self):
        attempted=[];denied=[];f=None
        def gate(context,action):
            if action.kind=='model' and not attempted:
                attempted.append(True)
                session=f.owner._session
                try:
                    with session.dispatch(session._runner,context,Action('tool','echo','{}','fabricated')):pass
                except RunStopped:denied.append(True)
                # Stop here to avoid a later real tool call muddying evidence.
                return Grant(False,False,False)
            return Grant(True,True,True)
        f=self.fixture(gate=gate);f.run_agent()
        inserts=[q for q in f.db.calls if hasattr(q,'sql') and q.sql.startswith('INSERT')]
        self.assertTrue(denied,'Unissued pre-validation Action was accepted')
        self.assertEqual(inserts,[])
    def test_last_call_grant_cannot_revoke_normal_authority_then_execute(self):
        permitted=[True];tool_grants=[]
        def gate(context,action):
            if action.kind=='tool':tool_grants.append(True)
            return Grant(permitted[0],True,True)
        def call(*args):
            if len(tool_grants)>=2:permitted[0]=False
            return True
        f=base.DurableConversationTests();self.addCleanup(f.doCleanups)
        def owner(*args,**kwargs):
            kwargs['authorize_call']=call
            return ConversationTerminalIntercept(*args,**kwargs)
        with patch.object(base,'ConversationTerminalIntercept',owner):f.make(gate=gate)
        out=f.run_agent()
        self.assertFalse(f.effects,'Tool executed after original ordinary grant was revoked')
    def test_foreign_thread_cannot_use_live_receipt(self):
        from threading import Thread
        errors=[]
        f=None
        def callback(*args):
            session=f.owner._session
            def attempt():
                try:session._issued(session._call)
                except RunStopped:errors.append(True)
            t=Thread(target=attempt);t.start();t.join(2)
            self.assertFalse(t.is_alive());return 'inert'
        f=self.fixture(callback=callback);f.run_agent();self.assertEqual(errors,[True])
    def test_action_mutation_during_audit_refused(self):
        f=self.fixture()
        def mutate(q):
            if q.sql.startswith('INSERT'):
                action=f.owner._session._call_data[1]
                object.__setattr__(action,'input_json','{"value":"changed"}')
        f.db.on_query=mutate
        from artex.agent.runtime import EffectUncertain
        with self.assertRaises(EffectUncertain):f.run_agent()
        self.assertFalse(f.effects);self.assertTrue(f.policy._calls)
    def test_runtime_slot_revoked_after_success(self):
        saved=[];f=None
        def callback(*args):saved.append(f.owner._session._runner);return 'inert'
        f=self.fixture(callback=callback);f.run_agent()
        self.assertIsNone(saved[0]._terminal_dispatch_slot)
if __name__=='__main__':unittest.main()
