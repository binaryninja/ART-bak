"""Independent inert HTTP compaction authority/admission checks."""
from dataclasses import replace
import unittest
from artex.agent.chat import ChatBinding
from artex.agent.chat_compaction import ChatCompaction
from artex.agent.compaction import Budget
import test_chat_http


class AssemblyReview(unittest.TestCase):
    def fixture(self):
        f=test_chat_http.ChatHTTPTests();f.setUp();return f

    def test_client_cannot_supply_compaction_configuration(self):
        f=self.fixture()
        for key in ('compaction','context_window','max_summary_bytes'):
            response=f.router.dispatch(f.request({'message':'hello',key:1}))
            self.assertEqual(response.status,400)
        self.assertEqual(f.activities.rows,[])
        self.assertEqual(f.queue.jobs,{})
        self.assertEqual(f.provider.calls,[])

    def test_profile_window_rotation_after_queue_admission_never_calls_provider(self):
        f=self.fixture();model=replace(f.resources.model,window_tokens=4000)
        active=[model]
        f.resources=replace(f.resources,model=model,compaction=ChatCompaction(Budget(4000,128,1)),
                            resolver=lambda expected:ChatBinding(active[0],f.provider))
        self.assertEqual(f.router.dispatch(f.request()).status,202)
        active[0]=replace(model,window_tokens=8000,identity='rotated')
        f.queue.drain()
        self.assertEqual(f.provider.calls,[])
        self.assertEqual(len(f.queue.results),1)
        self.assertTrue(isinstance(f.queue.results[0],Exception) or
                        f.queue.results[0].result.reason!='completed')
