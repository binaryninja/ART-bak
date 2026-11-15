"""Review regressions using authored Python port code and inert injected fakes.

No Go/upstream app, database, bcrypt, provider, subprocess, or network access.
These assert desired behavior and intentionally expose unresolved review findings.
"""
import io
import json
import unittest

from artex.server.auth import AuthService
from artex.server.wsgi import WSGIApplication


class ReviewStore:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def insert_if_absent(self, key, value):
        if key in self.values:
            return False
        self.values[key] = value
        return True

    def set(self, key, value):
        self.values[key] = value


class ReviewCodec:
    def hash(self, password):
        return 'review-test-only:' + password

    def verify(self, password, encoded):
        return encoded == self.hash(password)


class ReviewRequestTests(unittest.TestCase):
    def setUp(self):
        self.store = ReviewStore()
        self.app = WSGIApplication(AuthService(self.store, ReviewCodec(), b'r' * 32).router())

    def request(self, method, path, body=b''):
        result = []
        env = {'REQUEST_METHOD': method, 'PATH_INFO': path,
               'QUERY_STRING': '', 'CONTENT_LENGTH': str(len(body)),
               'wsgi.input': io.BytesIO(body)}
        output = b''.join(self.app(env, lambda status, headers: result.append((status, headers))))
        return int(result[0][0].split()[0]), output

    def test_head_public_health_matches_get_auth_policy(self):
        self.assertEqual(self.request('GET', '/api/health')[0], 200)
        status, body = self.request('HEAD', '/api/health')
        self.assertEqual(status, 200)
        self.assertEqual(body, b'')

    def test_invalid_utf8_password_is_client_error_without_write(self):
        body = json.dumps({'password': '\ud800' + 'a' * 7}).encode()
        status, _ = self.request('POST', '/api/auth/init', body)
        self.assertEqual(self.store.values, {})
        self.assertEqual(status, 400)


from dataclasses import replace
from datetime import datetime, timezone
from artex.db.tasks import Task
from artex.manager import TaskManager


def review_task(**changes):
    task = Task(id=1, name='review', category_id=None, category_name='',
                description='review', goal='', exploration_id=1, status='created',
                paused=False, queued=False, queued_at=None, queue_mode='',
                llm_profile_id=None, active_llm_profile_id=None, parent_ref='',
                pinned_at=None, created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                completed_at=None, timeout_seconds=0, plan_heartbeat_seconds=600,
                coverage_enabled=True, first_run_at=None, deadline_at=None)
    return replace(task, **changes)


class ReviewTaskRepository:
    def __init__(self, task):
        self.row = task
    def list(self):
        return [self.row]
    def update(self, task_id, **patch):
        self.row = replace(self.row, **{k: v for k, v in patch.items() if k == 'name'})
        if patch.get('pinned') is False:
            self.row = replace(self.row, pinned_at=None)
        return self.row
    def apply_pause(self, task_id):
        self.row = replace(self.row, paused=True, queued=False, queued_at=None)
        return self.row


def review_metrics(dto):
    return dict(last_activity_unix=0, tokens={}, goals_total=0, goals_met=0,
                in_flight=0, findings={})


class ReviewTaskBoundaryTests(unittest.TestCase):
    def manager(self, task):
        manager = TaskManager(ReviewTaskRepository(task), dto_enricher=review_metrics)
        manager.load_existing()
        return manager

    def test_unpinned_metadata_update_returns_after_persistence(self):
        manager = self.manager(review_task())
        result = manager.update_metadata('1', name='updated')
        self.assertEqual(result['name'], 'updated')
        self.assertFalse(result['pinned'])

    def test_pause_clears_cached_queue_timestamp(self):
        manager = self.manager(review_task(queued=True, queued_at=datetime.now(timezone.utc), queue_mode='bootstrap'))
        manager.pause('1')
        self.assertIsNone(manager.lifecycle_snapshot('1').get('queued_at'))

    def test_empty_dto_profile_and_company_lists_are_present(self):
        result = self.manager(review_task()).task('1')
        self.assertEqual(result['company_ids'], [])
        self.assertEqual(result['llm_profile_ids'], [])


if __name__ == '__main__':
    unittest.main()
