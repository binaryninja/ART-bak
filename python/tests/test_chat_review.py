"""Independent inert chat admission/generation regression tests."""
import unittest
from artex.agent.chat import ChatAgent,ChatBinding,ChatPersistenceError
from artex.agent.runtime import Tool
from test_chat_runtime import Transcript,Provider,MODEL,ALLOW,text

class ChatReviewTests(unittest.TestCase):
    def test_clear_during_resolve_prevents_stale_capture(self):
        tx=Transcript();provider=Provider([text('unused')]);captures=[];calls=[]
        def resolve(model):
            calls.append(1)
            if len(calls)==2:tx.clear()
            return ChatBinding(model,provider)
        agent=ChatAgent(tx,resolve,gate=ALLOW,capture=captures.append)
        result=agent.run('conversation','id','hello',model=MODEL)
        self.assertEqual(provider.calls,[])
        self.assertFalse(result.persisted)
        self.assertEqual(captures,[], 'Capture must not publish a cleared generation')

    def test_invalid_tool_configuration_fails_before_user_persistence(self):
        tx=Transcript();provider=Provider([])
        try:
            agent=ChatAgent(tx,lambda model:ChatBinding(model,provider),gate=ALLOW,
                            tools=[Tool('invalid','test',{},None,lambda _:True)])
            agent.run('conversation','id','hello',model=MODEL)
        except ValueError:pass
        else:self.fail('invalid tool must be rejected')
        self.assertEqual(tx.history,{},'Invalid startup must not leave an unresolved persisted turn')
        self.assertEqual(tx.turns,{})

    def test_retry_after_failed_commit_must_not_claim_durable_completion(self):
        tx=Transcript();provider=Provider([text('done')])
        agent=ChatAgent(tx,lambda model:ChatBinding(model,provider),gate=ALLOW)
        tx.fail=True
        with self.assertRaises(ChatPersistenceError):agent.run('conversation','id','hello',model=MODEL)
        tx.fail=False
        try:outcome=agent.run('conversation','id','hello',model=MODEL)
        except (ChatPersistenceError,RuntimeError):return
        self.assertFalse(outcome.persisted,'A reused running turn is not a confirmed durable result')
        self.assertEqual(len(provider.calls),1)

if __name__=='__main__':unittest.main()
