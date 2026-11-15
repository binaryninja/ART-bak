"""Independent disclosure-boundary and optional factory regressions."""
import unittest
import test_intercept_history as history
import test_intercept_inspection_application as assembly
from artex.server.application import build_persisted_application
from artex.server.intercept_inspection import InterceptInspectionService
from artex.server.core import Request
from test_tasks_store import Fake
from test_application import TestCodec

class InspectionAssemblyReview(unittest.TestCase):
    def test_revoked_postfetch_grants_block_private_response(self):
        for gate in ('raw','authorize'):
            f=history.InspectionTests();f.make([[history.ROW]])
            checks=[]
            def grant(scope,selection):
                checks.append(selection)
                return len(checks)==1
            setattr(f.store,gate,grant)
            response=f.service.handle('pending',Request('GET','/api/intercept/pending'))
            self.assertEqual(response.status,403)
            self.assertEqual(len(checks),2)
            self.assertIs(checks[0],checks[1])
            self.assertNotIn(b'"tool_input"',response.body)
    def test_original_guard_changed_before_service_capture_rejected_before_startup(self):
        f=assembly.InspectionApplicationTests();db=Fake([]);store=f.make(db).store
        store.guard=lambda *a:self.fail('must not invoke guard')
        service=InterceptInspectionService(store,authorize=lambda *a:True)
        with self.assertRaises(ValueError):build_persisted_application(db,b'x'*32,TestCodec(),intercept_inspection_service=service)
        self.assertEqual(db.calls,[])
