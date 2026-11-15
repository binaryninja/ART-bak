"""Independent pool factory checks; no providers or external effects."""
import unittest
import test_pool_application as fixtures
from artex.server.application import build_application
from artex.health import State
from test_tasks_store import Fake
from test_application import TestCodec

class PoolApplicationReview(unittest.TestCase):
    def test_write_routes_are_also_absent_by_default(self):
        f=fixtures.PoolApplicationTests();app=build_application(Fake([]),b'x'*32,TestCodec())
        for path in ('/api/llm/pool/reset','/api/llm/retry-policy'):
            self.assertEqual(f.request(app,path,method='POST',body=b'{}')[0],404)
    def test_unauthenticated_mutation_never_calls_authority_or_registry(self):
        f=fixtures.PoolApplicationTests();db=Fake([]);service=f.make(db)
        service.registry.restore(7,State(1))
        def forbidden(*args):self.fail('Must not invoke host authority')
        service.authorize=forbidden;service.current=forbidden
        service.idle=forbidden;service.coordinate=forbidden;service.mutate=forbidden
        app=build_application(db,b'x'*32,TestCodec(),pool_health_service=service)
        self.assertEqual(f.request(app,'/api/llm/pool/reset',authorized=False,method='POST',body=b'{"id":7}')[0],401)
        self.assertEqual(service.registry.get(7).fails,1);self.assertEqual(db.calls,[])
    def test_mounting_does_not_install_missing_coordinator(self):
        f=fixtures.PoolApplicationTests();db=Fake([]);service=f.make(db)
        service.registry.restore(7,State(1))
        service.mutate=lambda *a:True;service.idle=lambda *a:True
        app=build_application(db,b'x'*32,TestCodec(),pool_health_service=service)
        self.assertIsNone(service.coordinate)
        self.assertEqual(f.request(app,'/api/llm/pool/reset',method='POST',body=b'{"id":7}')[0],503)
        self.assertEqual(service.registry.get(7).fails,1);self.assertEqual(db.calls,[])
