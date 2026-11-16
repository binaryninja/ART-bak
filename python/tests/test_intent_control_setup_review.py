"""Independent HTTP authority boundary regressions; inert actual owned worker."""
import unittest
from unittest.mock import patch
import test_intent_control_setup as base

class Review(unittest.TestCase):
    def make(self,proposal=None):
        f=base.Tests(); original=base.IntentControlService
        def create(*a,**kw):
            if proposal is not None:kw['authorize_proposal']=lambda p:proposal(f,p)
            return original(*a,**kw)
        with patch.object(base,'IntentControlService',create):f.setUp()
        self.addCleanup(f.doCleanups)
        return f
    def test_final_proposal_callback_cannot_revoke_selector_then_cancel(self):
        def grant(f,p):f.allowed=False;return True
        f=self.make(grant);f.f.start()
        f.request()
        self.assertFalse(f.service.adapter.retained(f.f.outer))
        self.assertFalse(f.f.outer._child.worker._active[3][0].cancelled)
    def test_after_headers_proposal_revokes_selector_no_body(self):
        late=[False]
        def grant(f,p):
            if late[0]:f.allowed=False
            return True
        f=self.make(grant);f.f.start()
        with self.assertRaises(PermissionError):f.request(start=lambda:late.__setitem__(0,True))
        self.assertFalse(f.service.adapter.authorize(None))
    def test_request_headers_mutation_denied_before_worker_cancel(self):
        f=self.make();f.f.start()
        from artex.server.intent_control import _CONTEXT
        def hook():_CONTEXT.get()['request'].headers['Authorization']='Bearer changed'
        f.selector_hook=hook
        self.assertEqual(f.request()[0],403)
        self.assertFalse(f.service.adapter.retained(f.f.outer))
    def test_unsupported_action_does_not_create_retained_control(self):
        f=self.make();f.f.start()
        self.assertEqual(f.request({'action':'cancel'})[0],501)
        self.assertFalse(f.service.adapter.retained(f.f.outer))
        self.assertFalse(f.service.adapter.authorize(None))
