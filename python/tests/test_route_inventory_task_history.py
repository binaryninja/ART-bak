import unittest
import test_route_inventory_detail as fixture
class Tests(unittest.TestCase):
    def test_exact_path_and_guard(self):
        rows=fixture.Tests().scan("        if 'task_history' in services:staged.add('GET','/api/intercept/task/{taskID}',handler)")
        self.assertEqual(rows[0]['path'],'/api/intercept/task/{taskID}')
        self.assertFalse(rows[0]['public'])
    def test_unknown_not_implicitly_supported(self):
        for statement in ("if 'task_history' in services:staged.add('GET','/api/intercept/task/{id}',handler)","if 'task_history' in other:staged.add('GET','/api/intercept/task/{taskID}',handler)","if 'task_history' in services:staged.add('POST','/api/intercept/task/{taskID}',handler)"):
            with self.assertRaises(fixture.fixture.audit.AuditError):fixture.Tests().scan('        '+statement)
