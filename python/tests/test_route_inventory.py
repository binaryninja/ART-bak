"""Read-only AST audit fixtures. Fixture source is NEVER imported or executed."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('review_route_inventory', Path(__file__).resolve().parents[1]/'tools/route_inventory.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class RouteInventoryTests(unittest.TestCase):
    def scan(self, body):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            server = root/'python/artex/server'
            server.mkdir(parents=True)
            (server/'fixture.py').write_text('def register(router):\n' + ''.join('    '+line+'\n' for line in body.splitlines()))
            return audit.python_inventory(root)[0]

    def test_literal_table(self):
        routes = self.scan("routes = [('GET','/one'),('POST','/two')]\nfor method,path in routes:\n    router.add(method,path,handler)")
        self.assertEqual([(r['method'],r['path']) for r in routes],[('GET','/one'),('POST','/two')])

    def test_supported_literal_reassignment_uses_new_value(self):
        routes = self.scan("routes = [('GET','/old')]\nroutes = [('POST','/new')]\nfor method,path in routes:\n    router.add(method,path,handler)")
        self.assertEqual([r['path'] for r in routes],['/new'])

    def test_unsupported_writes_cannot_reuse_stale_literal(self):
        mutations = [
            "routes += [('POST','/two')]",
            "routes = make_routes()",
            "routes: list = make_routes()",
            "routes[0] = ('POST','/two')",
            "routes.append(('POST','/two'))",
            "routes.extend([('POST','/two')])",
            "routes[0].append('extra')",
            "ignored = routes.pop()",
            "alter(routes)",
            "alter(value=routes)",
            "alias = routes\nalias.append(('POST','/two'))",
            "alias = routes[0]\nalias[0] = 'POST'",
            "alias = routes[:]\nalias.append(('POST','/two'))",
            "del routes[0]",
            "if condition:\n    routes = [('POST','/two')]",
            "for x in xs:\n    routes.append(x)",
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(audit.AuditError):
                self.scan("routes = [('GET','/one')]\n"+mutation+"\nfor method,path in routes:\n    router.add(method,path,handler)")

    def test_scalar_reassignment_does_not_keep_stale_path(self):
        with self.assertRaises(audit.AuditError):
            self.scan("path = '/one'\npath = compute_path()\nrouter.add('GET',path,handler)")

    def test_post_registration_mutation_fails_audit(self):
        with self.assertRaises(audit.AuditError):
            self.scan("routes = [('GET','/one')]\nfor method,path in routes:\n    router.add(method,path,handler)\nroutes.append(('POST','/two'))")

    def test_source_is_not_executed(self):
        routes = self.scan("raise RuntimeError('never execute fixture')\nrouter.add('GET','/one',handler)")
        self.assertEqual(len(routes),1)

    def test_embedded_and_conditional_registration_fails(self):
        for body in ("result = router.add('GET','/one',handler)",
                     "while condition:\n    router.add('GET','/one',handler)",
                     "if condition:\n    router.add('GET','/one',handler)"):
            with self.subTest(body=body), self.assertRaises(audit.AuditError):
                self.scan(body)
