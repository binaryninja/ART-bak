"""Independent opt-in assembly checks; no commands are executed."""
import unittest
from artex.server.application import build_application,build_persisted_application
import test_command_application as fixture_module
from test_tasks_store import Fake
from test_application import TestCodec

class CommandApplicationReview(unittest.TestCase):
    def test_mounted_metadata_permission_never_implies_raw_grant(self):
        fixture=fixture_module.CommandApplicationTests();db=Fake([])
        app=build_application(db,b'x'*32,TestCodec(),command_history_service=fixture.make(db))
        self.assertEqual(fixture.request(app,'/api/commands')[0],403)
        self.assertEqual(db.calls,[])
    def test_guard_change_before_startup_is_rejected(self):
        for factory in (build_application,build_persisted_application):
            with self.subTest(factory=factory.__name__):
                fixture=fixture_module.CommandApplicationTests();db=Fake([]);service=fixture.make(db)
                service.store.guard=lambda *a: self.fail('must not invoke replacement guard')
                with self.assertRaises(ValueError):factory(db,b'x'*32,TestCodec(),command_history_service=service)
                self.assertEqual(db.calls,[]);self.assertEqual(fixture.calls,[])
    def test_assembly_invokes_no_scope_or_request_policies(self):
        fixture=fixture_module.CommandApplicationTests();db=Fake([]);service=fixture.make(db)
        def forbidden(*a):self.fail('assembly must not invoke authority')
        service.store.authorize=forbidden;service.store.current=forbidden;service.authorize=forbidden
        build_application(db,b'x'*32,TestCodec(),command_history_service=service)
        self.assertEqual(db.calls,[])
