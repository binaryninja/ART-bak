"""Task HTTP tests with inert managers; no provider, subprocess or tool execution."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import unittest
from artex.server import AuthService, Request, sign_jwt
from artex.server.tasks import TaskService, parse_create_request, MAX_REQUEST_BYTES
from artex.manager import RuntimeUnavailable, TaskConflict, TaskNotFound
from artex.db.tasks import TaskInvalid


def public_task(task_id='1', status='created'):
    return {'id': task_id, 'exploration_id': 10, 'name': '', 'pinned': False,
            'description': 'Authorized local test', 'goal': 'Inspect fixtures',
            'status': status, 'created_at': '2026-11-15T00:00:00Z', 'created_unix': 1794700800,
            'completed_at': '', 'completed_unix': 0, 'last_activity_unix': 0,
            'paused': status == 'paused', 'queued': status == 'queued',
            'tokens': {'input_tokens': 0, 'output_tokens': 0, 'cache_read_tokens': 0, 'cache_write_tokens': 0},
            'goals_total': 0, 'goals_met': 0, 'in_flight': 0,
            'findings': {'critical': 0, 'high': 0, 'medium': 0, 'low': 0},
            'llm_profile_ids': [], 'llm_failover_state': '', 'source_task_ids': [],
            'company_ids': [], 'coverage_enabled': True}


class FakeManager:
    """Only records requested actions; never runs anything."""
    def __init__(self):
        self.tasks = {'1': public_task()}
        self.calls = []
        self.error = None
        self.runtime = True
    def list_tasks(self):
        return [deepcopy(x) for x in self.tasks.values()]
    def active_task_id(self):
        return '1'
    def task(self, task_id):
        return deepcopy(self.tasks.get(task_id))
    def create_task(self, description, goal, options=None, *, start=True, seed_first_intent=False):
        if not self.runtime:
            raise RuntimeUnavailable('Missing runner')
        if self.error:
            raise self.error
        if options.intercept_rules:
            raise NotImplementedError('Interception persistence unavailable')
        self.calls.append(('create', description, goal, options, start, seed_first_intent))
        task = public_task('2', 'running')
        task.update(description=description, goal=goal)
        return task
    def control_task(self, task_id, action):
        if self.error:
            raise self.error
        if not self.runtime:
            raise RuntimeUnavailable('Missing runner')
        before = self.tasks[task_id]['status']
        if action == 'resume' and before != 'paused':
            raise TaskConflict('Only paused tasks can resume')
        if action == 'pause' and before in ('paused', 'done', 'failed', 'timeout'):
            raise TaskConflict('Task cannot be paused')
        status = 'paused' if action == 'pause' else 'running'
        self.calls.append(('control', task_id, action))
        self.tasks[task_id].update(status=status, paused=action == 'pause')
        return {'id': task_id, 'paused': action == 'pause', 'queued': False, 'status': status}


class UnusedStore:
    def get(self, key): raise AssertionError('Unexpected password lookup')


class UnusedCodec:
    def hash(self, password): raise AssertionError('Unexpected password hashing')
    def verify(self, password, encoded): raise AssertionError('Unexpected password verification')


class TaskHTTPTests(unittest.TestCase):
    def setUp(self):
        self.key = b'k' * 32
        self.auth = AuthService(UnusedStore(), UnusedCodec(), self.key, clock=lambda: 1000)
        self.router = self.auth.router()
        self.manager = FakeManager()
        self.profile_ids = []
        def profile_available(identifier):
            self.profile_ids.append(identifier)
            return identifier in (4, 5)
        TaskService(self.manager, profile_available=profile_available).register(self.router)
        self.token = sign_jwt(self.key, now=1000)

    def request(self, method, target, data=None, *, body=None, token=True):
        return self.router.dispatch(Request(method, target,
            {'Authorization': 'Bearer '+self.token} if token else {},
            body if body is not None else json.dumps(data or {}).encode()))

    def test_routes_protected_before_any_action(self):
        for method, path in [('GET', '/api/tasks'), ('POST', '/api/tasks'), ('GET', '/api/tasks/1'), ('POST', '/api/tasks/1/control')]:
            self.assertEqual(self.request(method, path, {'action': 'pause'}, token=False).status, 401)
        self.assertEqual(self.manager.calls, [])
        self.assertEqual(self.profile_ids, [])

    def test_list_and_get_exact_envelopes(self):
        self.assertEqual(self.request('GET', '/api/tasks').json(), {'tasks': [public_task()], 'active': '1'})
        response = self.request('GET', '/api/tasks/1')
        self.assertEqual(response.status, 200)
        self.assertEqual(response.json(), public_task())
        self.assertEqual(response.headers['Content-Type'], 'application/json')
        self.assertEqual(self.request('GET', '/api/tasks/missing').status, 404)

    def test_create_always_requests_launch_not_silent_store_only(self):
        response = self.request('POST', '/api/tasks', {'description': 'Local fixture', 'goal': 'Inspect', 'seed_first_intent': True})
        self.assertEqual(response.status, 201)
        self.assertEqual(response.json()['status'], 'running')
        call = self.manager.calls[0]
        self.assertEqual(call[1:3], ('Local fixture', 'Inspect'))
        self.assertTrue(call[4])
        self.assertTrue(call[5])

    def test_default_description_and_normalized_options(self):
        result = self.request('POST', '/api/tasks', {'name': '  Example  ', 'description': '  ', 'timeout_seconds': -3,
            'plan_heartbeat_seconds': 1, 'coverage_enabled': False, 'company_ids': [2, 2, 3],
            'llm_profile_id': 4, 'source_task_ids': [' +001 ']})
        self.assertEqual(result.status, 201)
        call = self.manager.calls[0]
        self.assertEqual(call[1], 'Untitled task')
        options = call[3]
        self.assertEqual(options.name, 'Example')
        self.assertEqual(options.timeout_seconds, 0)
        self.assertEqual(options.plan_heartbeat_seconds, 1)  # Store clamps to 600.
        self.assertFalse(options.coverage_enabled)
        self.assertEqual(list(options.company_ids), [2, 3])
        self.assertEqual(list(options.llm_profile_ids), [4])
        self.assertEqual(list(options.source_task_ids), [1])
        self.assertFalse(call[5])

    def test_profile_chain_preserves_order_and_overrides_legacy(self):
        response = self.request('POST', '/api/tasks', {'llm_profile_ids': [5, 4], 'llm_profile_id': 99})
        self.assertEqual(response.status, 201)
        self.assertEqual(self.profile_ids, [5, 4])
        self.assertEqual(list(self.manager.calls[0][3].llm_profile_ids), [5, 4])

    def test_profile_existence_and_missing_resolver(self):
        self.assertEqual(self.request('POST', '/api/tasks', {'llm_profile_ids': [99]}).status, 400)
        router = self.auth.router()
        TaskService(self.manager).register(router)
        result = router.dispatch(Request('POST', '/api/tasks', {'Authorization': 'Bearer '+self.token}, b'{"llm_profile_ids":[4]}'))
        self.assertEqual(result.status, 503)
        self.assertEqual(self.manager.calls, [])

    def test_sources_must_exist_and_duplicates_rejected(self):
        for sources in (['9'], ['1', '01'], ['0'], ['abc'], [str(2**63)], ['1']*9, [1], [None]):
            self.assertEqual(self.request('POST', '/api/tasks', {'source_task_ids': sources}).status, 400)
        self.assertEqual(self.manager.calls, [])

    def test_company_limits_deduplicate_and_require_positive_ids(self):
        for values in ([0], [-1], [True], ['2'], list(range(1,34))):
            self.assertEqual(self.request('POST', '/api/tasks', {'company_ids': values}).status, 400)
        self.assertEqual(self.request('POST', '/api/tasks', {'company_ids': [2]*40}).status, 201)
        self.assertEqual(list(self.manager.calls[0][3].company_ids), [2])

    def test_type_and_int64_validation(self):
        for field, value in [('description', 1), ('goal', []), ('name', True), ('timeout_seconds', True),
                             ('timeout_seconds', 1.0), ('timeout_seconds', 2**63), ('plan_heartbeat_seconds', '1'),
                             ('category_id', 0), ('category_id', -1), ('coverage_enabled', 0), ('seed_first_intent', 'true'),
                             ('llm_profile_ids', [4, 4]), ('llm_profile_ids', [0]), ('llm_profile_ids', '4')]:
            with self.subTest(field=field, value=value):
                self.assertEqual(self.request('POST', '/api/tasks', {field: value}).status, 400)
        self.assertEqual(self.manager.calls, [])

    def test_optional_nulls_have_go_zero_value_semantics(self):
        fields = ('name', 'description', 'goal', 'llm_profile_id', 'llm_profile_ids', 'source_task_ids', 'company_ids',
                  'timeout_seconds', 'plan_heartbeat_seconds', 'coverage_enabled', 'seed_first_intent', 'intercept_rules')
        self.assertEqual(self.request('POST', '/api/tasks', dict.fromkeys(fields)).status, 201)

    def test_invalid_and_oversize_json(self):
        for body in (b'{', b'[]', b'null', b'{"timeout_seconds":NaN}', b'{}{}', b'{"name":"\\ud800"}', '{}'.encode('utf-16'), '{}'.encode('utf-32')):
            self.assertEqual(self.request('POST', '/api/tasks', body=body).status, 400)
        self.assertEqual(self.request('POST', '/api/tasks', body=b' '*(MAX_REQUEST_BYTES+1)).status, 413)
        self.assertEqual(self.manager.calls, [])

    def test_interception_scope_is_validated_and_not_dropped(self):
        rule = {'enabled': True, 'kind': 'cidr', 'pattern': ' 192.168.1.1/24 ', 'note': 'Local scope'}
        parsed = parse_create_request({'intercept_rules': [rule]})
        self.assertEqual(parsed.options.intercept_rules[0], {**rule, 'pattern': '192.168.1.1/24', 'action': 'block'})
        result = self.request('POST', '/api/tasks', {'intercept_rules': [rule]})
        self.assertEqual(result.status, 503)
        self.assertEqual(self.manager.calls, [])
        for bad in ({**rule, 'action': 'skip'}, {**rule, 'pattern': ''}, {**rule, 'pattern': 'bad'},
                    {**rule, 'kind': 'unknown'}, {**rule, 'kind': 'exact_ip', 'pattern': 'fe80::1%eth0'},
                    {**rule, 'enabled': 'yes'}, {**rule, 'pattern': '192.168.1.0/255.255.255.0'}):
            self.assertEqual(self.request('POST', '/api/tasks', {'intercept_rules': [bad]}).status, 400)

    def test_pause_and_resume_exact_control_response(self):
        self.manager.tasks['1']['status'] = 'running'
        response = self.request('POST', '/api/tasks/1/control', {'action': 'pause'})
        self.assertEqual(response.json(), {'id': '1', 'paused': True, 'queued': False, 'status': 'paused'})
        response = self.request('POST', '/api/tasks/1/control', {'action': 'resume'})
        self.assertEqual(response.json(), {'id': '1', 'paused': False, 'queued': False, 'status': 'running'})
        self.assertEqual(self.manager.calls, [('control', '1', 'pause'), ('control', '1', 'resume')])

    def test_invalid_control_and_unknown_task_do_not_mutate(self):
        for action in ('stop', 'delete', 'cancel', '', True):
            self.assertEqual(self.request('POST', '/api/tasks/1/control', {'action': action}).status, 400)
        self.assertEqual(self.request('POST', '/api/tasks/missing/control', body=b'{').status, 404)
        self.assertEqual(self.manager.calls, [])

    def test_resume_requires_paused_and_terminal_pause_is_conflict(self):
        self.assertEqual(self.request('POST', '/api/tasks/1/control', {'action': 'resume'}).status, 409)
        for status in ('done', 'failed', 'timeout', 'paused'):
            self.manager.tasks['1']['status'] = status
            self.assertEqual(self.request('POST', '/api/tasks/1/control', {'action': 'pause'}).status, 409)
        self.assertEqual(self.manager.calls, [])

    def test_unavailable_runtime_never_claims_started_or_paused(self):
        self.manager.runtime = False
        self.assertEqual(self.request('POST', '/api/tasks').status, 503)
        self.assertEqual(self.request('POST', '/api/tasks/1/control', {'action': 'pause'}).status, 503)
        self.assertEqual(self.manager.calls, [])
        self.assertEqual(self.manager.tasks['1']['status'], 'created')

    def test_error_mapping_and_redaction(self):
        for error, expected in ((TaskInvalid('bad scope'), 400), (TaskConflict('busy'), 409),
                                (TaskNotFound('gone'), 404), (RuntimeUnavailable('no runner'), 503),
                                (RuntimeError('secret credentials'), 500)):
            self.manager.error = error
            result = self.request('POST', '/api/tasks')
            self.assertEqual(result.status, expected)
            self.assertEqual(set(result.json()), {'error'})
            self.assertNotIn(b'secret', result.body)

    def test_missing_manager_requires_auth_then_503(self):
        router = self.auth.router()
        TaskService(None).register(router)
        self.assertEqual(router.dispatch(Request('GET', '/api/tasks')).status, 401)
        self.assertEqual(router.dispatch(Request('GET', '/api/tasks', {'Authorization': 'Bearer '+self.token})).status, 503)

    def test_real_store_manager_read_slice_over_fake_executor(self):
        from artex.db.tasks import TaskStore
        from artex.manager import TaskManager
        timestamp = datetime(2026, 11, 15, tzinfo=timezone.utc)
        class Executor:
            def __init__(self): self.calls = []
            def fetch_all(self, query):
                self.calls.append(query)
                if 'FROM tasks WHERE deleted_at IS NULL' in query.sql:
                    return [(1, 'Fixture', None, '', 'Local fixture', 'Inspect', 10, 'created', False,
                             False, None, '', None, None, 'private-parent', None, timestamp, None,
                             0, 600, True, None, None)]
                return []
        metrics = {'last_activity_unix': 0, 'tokens': {'input_tokens': 11, 'output_tokens': 3,
                    'cache_read_tokens': 0, 'cache_write_tokens': 0}, 'goals_total': 2, 'goals_met': 1,
                   'in_flight': 0, 'findings': {'critical': 0, 'high': 1, 'medium': 0, 'low': 0}}
        executor = Executor()
        manager = TaskManager(TaskStore(executor), dto_enricher=lambda dto: {**dto, **deepcopy(metrics)})
        self.assertEqual(manager.load_existing(), 1)
        router = self.auth.router()
        TaskService(manager).register(router)
        headers = {'Authorization': 'Bearer '+self.token}
        result = router.dispatch(Request('GET', '/api/tasks', headers))
        self.assertEqual(result.status, 200)
        self.assertEqual(result.json()['active'], '1')
        task = result.json()['tasks'][0]
        self.assertEqual(task['id'], '1')
        self.assertEqual(task['tokens']['input_tokens'], 11)
        self.assertEqual(task['goals_met'], 1)
        self.assertEqual(task['status'], 'created')
        for private in ('parent_ref', 'llm_chain_revision', 'queue_mode', 'timeout_seconds', 'deadline_at'):
            self.assertNotIn(private, task)
        self.assertEqual(router.dispatch(Request('GET', '/api/tasks/1', headers)).json(), task)
        before = len(executor.calls)
        # Creation requires a real injected runner; no SQL writes are attempted.
        result = router.dispatch(Request('POST', '/api/tasks', headers, b'{}'))
        self.assertEqual(result.status, 503)
        self.assertEqual(len(executor.calls), before)
        # Missing metrics does not silently substitute fabricated zero values.
        manager.dto_enricher = None
        self.assertEqual(router.dispatch(Request('GET', '/api/tasks/1', headers)).status, 503)
        self.assertEqual(router.dispatch(Request('GET', '/api/tasks', headers)).status, 503)

    def test_real_manager_pause_waits_for_cancellation_acknowledgement(self):
        from artex.manager import TaskManager
        class Repository:
            def __init__(self):
                self.row = {**public_task(), 'created_at': datetime(2026, 11, 15, tzinfo=timezone.utc)}
                self.pause_writes = 0
            def list(self): return [deepcopy(self.row)]
            def apply_admission(self, task_id, *args):
                self.row.update(status='running', paused=False, queued=False)
                return deepcopy(self.row)
            def apply_pause(self, task_id):
                self.pause_writes += 1
                self.row.update(paused=True, queued=False)
                return deepcopy(self.row)
        class Handle:
            acknowledged = False
            def cancel(self, reason): self.reason = reason
            def wait(self, timeout): return self.acknowledged
        class Runner:
            def __init__(self): self.handle = Handle()
            def ready(self, task): return True
            def start(self, task, token):
                self.token = token
                return self.handle
        repo, runner = Repository(), Runner()
        metrics = {key: public_task()[key] for key in ('last_activity_unix', 'tokens', 'goals_total', 'goals_met', 'in_flight', 'findings')}
        manager = TaskManager(repo, runner=runner, dto_enricher=lambda dto: {**dto, **deepcopy(metrics)})
        manager.load_existing()
        self.assertTrue(manager.start('1'))
        router = self.auth.router()
        TaskService(manager).register(router)
        request = Request('POST', '/api/tasks/1/control', {'Authorization': 'Bearer '+self.token}, b'{"action":"pause"}')
        result = router.dispatch(request)
        self.assertEqual(result.status, 409)
        self.assertEqual(repo.pause_writes, 0)
        self.assertTrue(runner.token.cancelled)
        self.assertEqual(runner.handle.reason, 'paused_by_user')
        runner.handle.acknowledged = True
        result = router.dispatch(request)
        self.assertEqual(result.status, 200)
        self.assertEqual(result.json(), {'id': '1', 'paused': True, 'queued': False, 'status': 'paused'})
        self.assertEqual(repo.pause_writes, 1)

    def test_no_invented_stop_or_status_route(self):
        self.assertEqual(self.request('POST', '/api/tasks/1/stop').status, 404)
        self.assertEqual(self.request('GET', '/api/tasks/1/status').status, 404)
        self.assertEqual(self.request('DELETE', '/api/tasks/1').status, 405)


if __name__ == '__main__':
    unittest.main()
