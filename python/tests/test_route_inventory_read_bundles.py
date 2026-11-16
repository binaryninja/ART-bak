"""Read-only AST classification for explicit original read bundles."""
import unittest
import test_route_inventory_detail as fixture

class ReadBundles(unittest.TestCase):
    def test_real_declarations_are_optional(self):
        for file,owner,method,path in (
            ('catalog_read_setup.py','_CatalogApplication','__call__','/api/chat/mentions'),
            ('task_report_setup.py','_ReportApplication','__init__','/api/report'),
        ):
            with self.subTest(path=path):
                rows=fixture.Tests().scan(f"        staged.add('GET','{path}',handler)",file=file,owner=owner,method=method)
                self.assertEqual(len(rows),1)
                self.assertEqual(rows[0]['registration_mode'],'optional explicit service registration')
                self.assertFalse(rows[0]['public'])
                self.assertIn('original issued disclosure receipt',rows[0]['dependencies'])
    def test_absent_declaration_is_not_invented(self):
        rows=fixture.Tests().scan('        pass',file='catalog_read_setup.py',owner='_CatalogApplication',method='__call__')
        self.assertEqual(rows,[])
    def test_report_alternative_is_exact_and_not_duplicate_permission(self):
        rows=[]
        for file,owner,method in (('task_report.py','TaskReportService','register'),('task_report_setup.py','_ReportApplication','__init__')):
            rows+=fixture.Tests().scan("        staged.add('GET','/api/report',handler)",file=file,owner=owner,method=method)
        grouped=fixture.fixture.audit.group_python_routes(rows)
        self.assertEqual(len(grouped),1)
        self.assertEqual(len(grouped[0]['declarations']),2)
        self.assertIn('alternative_group',grouped[0])
        for bad in (rows+[rows[0]],rows+[dict(rows[0],function='Other.register')],[rows[0],dict(rows[1],public=True)]):
            with self.assertRaises(fixture.fixture.audit.AuditError):fixture.fixture.audit.group_python_routes(bad)
