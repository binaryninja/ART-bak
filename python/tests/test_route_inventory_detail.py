"""Narrow detail registration AST whitelist; fixture code never executes."""
import unittest,tempfile
from pathlib import Path
import test_route_inventory as fixture

class Tests(unittest.TestCase):
    def scan(self,statement,*,file='intercept_read_setup.py',owner='_InterceptReadApplication',method='__init__'):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);server=root/'python/artex/server';server.mkdir(parents=True)
            (server/file).write_text(f'class {owner}:\n    def {method}(self,services):\n        staged=Router(auth)\n'+statement+'\n')
            return fixture.audit.python_inventory(root)[0]
    def test_exact_detail_is_optional_with_original_source(self):
        rows=self.scan("        if 'detail' in services:staged.add('GET','/api/intercept/history/{id}',handler)")
        self.assertEqual(len(rows),1);row=rows[0]
        self.assertEqual((row['method'],row['path']),('GET','/api/intercept/history/{id}'))
        self.assertEqual(row['source'],'python/artex/server/intercept_read_setup.py')
        self.assertEqual(row['function'],'_InterceptReadApplication.__init__')
        self.assertFalse(row['public']);self.assertEqual(row['registration_mode'],'optional explicit service registration')
    def test_unknown_path_guard_receiver_method_and_control_flow_rejected(self):
        cases=[
            "if 'detail' in services:staged.add('GET','/api/intercept/history/{id}/execution',handler)",
            "if 'detail' in services:staged.add('GET','/api/intercept/history/{other}',handler)",
            "if 'details' in services:staged.add('GET','/api/intercept/history/{id}',handler)",
            "if 'detail' in other:staged.add('GET','/api/intercept/history/{id}',handler)",
            "if 'detail' not in services:staged.add('GET','/api/intercept/history/{id}',handler)",
            "if 'detail' in services:staged.add('POST','/api/intercept/history/{id}',handler)",
            "if 'detail' in services:staged.add('GET','/api/intercept/history/{id}',handler,public=True)",
        ]
        for statement in cases:
            with self.subTest(statement=statement),self.assertRaises(fixture.audit.AuditError):self.scan('        '+statement)
    def test_other_file_owner_method_not_whitelisted(self):
        statement="        if 'detail' in services:staged.add('GET','/api/intercept/history/{id}',handler)"
        for args in ({'file':'other.py'},{'owner':'Other'},{'method':'register'}):
            with self.subTest(args=args),self.assertRaises(fixture.audit.AuditError):self.scan(statement,**args)
    def test_duplicate_exact_declarations_remain_error(self):
        statement="        if 'detail' in services:staged.add('GET','/api/intercept/history/{id}',handler)\n"
        rows=self.scan(statement*2)
        with self.assertRaises(fixture.audit.AuditError):fixture.audit.group_python_routes(rows)
