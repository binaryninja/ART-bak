"""Independent runtime boundary review; fake services/providers/storage only."""
from dataclasses import replace
from unittest.mock import patch
import unittest
from test_sidequestion_runtime import Store,Provider,SNAP
from artex.sidequestion import Answer,ContextInfo
from artex.sidequestion.runtime import SideQuestionCoordinator,ProviderBinding,SidePersistenceError
from artex.db.sidequestions import Parent


class TwoBoundaryService:
    """Deterministic compaction-like call followed by answer-like call."""
    def __init__(self,provider):self.provider=provider
    def respond(self,snapshot,question,*args,**kwargs):
        self.provider.complete({'fixture':'compaction'})
        self.provider.complete({'fixture':'answer'})
        return Answer(text='fixture'),ContextInfo()


class RuntimeReviewTests(unittest.TestCase):
    def test_profile_change_between_provider_boundaries_blocks_second_call(self):
        changed=[False]
        provider=Provider(lambda:changed.__setitem__(0,True))
        def resolve(model):
            return ProviderBinding(replace(model,identity='edited-config') if changed[0] else model,provider)
        coordinator=SideQuestionCoordinator(resolve);store=Store()
        admitted=coordinator.admit(store,'client','question')
        with patch('artex.sidequestion.runtime.SideQuestionService',TwoBoundaryService):
            result=coordinator.execute(admitted.handle)
        self.assertEqual(len(provider.calls),1,'A changed profile must stop the next actual provider call')
        self.assertEqual(result.exchange.status,'failed')
        self.assertTrue(result.persisted)

    def test_clear_between_provider_boundaries_prevents_late_call_and_writes(self):
        store=Store();provider=Provider(store.clear_for_test)
        coordinator=SideQuestionCoordinator(lambda m:ProviderBinding(m,provider))
        admitted=coordinator.admit(store,'client','question')
        with patch('artex.sidequestion.runtime.SideQuestionService',TwoBoundaryService):
            result=coordinator.execute(admitted.handle)
        self.assertEqual(len(provider.calls),1)
        self.assertTrue(result.stale);self.assertFalse(result.persisted);self.assertIsNone(store.row)

    def test_final_persistence_failure_releases_global_slot_without_false_success(self):
        store=Store();provider=Provider()
        coordinator=SideQuestionCoordinator(lambda m:ProviderBinding(m,provider),max_running=1)
        admitted=coordinator.admit(store,'client','question');store.fail_writes=10
        with self.assertRaises(SidePersistenceError):coordinator.execute(admitted.handle)
        other=Store(replace(SNAP,parent=Parent(conversation_id=8)))
        second=coordinator.admit(other,'other','question')
        self.assertTrue(second.created)
        coordinator.cancel(second.handle);coordinator.execute(second.handle)

    def test_external_cancel_after_response_preserves_accounted_usage(self):
        cancelled=[False]
        store=Store();provider=Provider(lambda:cancelled.__setitem__(0,True))
        coordinator=SideQuestionCoordinator(lambda m:ProviderBinding(m,provider))
        admitted=coordinator.admit(store,'client','question',cancelled=lambda:cancelled[0])
        result=coordinator.execute(admitted.handle)
        self.assertEqual(result.exchange.status,'cancelled')
        self.assertEqual(result.exchange.answer,'answer')
        self.assertEqual(result.exchange.usage,{'input_tokens':5,'output_tokens':2})
        self.assertTrue(result.persisted)


if __name__=='__main__':unittest.main()
