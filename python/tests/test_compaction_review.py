"""Independent compaction boundary tests using only an inert provider."""
import unittest
from copy import deepcopy
from artex.agent.compaction import WorkingCompactor, Budget, CompactionError
from artex.agent.runtime import Grant
from test_agent_compaction import Provider, user, assistant
from test_process import Context


class CompactionReview(unittest.TestCase):
    def test_tail_boundary_retains_entire_tool_round_and_independent_raw(self):
        provider = Provider()
        messages = [user('old facts '*5000), assistant('old answer '*1000),
            {'role':'assistant','content':[{'type':'tool_use','id':'pair1','name':'fixture','input':{}}]},
            {'role':'user','content':[{'type':'tool_result','tool_use_id':'pair1','content':[{'type':'text','text':'observed'}]}]},
            assistant('recent')]
        original = deepcopy(messages)
        compactor = WorkingCompactor(provider, gate=lambda *a: Grant(True,True,True),
            current=lambda: True, budget=Budget(keep_recent=2))
        result = compactor.compact(Context(), messages, force=True)
        self.assertEqual(result.messages[1:], original[-3:])
        self.assertEqual(result.summarized_messages, 2)
        self.assertNotIn('pair1', provider.calls[0]['messages'][0]['content'][0]['text'])
        result.raw_messages[0]['content'][0]['text'] = 'changed copy'
        self.assertEqual(messages, original)

    def test_refusal_with_apparently_successful_stop_does_not_publish_or_retry(self):
        provider = Provider()
        message = assistant('plausible summary')
        message['refusal'] = 'declined'
        provider.result = message, 'end_turn', {}
        messages = [user('old '*5000), assistant('old '*1000), assistant('tail')]
        before = deepcopy(messages)
        compactor = WorkingCompactor(provider, gate=lambda *a: Grant(True,True,True),
            current=lambda: True, budget=Budget(keep_recent=1))
        with self.assertRaises(CompactionError):
            compactor.compact(Context(), messages, force=True)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(messages, before)
