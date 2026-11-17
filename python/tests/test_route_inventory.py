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

    def test_generic_literal_table_branch_requires_explicit_support(self):
        with self.assertRaises(audit.AuditError):
            self.scan("""for method,path,action in [('GET','/one','list'),('POST','/two','create')]:
    if action == 'list':
        from never_import_this_module import bind_list
        router.add(method,path,bind_list(router))
    else:
        router.add(method,path,write_handler)""")

    def test_cross_iteration_branch_state_is_explicitly_unsupported(self):
        with self.assertRaises(audit.AuditError):
            self.scan("""path = '/initial'
for action in ['first', 'second']:
    if action == 'first':
        path = '/selected'
        router.add('GET', path, handler)
    else:
        router.add('POST', path, handler)""")

    def test_nested_loop_rebinding_is_explicitly_unsupported(self):
        with self.assertRaises(audit.AuditError):
            self.scan("""for action in ['first']:
    for action in ['second']:
        router.add('POST', '/inner', handler)
    if action == 'first':
        router.add('GET', '/wrong', handler)
    else:
        router.add('GET', '/right', handler)""")

    def test_dynamic_or_non_boolean_route_branch_still_fails(self):
        for guard in ("enabled", "action == runtime_action", "check(action)", "action", "1"):
            with self.subTest(guard=guard), self.assertRaises(audit.AuditError):
                self.scan(f"for action in ['list']:\n    if {guard}:\n        router.add('GET','/one',handler)\n    else:\n        router.add('POST','/two',handler)")
        with self.assertRaises(audit.AuditError):
            self.scan("enabled = True\nenabled = runtime_value()\nif enabled:\n    router.add('GET','/one',handler)")

    def test_literal_condition_support_does_not_accept_shadowed_non_loop_names(self):
        cases = (
            "action = 'read'\ndef nested(action):\n    if action == 'read':\n        router.add('GET','/one',handler)",
            "for action in ['read']:\n    def nested(action):\n        if action == 'read':\n            router.add('GET','/one',handler)",
            "for action in ['read']:\n    action = runtime_action()\n    if action == 'read':\n        router.add('GET','/one',handler)",
        )
        for body in cases:
            with self.subTest(body=body), self.assertRaises(audit.AuditError):
                self.scan(body)

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


class ConversationBranchInventoryTests(unittest.TestCase):
    FUNCTION = """def register(self, router):
    for method, path, action in [('GET', '/one', 'list'), ('POST', '/two', 'create')]:
        if action == 'list':
            from .conversation_list_read import bind_conversation_list
            router.add(method, path, bind_conversation_list(self, router))
        else:
            router.add(method, path, lambda request, params, action=action: self._handle(action, request, params))
"""

    def scan(self, function=None, *, file='conversations.py', owner='ConversationService'):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            server = root / 'python/artex/server'
            server.mkdir(parents=True)
            body = self.FUNCTION if function is None else function
            (server / file).write_text("raise RuntimeError('never execute')\n" + f'class {owner}:\n' +
                                       ''.join('    ' + line + '\n' for line in body.splitlines()))
            return audit.python_inventory(root)[0]

    def test_exact_conversation_branch_uses_literal_rows_and_original_handler(self):
        rows = self.scan()
        self.assertEqual([(r['method'], r['path']) for r in rows], [('GET', '/one'), ('POST', '/two')])
        self.assertEqual(rows[0]['handler'], 'bind_conversation_list(self, router)')
        self.assertIn('self._handle(', rows[1]['handler'])
        self.assertTrue(all(r['function'] == 'ConversationService.register' for r in rows))
        self.assertEqual(self.scan(self.FUNCTION.replace("[('GET', '/one', 'list'), ('POST', '/two', 'create')]", '[]')), [])

    def test_other_file_owner_and_function_shape_fail_closed(self):
        for params in ({'file': 'other.py'}, {'owner': 'Other'}):
            with self.subTest(params=params), self.assertRaises(audit.AuditError):
                self.scan(**params)
        variants = [
            self.FUNCTION.replace('def register(self, router):', 'def register(self, router, action):'),
            self.FUNCTION.replace('def register(self, router):', 'async def register(self, router):'),
            self.FUNCTION.replace("if action == 'list':", "if action != 'list':"),
            self.FUNCTION.replace('bind_conversation_list(self, router)', 'bind_other(self, router)'),
            self.FUNCTION.replace('import bind_conversation_list', 'import bind_conversation_list as action'),
            self.FUNCTION.replace('    for method, path, action', "    path = '/seed'\n    for method, path, action"),
            self.FUNCTION + "    router.add('DELETE', '/after', handler)\n",
        ]
        for source in variants:
            with self.subTest(source=source), self.assertRaises(audit.AuditError):
                self.scan(source)

    def test_assignments_nested_loops_and_branch_transfers_fail_closed(self):
        insertions = (
            "path = '/replacement'", 'action = lookup()',
            'from module import action', 'def action(): pass',
            "for action in ['create']:\n                router.add('POST', '/nested', handler)",
            'break', 'continue', 'return',
        )
        for statement in insertions:
            for branch in ('if', 'else'):
                old = "        if action == 'list':\n" if branch == 'if' else '        else:\n'
                source = self.FUNCTION.replace(old, old + '            ' + statement + '\n')
                with self.subTest(statement=statement, branch=branch), self.assertRaises(audit.AuditError):
                    self.scan(source)

    def test_nonliteral_or_non_string_row_data_fail_closed(self):
        original = "[('GET', '/one', 'list'), ('POST', '/two', 'create')]"
        for rows in ('make_rows()', "[('GET', '/one')]", "[('GET', '/one', True)]", "[(method, '/one', 'list')]", "[('GET', '/one', 'list', 'extra')]"):
            with self.subTest(rows=rows), self.assertRaises(audit.AuditError):
                self.scan(self.FUNCTION.replace(original, rows))


class AlternativeGroupTests(unittest.TestCase):
    def declarations(self,key):
        name,members=audit.PYTHON_ALTERNATIVE_GROUPS[key]
        return [dict(method=key[0],path=key[1],source=audit._ALTERNATIVE_BASE+source,
                     function=owner,line=i+1,public=False) for i,(source,owner) in enumerate(members)]
    def test_exact_alternative_group_preserves_all_locations_unique_count(self):
        key=('GET','/api/exploration/findings');rows=self.declarations(key)
        grouped=audit.group_python_routes(rows)
        self.assertEqual(len(grouped),1);self.assertEqual(grouped[0]['declarations'],rows)
        self.assertIn('alternative_group',grouped[0])
    def test_unexpected_duplicates_still_fail(self):
        key=('GET','/api/stats');rows=self.declarations(key)
        variants=[rows+[dict(rows[0])],rows+[dict(rows[0],source='foreign.py')],
                  [dict(r,function='Other.register') for r in rows],
                  [dict(r,public=True) for r in rows],
                  [dict(r,path='/unexpected') for r in rows]]
        for variant in variants:
            with self.subTest(variant=variant),self.assertRaises(audit.AuditError):audit.group_python_routes(variant)
    def test_partial_duplicate_set_cannot_hide_missing_alternative(self):
        rows=self.declarations(('GET','/api/exploration/findings'))
        with self.assertRaises(audit.AuditError):audit.group_python_routes(rows[:2])

    def test_dynamic_alternatives_require_exact_owners_for_all_twenty_two_paths(self):
        self.assertEqual(len(audit.PYTHON_ALTERNATIVE_GROUPS),22)
        for key in audit.PYTHON_ALTERNATIVE_GROUPS:
            rows=self.declarations(key)
            self.assertEqual(sum(r['function']=='TaskRuntimeReadRouter.bind' for r in rows),1)
            self.assertEqual(len(audit.group_python_routes(rows)),1)
            with self.assertRaises(audit.AuditError):
                audit.group_python_routes(rows+[dict(rows[-1])])
            with self.assertRaises(audit.AuditError):
                audit.group_python_routes(rows[:-1]+[dict(rows[-1],function='Other.bind')])
            with self.assertRaises(audit.AuditError):
                audit.group_python_routes(rows[:-1]+[dict(rows[-1],public=True)])

class MetadataConditionalInventoryTests(unittest.TestCase):
    def scan(self,guard="'judge_usage' in services",path='/api/intercept/judge/usage',name='intercept_read_setup.py',owner='_InterceptReadApplication',tail=''):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);server=root/'python/artex/server';server.mkdir(parents=True)
            (server/name).write_text(f"class {owner}:\n    def __init__(self,services):\n        staged=Router(auth)\n        if {guard}:staged.add('GET','{path}',handler)\n"+tail)
            return audit.python_inventory(root)[0]
    def test_exact_typed_condition_is_optional_not_default_route(self):
        rows=self.scan();self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['registration_mode'],'optional explicit service registration')
        self.assertIn('independent global/source/raw',rows[0]['dependencies'])
    def test_unknown_condition_path_file_or_owner_fail_closed(self):
        cases=[dict(guard='enabled'),dict(guard="'judge_usage' not in services"),dict(guard="'judge_usage' in other"),dict(guard="'unknown' in services"),dict(path='/other'),dict(name='foreign.py'),dict(owner='Other'),dict(tail="        else:staged.add('GET','/other',handler)\n")]
        for case in cases:
            with self.subTest(case=case),self.assertRaises(audit.AuditError):self.scan(**case)
