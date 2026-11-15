"""Read-grant revocation after SQL must never disclose synthetic private rows."""
import unittest
import test_task_scope_http as scope_fixture
import test_intercept_rules_http as rule_fixture


class Review(unittest.TestCase):
    def test_task_scope_request_grant_revoked_by_fetch(self):
        f=scope_fixture.Tests();f.setUp();allowed=[True]
        f.router=f.make(authorize=lambda *a:allowed[0])
        f.db.items=[({'id':2,'task_id':5,'kind':'keyword','value':'private-marker','source':'manual'},1)]
        original=f.db.fetch_all
        def fetch(q):
            rows=original(q)
            if 'scope_items' in q.sql:allowed[0]=False
            return rows
        f.db.fetch_all=fetch
        response=f.call();self.assertEqual(response.status,403);self.assertNotIn(b'private-marker',response.body)

    def test_rule_request_grant_revoked_by_fetch(self):
        f=rule_fixture.HTTPRuleTests();f.make([[(5,)],[rule_fixture.ROW]])
        allowed=[True];f.service.authorize=lambda *a:allowed[0]
        original=f.f.db.fetch_all
        def fetch(q):
            rows=original(q)
            if 'WITH selected' in q.sql:allowed[0]=False
            return rows
        f.f.db.fetch_all=fetch
        response=f.call('list');self.assertEqual(response.status,403);self.assertNotIn(b'example.test',response.body)

    def test_rule_store_grant_revoked_by_fetch(self):
        f=rule_fixture.HTTPRuleTests();f.make([[(5,)],[rule_fixture.ROW]])
        allowed=[True];f.store.authorize=lambda *a:allowed[0]
        original=f.f.db.fetch_all
        def fetch(q):
            rows=original(q)
            if 'WITH selected' in q.sql:allowed[0]=False
            return rows
        f.f.db.fetch_all=fetch
        response=f.call('list');self.assertEqual(response.status,403);self.assertNotIn(b'example.test',response.body)

    def test_scope_disclosure_check_is_after_serialization(self):
        from unittest.mock import patch
        import artex.server.task_scope as module
        f=scope_fixture.Tests();f.setUp();allowed=[True]
        f.router=f.make(authorize=lambda *a:allowed[0])
        f.db.items=[({'id':2,'task_id':5,'kind':'keyword','value':'private-marker','source':'manual'},1)]
        original=module.json_response
        def serialize(*args,**kwargs):
            response=original(*args,**kwargs);allowed[0]=False;return response
        with patch.object(module,'json_response',serialize):response=f.call()
        self.assertEqual(response.status,403);self.assertNotIn(b'private-marker',response.body)

    def test_rule_disclosure_checks_store_after_serialization(self):
        from unittest.mock import patch
        import artex.server.intercept_rules as module
        f=rule_fixture.HTTPRuleTests();f.make([[(5,)],[rule_fixture.ROW]])
        allowed=[True];f.store.authorize=lambda *a:allowed[0]
        original=module.json_response
        def serialize(*args,**kwargs):
            response=original(*args,**kwargs);allowed[0]=False;return response
        with patch.object(module,'json_response',serialize):response=f.call('list')
        self.assertEqual(response.status,403);self.assertNotIn(b'example.test',response.body)

    def test_direct_rule_store_revoked_after_transaction(self):
        from contextlib import contextmanager
        from artex.db.intercept_rules import RuleDenied
        f=rule_fixture.HTTPRuleTests();f.make([[(5,)],[rule_fixture.ROW]])
        allowed=[True];f.store.authorize=lambda *a:allowed[0]
        original=f.f.db.transaction
        @contextmanager
        def transaction():
            with original() as tx:yield tx
            allowed[0]=False
        f.f.db.transaction=transaction
        with self.assertRaises(RuleDenied):f.store.list()

    def test_direct_scope_store_requires_read_authority_not_entry_approval(self):
        from artex.db.task_scope import ScopeUnavailable
        f=scope_fixture.Tests();f.setUp()
        with self.assertRaises(ScopeUnavailable):f.store.list_bounded()
        self.assertEqual(f.db.calls,[])

    def test_direct_scope_store_final_read_grant(self):
        from artex.db.task_scope import ScopeDenied
        f=scope_fixture.Tests();f.setUp();allowed=[True]
        f.store.read_authorize=lambda task,exp:allowed[0]
        f.db.items=[({'id':2,'task_id':5,'kind':'keyword','value':'private-marker','source':'manual'},1)]
        original=f.db.fetch_all
        def fetch(q):
            rows=original(q)
            if 'scope_items' in q.sql:allowed[0]=False
            return rows
        f.db.fetch_all=fetch
        with self.assertRaises(ScopeDenied):f.store.list_bounded()

    def test_scope_http_final_check_does_not_reenter_guard(self):
        from contextlib import contextmanager
        f=scope_fixture.Tests();f.setUp();entered=[False]
        @contextmanager
        def guard(binding):
            if entered[0]:raise AssertionError('Nested lifecycle acquisition')
            entered[0]=True
            try:yield
            finally:entered[0]=False
        f.router=f.make(lifecycle_guard=guard)
        self.assertEqual(f.call().status,200)

    def test_scope_http_policy_replacement_during_fetch_denied(self):
        f=scope_fixture.Tests();f.setUp();original=f.db.fetch_all
        def fetch(q):
            rows=original(q)
            if 'scope_items' in q.sql:f.service.authorize=lambda *a:True
            return rows
        f.db.fetch_all=fetch
        self.assertEqual(f.call().status,403)

    def test_scope_direct_policy_replacement_during_fetch_denied(self):
        from artex.db.task_scope import ScopeDenied
        f=scope_fixture.Tests();f.setUp();f.store.read_authorize=lambda *a:True
        original=f.db.fetch_all
        def fetch(q):
            rows=original(q)
            if 'scope_items' in q.sql:f.store.read_authorize=lambda *a:True
            return rows
        f.db.fetch_all=fetch
        with self.assertRaises(ScopeDenied):f.store.list_bounded()

    def test_rule_direct_policy_replacement_during_fetch_denied(self):
        from artex.db.intercept_rules import RuleDenied
        f=rule_fixture.HTTPRuleTests();f.make([[(5,)],[rule_fixture.ROW]])
        original=f.f.db.fetch_all
        def fetch(q):
            rows=original(q)
            if 'WITH selected' in q.sql:f.store.authorize=lambda *a:True
            return rows
        f.f.db.fetch_all=fetch
        with self.assertRaises(RuleDenied):f.store.list()
