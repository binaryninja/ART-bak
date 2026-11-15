"""Router-level task-category contracts; no database, processes or network."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import unittest
from artex.server import AuthService, Request, sign_jwt
from artex.server.task_categories import TaskCategoryService, normalize_task_ids, MAX_REQUEST_BYTES
from artex.db.task_categories import (TaskCategoryStore, CategoryInvalid,
                                      CategoryNameConflict, CategoryNotFound,
                                      CategoryTaskNotFound)


class Category:
    def __init__(self, id=7, name='Research'):
        self.id, self.name = id, name
    def to_dict(self):
        return {'id': self.id, 'name': self.name, 'task_count': 0,
                'created_at': '2026-11-15T00:00:00Z', 'updated_at': '2026-11-15T00:00:00Z'}


class Repository:
    def __init__(self):
        self.calls = []
        self.error = None
    def list(self):
        self.calls.append(('list',))
        if self.error:
            raise self.error
        return [Category()]
    def create(self, name):
        self.calls.append(('create', name))
        if self.error:
            raise self.error
        return Category(name=' '.join(name.split()))


class Manager:
    """Only a test double for the required live-manager atomicity contract."""
    def __init__(self):
        self.tasks = {'1': {'id': '1', 'status': 'running', 'category_id': None, 'category_name': ''},
                      '2': {'id': '2', 'status': 'paused', 'category_id': None, 'category_name': ''}}
        self.calls = []
        self.error = None
        self.disappear = set()
    def task(self, task_id):
        return deepcopy(self.tasks.get(task_id))
    def check(self):
        if self.error:
            raise self.error
    def rename(self, category_id, name):
        self.calls.append(('rename', category_id, name))
        self.check()
        for task in self.tasks.values():
            if task['category_id'] == category_id:
                task['category_name'] = name
        return Category(category_id, name)
    def delete(self, category_id):
        self.calls.append(('delete', category_id))
        self.check()
        if category_id != 7:
            return False
        for task in self.tasks.values():
            if task['category_id'] == category_id:
                task.update(category_id=None, category_name='')
        return True
    def set_task(self, task_id, category_id):
        self.calls.append(('set_task', task_id, category_id))
        self.check()
        if task_id in self.disappear:
            self.tasks.pop(task_id, None)
            raise CategoryTaskNotFound('task not found')
        category = Category(category_id) if category_id is not None else None
        self.tasks[task_id].update(category_id=category_id, category_name=category.name if category else '')
        return category
    def set_tasks(self, task_ids, category_id):
        self.calls.append(('set_tasks', list(task_ids), category_id))
        self.check()
        category = Category(category_id) if category_id is not None else None
        updated = []
        for task_id in task_ids:
            if task_id not in self.disappear:
                self.tasks[task_id].update(category_id=category_id, category_name=category.name if category else '')
                updated.append(task_id)
        return updated, category


class UnusedPasswordStore:
    def get(self, key):
        raise AssertionError('Category authorization must not read passwords')


class UnusedPasswordCodec:
    def hash(self, password):
        raise AssertionError('No credential mutation permitted')
    def verify(self, password, encoded):
        raise AssertionError('No password verification expected')


class TaskCategoryHTTPTests(unittest.TestCase):
    def setUp(self):
        self.key = b'k' * 32
        self.auth = AuthService(UnusedPasswordStore(), UnusedPasswordCodec(), self.key, clock=lambda: 1000)
        self.router = self.auth.router()
        self.repo, self.manager = Repository(), Manager()
        self.service = TaskCategoryService(self.repo, self.manager)
        self.service.register(self.router)
        self.token = sign_jwt(self.key, now=1000)

    def request(self, method, path, data=None, *, body=None, token=True):
        return self.router.dispatch(Request(method, path,
            {'Authorization': 'Bearer ' + self.token} if token else {},
            body if body is not None else json.dumps(data or {}).encode()))

    def test_all_six_routes_require_auth_before_side_effects(self):
        for method, path in [('GET', '/api/task-categories'), ('POST', '/api/task-categories'),
                             ('PATCH', '/api/task-categories/7'), ('DELETE', '/api/task-categories/7'),
                             ('PATCH', '/api/tasks/1/category'), ('POST', '/api/tasks/category/batch')]:
            response = self.request(method, path, {'name': 'x', 'category_id': 7, 'task_ids': ['1']}, token=False)
            self.assertEqual(response.status, 401)
        self.assertEqual(self.repo.calls, [])
        self.assertEqual(self.manager.calls, [])

    def test_list_envelope_and_create_status(self):
        response = self.request('GET', '/api/task-categories')
        self.assertEqual(response.status, 200)
        self.assertEqual(response.json(), {'categories': [Category().to_dict()]})
        self.assertEqual(response.headers['Content-Type'], 'application/json')
        response = self.request('POST', '/api/task-categories', {'name': '  My research  '})
        self.assertEqual(response.status, 201)
        self.assertEqual(response.json()['name'], 'My research')
        self.assertNotIn('nkey', response.json())

    def test_create_name_validation(self):
        for data in ({}, {'name': None}, {'name': 3}, {'name': ''}, {'name': '  '}, {'name': 'a' * 81}):
            with self.subTest(data=data):
                self.assertEqual(self.request('POST', '/api/task-categories', data).status, 400)
        self.assertEqual(self.repo.calls, [])
        # Count Unicode code points, not UTF-8 bytes; exactly 80 is valid.
        self.assertEqual(self.request('POST', '/api/task-categories', {'name': '\U0001f642' * 80}).status, 201)

    def test_invalid_json_types_and_trailing_data(self):
        for body in (b'[]', b'null', b'{', b'{"name":NaN}', b'{"name":"a"}{}', b'{"name":"\\ud800"}'):
            self.assertEqual(self.request('POST', '/api/task-categories', body=body).status, 400)
        self.assertEqual(self.repo.calls, [])

    def test_body_limit_statuses(self):
        huge = b' ' * (MAX_REQUEST_BYTES + 1)
        for method, path in [('POST', '/api/task-categories'), ('PATCH', '/api/task-categories/7'),
                             ('POST', '/api/tasks/category/batch')]:
            self.assertEqual(self.request(method, path, body=huge).status, 413)
        self.assertEqual(self.request('PATCH', '/api/tasks/1/category', body=huge).status, 400)
        self.assertEqual(self.repo.calls, [])
        self.assertEqual(self.manager.calls, [])

    def test_exact_body_limit(self):
        base = b'{"name":"a"}'
        self.assertEqual(self.request('POST', '/api/task-categories', body=base + b' ' * (MAX_REQUEST_BYTES-len(base))).status, 201)

    def test_repository_error_mapping(self):
        for error, code in [(CategoryNameConflict('conflict'), 409), (CategoryInvalid('invalid task category'), 400),
                            (CategoryNotFound('task category not found'), 404), (RuntimeError('secret password'), 500)]:
            self.repo.error = error
            result = self.request('POST', '/api/task-categories', {'name': 'a'})
            self.assertEqual(result.status, code)
            self.assertEqual(set(result.json()), {'error'})
            self.assertNotIn('secret', result.json()['error'])

    def test_rename_delete_refresh_manager(self):
        self.manager.tasks['1']['category_id'] = 7
        result = self.request('PATCH', '/api/task-categories/+007', {'name': 'New'})
        self.assertEqual(result.status, 200)
        self.assertEqual(self.manager.tasks['1']['category_name'], 'New')
        self.assertEqual(self.request('DELETE', '/api/task-categories/7').json(), {'deleted': 7})
        self.assertIsNone(self.manager.tasks['1']['category_id'])
        self.assertEqual(self.request('DELETE', '/api/task-categories/8').status, 404)
        self.assertEqual(self.repo.calls, [])

    def test_invalid_route_ids_never_reach_manager(self):
        for value in ('x', '1.0', '9223372036854775808', ' 1', '--1', '\u0661'):
            self.assertEqual(self.request('DELETE', '/api/task-categories/' + value).status, 400)
        self.assertEqual(self.manager.calls, [])

    def test_single_move_returns_full_task_dto_without_changing_status(self):
        response = self.request('PATCH', '/api/tasks/1/category', {'category_id': 7})
        self.assertEqual(response.status, 200)
        self.assertEqual(response.json(), {'id': '1', 'status': 'running', 'category_id': 7, 'category_name': 'Research'})
        response = self.request('PATCH', '/api/tasks/1/category', {'category_id': None})
        self.assertIsNone(response.json()['category_id'])
        self.assertEqual(response.json()['status'], 'running')

    def test_single_unknown_task_precedes_body_validation(self):
        self.assertEqual(self.request('PATCH', '/api/tasks/missing/category', body=b'{').status, 404)
        self.assertEqual(self.manager.calls, [])

    def test_missing_and_invalid_category_id(self):
        for path, method, base in [('/api/tasks/1/category', 'PATCH', {}),
                                   ('/api/tasks/category/batch', 'POST', {'task_ids': ['1']})]:
            response = self.request(method, path, base)
            self.assertEqual(response.json(), {'error': 'category_id is required'})
            for value in (True, False, 0, -1, 1.0, '7', 2**63, [], {}):
                self.assertEqual(self.request(method, path, {**base, 'category_id': value}).status, 400)
        self.assertEqual(self.manager.calls, [])

    def test_batch_mixed_invalid_unknown_and_duplicate_ids(self):
        response = self.request('POST', '/api/tasks/category/batch',
            {'task_ids': [' 001 ', '+1', 'bad', '99', 'bad', '2'], 'category_id': 7})
        self.assertEqual(response.status, 200)
        self.assertEqual(response.json()['items'], [
            {'id': '1', 'ok': True}, {'id': 'bad', 'ok': False, 'error': 'bad task id'},
            {'id': '99', 'ok': False, 'error': 'task not found'}, {'id': '2', 'ok': True}])
        self.assertEqual(self.manager.calls, [('set_tasks', ['1', '2'], 7)])
        self.assertEqual(response.json()['category']['id'], 7)

    def test_batch_clearing_and_disappeared_task(self):
        self.manager.disappear = {'2'}
        response = self.request('POST', '/api/tasks/category/batch', {'task_ids': ['1', '2'], 'category_id': None})
        self.assertIsNone(response.json()['category'])
        self.assertEqual(response.json()['items'][1], {'id': '2', 'ok': False, 'error': 'task not found'})

    def test_batch_no_live_tasks_does_not_call_database(self):
        result = self.request('POST', '/api/tasks/category/batch', {'task_ids': ['99', 'abc', '99'], 'category_id': 123})
        self.assertEqual(result.status, 200)
        self.assertIsNone(result.json()['category'])
        self.assertEqual(len(result.json()['items']), 2)
        self.assertEqual(self.manager.calls, [])

    def test_batch_selection_limits_and_types(self):
        for ids in ([], None, '1', [1], [True], [None], [str(i) for i in range(101)]):
            self.assertEqual(self.request('POST', '/api/tasks/category/batch', {'task_ids': ids, 'category_id': None}).status, 400)
        # The limit applies after normalization, as in Go.
        self.assertEqual(self.request('POST', '/api/tasks/category/batch', {'task_ids': ['1'] * 101, 'category_id': None}).status, 200)

    def test_manager_failure_does_not_report_partial_success(self):
        self.manager.error = CategoryNotFound('task category not found')
        result = self.request('POST', '/api/tasks/category/batch', {'task_ids': ['1', '2'], 'category_id': 99})
        self.assertEqual(result.status, 404)
        self.assertEqual(set(result.json()), {'error'})
        self.assertIsNone(self.manager.tasks['1']['category_id'])

    def test_unconfigured_dependencies_fail_closed(self):
        router = self.auth.router()
        TaskCategoryService(None).register(router)
        for method, path in [('GET', '/api/task-categories'), ('POST', '/api/task-categories'),
                             ('PATCH', '/api/task-categories/7'), ('DELETE', '/api/task-categories/7'),
                             ('PATCH', '/api/tasks/1/category'), ('POST', '/api/tasks/category/batch')]:
            result = router.dispatch(Request(method, path, {'Authorization': 'Bearer ' + self.token}, b'{}'))
            self.assertEqual(result.status, 503)
        self.assertEqual(router.dispatch(Request('GET', '/api/task-categories')).status, 401)

    def test_wrong_method_and_batch_route_precedence(self):
        self.assertEqual(self.request('GET', '/api/tasks/category/batch').status, 405)
        self.assertEqual(self.request('GET', '/api/task-categories/7').status, 405)

    def test_real_store_adapter_list_and_create_with_bound_sql(self):
        class Executor:
            def __init__(self):
                self.calls = []
            def fetch_all(self, query):
                self.calls.append(query)
                timestamp = datetime(2026, 11, 15, tzinfo=timezone.utc)
                name = query.params[0] if query.params else 'Research'
                return [(7, name, name.lower(), 0, timestamp, timestamp)]
        executor = Executor()
        router = self.auth.router()
        TaskCategoryService(TaskCategoryStore(executor)).register(router)
        headers = {'Authorization': 'Bearer ' + self.token}
        response = router.dispatch(Request('GET', '/api/task-categories', headers))
        self.assertEqual(response.status, 200)
        self.assertEqual(response.json()['categories'][0]['created_at'], '2026-11-15T00:00:00+00:00')
        response = router.dispatch(Request('POST', '/api/task-categories', headers,
                                   json.dumps({'name': "  Research's   tests "}).encode()))
        self.assertEqual(response.status, 201)
        query = executor.calls[-1]
        self.assertEqual(query.params, ("Research's tests", "research's tests"))
        self.assertNotIn("Research's tests", query.sql)
        self.assertNotIn('nkey', response.json())
        # A repository alone cannot safely refresh a live manager after a rename.
        self.assertEqual(router.dispatch(Request('PATCH', '/api/task-categories/7', headers,
                                                b'{"name":"new"}')).status, 503)

    def test_canonicalization_int64_boundaries(self):
        self.assertEqual(normalize_task_ids(['+01', '1', '0', '-1', str(2**63-1), str(2**63)]),
                         [('1', True), ('0', False), ('-1', False), (str(2**63-1), True), (str(2**63), False)])


if __name__ == '__main__':
    unittest.main()
