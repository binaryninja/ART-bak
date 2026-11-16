import unittest
import test_route_inventory_detail as fixture
class Tests(unittest.TestCase):
    def test_exact_path_and_guard(self):
        rows=fixture.Tests().scan("        if 'execution' in services:staged.add('GET','/api/intercept/history/{id}/execution',handler)")
        self.assertEqual(rows[0]['path'],'/api/intercept/history/{id}/execution')
        self.assertFalse(rows[0]['public'])
    def test_unknown_not_implicitly_supported(self):
        for statement in ("if 'execution' in services:staged.add('GET','/api/intercept/history/{other}/execution',handler)","if 'execution' in other:staged.add('GET','/api/intercept/history/{id}/execution',handler)","if 'execution' in services:staged.add('POST','/api/intercept/history/{id}/execution',handler)"):
            with self.assertRaises(fixture.fixture.audit.AuditError):fixture.Tests().scan('        '+statement)
