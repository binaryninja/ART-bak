import unittest
import test_route_inventory_detail as fixture

class Tests(unittest.TestCase):
    def test_exact_optional_judge_config(self):
        rows=fixture.Tests().scan("        if 'judge_config' in services:staged.add('GET','/api/intercept/judge',handler)")
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['path'],'/api/intercept/judge');self.assertFalse(rows[0]['public'])
    def test_wrong_path_guard_and_owner_rejected(self):
        for text,kw in [
            ("if 'judge_config' in services:staged.add('GET','/api/intercept/judge/usage',handler)",{}),
            ("if 'judge_config' in other:staged.add('GET','/api/intercept/judge',handler)",{}),
            ("if 'judge_config' in services:staged.add('PUT','/api/intercept/judge',handler)",{}),
            ("if 'judge_config' in services:staged.add('GET','/api/intercept/judge',handler)",{'file':'other.py'}),
        ]:
            with self.subTest(text=text,kw=kw),self.assertRaises(fixture.fixture.audit.AuditError):fixture.Tests().scan('        '+text,**kw)
