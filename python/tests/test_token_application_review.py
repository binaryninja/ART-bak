"""Independent opt-in token factory checks; no external services."""
import unittest
from artex.server.application import build_application,build_persisted_application
from artex.server.token_usage import TokenUsageService
from artex.db.token_usage import TokenUsageStore,UsageScope
from test_tasks_store import Fake
from test_application import TestCodec

class TokenApplicationReview(unittest.TestCase):
    def service(self,db):
        def forbidden(*a):raise AssertionError('No assembly policy callbacks')
        store=TokenUsageStore(db,UsageScope('fixed'),authorize=forbidden,current=forbidden,lifecycle_guard=forbidden)
        return TokenUsageService(store,authorize=forbidden)
    def test_valid_assembly_invokes_no_authority_callbacks(self):
        db=Fake([])
        build_application(db,b'x'*32,TestCodec(),token_usage_service=self.service(db))
        self.assertEqual(db.calls,[])
    def test_post_service_pins_cannot_change_before_startup(self):
        for field in ('guard','scope','executor'):
            for factory in (build_application,build_persisted_application):
                with self.subTest(field=field,factory=factory.__name__):
                    db=Fake([]);service=self.service(db)
                    setattr(service.store,field,object())
                    with self.assertRaises(ValueError):factory(db,b'x'*32,TestCodec(),token_usage_service=service)
                    self.assertEqual(db.calls,[])
    def test_replacement_service_store_cannot_reuse_original_pins(self):
        db=Fake([]);service=self.service(db);service.store=self.service(db).store
        with self.assertRaises(ValueError):build_persisted_application(db,b'x'*32,TestCodec(),token_usage_service=service)
        self.assertEqual(db.calls,[])
