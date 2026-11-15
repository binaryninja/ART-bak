"""Independent audit read checks using synthetic in-memory records only."""
import unittest
import test_server_audit as fixture
from artex.guard import Guard

class Review(unittest.TestCase):
    def test_raw_permission_revoked_during_snapshot_hides_commands(self):
        f=fixture.Tests();f.setUp()
        f.audit._record('Bash','allow',command='synthetic-secret')
        original=f.audit.attributions
        def snapshot():
            f.raw=False
            return original()
        f.audit.attributions=snapshot
        result=f.call()
        self.assertEqual(result.status,403)
        self.assertNotIn(b'synthetic-secret',result.body)

    def test_binding_guard_replaced_by_current_callback_fails(self):
        f=fixture.Tests();f.setUp()
        def current(binding):
            object.__setattr__(binding,'audit_guard',Guard())
            return True
        f.service.current=current
        f.service._callbacks=(f.service.resolve,f.service.authorize_selection,f.service.raw_authorize,current,f.service.guard)
        self.assertEqual(f.call().status,503)

    def test_utf8_and_wire_expansion_bounds_reject_not_truncate(self):
        for command in ('é'*40000, '\x01'*60000):
            f=fixture.Tests();f.setUp()
            # Second case fits per-field bytes; repeated entries exceed final JSON cap.
            for _ in range(4):f.audit._record('Bash','allow',command=command)
            self.assertEqual(f.call().status,503)
