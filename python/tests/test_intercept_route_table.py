import unittest
import test_intercept_rules_http as fixtures
from artex.server.core import Router

class RouteTableTests(unittest.TestCase):
    def test_only_exact_scope_paths_registered(self):
        for task in (True,False):
            fixture=fixtures.HTTPRuleTests();service=fixture.make([],task=task)
            router=Router(lambda *args:False);service.register(router)
            base='/api/tasks/{id}/intercept-rules' if task else '/api/asset-intercept/rules'
            rid='rid' if task else 'id'
            expected={('GET',base),('POST',base),('PUT',base+'/{'+rid+'}'),('DELETE',base+'/{'+rid+'}'),('POST',base+'/{'+rid+'}/toggle')}
            self.assertEqual({(r[0],r[1]) for r in router._routes},expected)
            self.assertEqual(len(router._routes),5)
            self.assertTrue(all(r[4] is False for r in router._routes))
            self.assertEqual(fixture.f.db.calls,[])
