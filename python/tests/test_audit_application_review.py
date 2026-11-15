"""Independent optional audit wiring checks; synthetic guards only."""
import unittest
import test_audit_application as fixture
from test_tasks_store import Fake
from test_application import TestCodec
from artex.server.application import build_application
from artex.server.audit import AuditService

class Review(unittest.TestCase):
    def test_postconstruction_callback_swap_cannot_resolve(self):
        f=fixture.Tests();f.setUp();db=Fake([])
        app=build_application(db,b'x'*32,TestCodec(),audit_service=f.service)
        f.service.resolve=lambda s:(_ for _ in ()).throw(AssertionError('foreign resolver'))
        self.assertEqual(f.request(app)[0],503)
        self.assertEqual(f.calls,[]);self.assertEqual(db.calls,[])

    def test_current_revocation_and_raw_denial_survive_composition(self):
        for mode in ('current','raw'):
            with self.subTest(mode=mode):
                f=fixture.Tests();f.setUp();db=Fake([]);allowed=[True]
                old=f.service
                f.service=AuditService(resolve=old.resolve,authorize_selection=old.authorize_selection,
                    raw_authorize=lambda *a:allowed[0] if mode=='raw' else True,
                    current=lambda b:allowed[0] if mode=='current' else True,
                    lifecycle_guard=old.guard)
                app=build_application(db,b'x'*32,TestCodec(),audit_service=f.service)
                allowed[0]=False
                status,body=f.request(app)
                self.assertEqual(status,403);self.assertNotIn('entries',body)
                self.assertEqual(db.calls,[])
