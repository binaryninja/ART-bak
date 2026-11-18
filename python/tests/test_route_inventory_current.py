"""Source-only regression for current declarations and generated artifact checks."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import test_route_inventory as fixture


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'python/tools/route_inventory.py'
audit = fixture.audit


class CurrentCheckoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = audit.inventory(ROOT)

    def test_existing_optional_registrations_are_matched_from_literal_source(self):
        expected = {
            ('GET', '/api/task-archives'), ('GET', '/api/task-archives/{id}'),
            ('GET', '/api/skills/missing'), ('GET', '/api/skills/{name}/usage'),
            ('GET', '/api/skills/{name}/files'), ('POST', '/api/skills'),
            ('POST', '/api/skills/upload'), ('POST', '/api/skills/{name}/dirs'),
            ('POST', '/api/intercept/rules'), ('PUT', '/api/intercept/rules/{id}'),
            ('DELETE', '/api/intercept/rules/{id}'),
            ('POST', '/api/intercept/rules/{id}/toggle'),
            ('PUT', '/api/intercept/tool-config'),
        }
        index = {(r['method'], r['path']): r for r in self.data['go_routes']}
        for key in expected:
            with self.subTest(key=key):
                declaration = index[key]['python']
                self.assertIsNotNone(declaration)
                self.assertEqual(declaration['registration_mode'], 'optional explicit service registration')
                self.assertFalse(declaration['public'])
                self.assertIn('no ', declaration['dependencies'])
                self.assertIn('default activation', declaration['dependencies'])
                source = ROOT / declaration['source']
                self.assertIn('.add(', source.read_text().splitlines()[declaration['line'] - 1])

    def test_conversation_table_has_six_routes_and_correct_list_handler(self):
        rows = [r for r in self.data['python_route_declarations']
                if r['function'] == 'ConversationService.register']
        self.assertEqual(len(rows), 6)
        listed = [r for r in rows if r['method'] == 'GET']
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]['path'], '/api/conversations')
        self.assertEqual(listed[0]['handler'], 'bind_conversation_list(self, router)')
        self.assertTrue(all('self._handle(' in r['handler'] for r in rows if r['method'] != 'GET'))

    def test_all_dynamic_and_fixed_read_declarations_remain_optional(self):
        rows = [r for r in self.data['python_route_declarations']
                if r['function'] in ('TaskRuntimeReadRouter.bind', 'TaskRuntimeReads.bind')]
        self.assertEqual(len(rows), 28)
        for row in rows:
            with self.subTest(path=row['path'], owner=row['function']):
                self.assertEqual(row['registration_mode'], 'optional explicit service registration')
                self.assertIn('no ', row['dependencies'])
                self.assertIn('default activation', row['dependencies'])
        self.assertEqual({r['function'] for r in self.data['python_route_declarations']
                          if r['registration_mode'] == 'auth router factory'}, {'AuthService.router'})

    def test_placeholder_spelling_is_preserved_instead_of_inventing_an_alias(self):
        self.assertEqual([(r['method'], r['path']) for r in self.data['python_only']],
                         [('GET', '/api/assessments/runtime'),
                          ('GET', '/api/assessment-definitions'),
                          ('GET', '/api/assessments'),
                          ('POST', '/api/assessments'),
                          ('POST', '/api/assessments/discover'),
                          ('GET', '/api/tasks/{id}/assessment'),
                          ('GET', '/api/tasks/{id}/assessment/evidence/{operation}'),
                          ('POST', '/api/llm/profiles/reconcile'),
                          ('GET', '/api/recon'),
                          ('POST', '/api/recon/campaigns'),
                          ('GET', '/api/recon/campaigns/{id}'),
                          ('GET', '/api/intercept/task/{id}')])
        matches = [r for r in self.data['go_routes'] if r['path'] == '/api/intercept/task/{taskID}']
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]['python']['source'], 'python/artex/server/intercept_read_setup.py')

    def test_checked_in_artifacts_and_every_recorded_source_hash_are_current(self):
        self.assertEqual(json.loads((ROOT / 'python/ROUTE_PARITY.json').read_text()), self.data)
        self.assertEqual((ROOT / 'python/ROUTE_PARITY.md').read_text(), audit.markdown(self.data))
        for path, digest in self.data['source_sha256'].items():
            with self.subTest(path=path):
                self.assertEqual(hashlib.sha256((ROOT / path).read_bytes()).hexdigest(), digest)


class OptionalOwnerTests(unittest.TestCase):
    def scan(self, source, owner, *, registration=True):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / source
            path.parent.mkdir(parents=True)
            statement = "router.add('GET', '/fixture', handler)" if registration else 'pass'
            if '.' in owner:
                class_name, method = owner.split('.')
                body = f'class {class_name}:\n    def {method}(self, router):\n        {statement}\n'
            else:
                body = f'def {owner}(router):\n    {statement}\n'
            path.write_text("raise RuntimeError('fixture must never execute')\n" + body)
            return audit.python_inventory(root)[0]

    def test_optional_annotation_requires_exact_source_and_owner(self):
        for (source, owner), dependencies in audit.PYTHON_OPTIONAL_OWNERS.items():
            with self.subTest(source=source, owner=owner):
                rows = self.scan(source, owner)
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]['dependencies'], dependencies)
                self.assertEqual(rows[0]['registration_mode'], 'optional explicit service registration')
                self.assertEqual(self.scan(source, owner, registration=False), [])
                for alternate_source, alternate_owner in (
                    ('python/artex/server/other.py', owner), (source, 'Other.bind'),
                ):
                    row = self.scan(alternate_source, alternate_owner)[0]
                    self.assertEqual(row['registration_mode'], 'auth router factory')
                    self.assertNotEqual(row['dependencies'], dependencies)


class StaleSourceTests(unittest.TestCase):
    def test_check_detects_non_route_source_change_without_rewriting_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'server').mkdir()
            (root / 'server/routes.go').write_text(
                'package server\nfunc register() {\n mux.HandleFunc("GET /fixture", handler)\n}\n')
            source = root / 'python/artex/server/fixture.py'
            source.parent.mkdir(parents=True)
            source.write_text("raise RuntimeError('never execute')\ndef register(router):\n    router.add('GET', '/fixture', handler)\n")

            def invoke(*args):
                return subprocess.run([sys.executable, str(SCRIPT), '--root', str(root), *args],
                                      capture_output=True, text=True, check=False)

            created = invoke()
            self.assertEqual(created.returncode, 0, created.stderr)
            paths = [root / 'python/ROUTE_PARITY.json', root / 'python/ROUTE_PARITY.md']
            before = [p.read_bytes() for p in paths]
            self.assertEqual(invoke('--check').returncode, 0)
            source.write_text(source.read_text() + '# Source changed without changing route literals.\n')
            stale = invoke('--check')
            self.assertEqual(stale.returncode, 1, stale.stderr)
            self.assertIn('Stale route inventory:', stale.stderr)
            self.assertIn('ROUTE_PARITY.json', stale.stderr)
            self.assertEqual([p.read_bytes() for p in paths], before)
            self.assertEqual(invoke().returncode, 0)
            self.assertEqual(invoke('--check').returncode, 0)
