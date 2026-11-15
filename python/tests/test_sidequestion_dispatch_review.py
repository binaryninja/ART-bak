"""Independent dispatch ownership regression; inert local fixtures only."""
import unittest
from artex.sidequestion.runtime import SideQuestionCoordinator,ProviderBinding
from artex.sidequestion.dispatch import SideQuestionDispatcher
from artex.db.sidequestions import SideInvalid
from test_sidequestion_runtime import Store,Provider

class DispatchReviewTests(unittest.TestCase):
    def test_foreign_coordinator_admission_not_acknowledged(self):
        provider=Provider()
        owner=SideQuestionCoordinator(lambda model:ProviderBinding(model,provider))
        other=SideQuestionCoordinator(lambda model:ProviderBinding(model,provider))
        store=Store();admission=owner.admit(store,'request','question')
        dispatcher=SideQuestionDispatcher(other)
        try:
            with self.assertRaises(SideInvalid):dispatcher.dispatch(admission)
            self.assertEqual(provider.calls,[])
        finally:
            dispatcher.close(cancel=True,timeout=2)
            owner.abort_unstarted(admission.handle)

if __name__=='__main__':unittest.main()
