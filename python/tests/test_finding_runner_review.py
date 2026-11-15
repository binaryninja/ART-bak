"""Independent inert finding scope/version regressions; no database or tools."""
from contextlib import contextmanager
import unittest
from artex.db.findings import FindingStore, FindingFilter, FindingInvalid, EvidenceConflict


class Executor:
    def __init__(self):
        self.queries = []
        self.events = []
    def fetch_one(self, query):
        self.queries.append(query)
        if 'evidence_version FROM' in query.sql: return (4,)
        if 'count(*)' in query.sql: return (0,)
        return (None,)
    def fetch_all(self, query):
        self.queries.append(query)
        if 'SELECT f.id, f.task_id' in query.sql: return [(8,7)]
        if 'SELECT deleted_at' in query.sql: return [(None,)]
        return []
    def execute(self, query):
        self.queries.append(query)
        return 1
    @contextmanager
    def transaction(self):
        self.events.append('begin')
        try: yield self
        except Exception:
            self.events.append('rollback')
            raise
        else: self.events.append('commit')


class FindingRunnerReviewTests(unittest.TestCase):
    def test_malformed_direct_store_task_filter_never_becomes_global(self):
        for value in ('oops', '0', '-1', '9223372036854775808', '1 OR 1=1'):
            executor = Executor()
            with self.subTest(value=value):
                with self.assertRaises((FindingInvalid,ValueError)):
                    FindingStore(executor).list_page(FindingFilter(task_id=value))
                self.assertEqual(executor.queries, [])

    def test_bound_store_scope_survives_or_search_filter(self):
        executor = Executor()
        FindingStore(executor,task_id=7,exploration_id=12).list_page(FindingFilter(query='x%_'))
        query = executor.queries[0]
        self.assertIn('f.task_id=$1',query.sql)
        self.assertIn('scope_node.exploration_id=$2',query.sql)
        self.assertIn("AND (COALESCE(f.name",query.sql)
        self.assertEqual(query.params,(7,12,'%x\\%\\_%'))

    def test_report_stale_version_rolls_back_without_update(self):
        executor = Executor()
        with self.assertRaises(EvidenceConflict):
            FindingStore(executor,task_id=7).set_report_by_node_id(3,'fixture',version=2)
        self.assertEqual(executor.events,['begin','rollback'])
        self.assertEqual(executor.queries[0].params,(7337741004,))
        sqls = [q.sql for q in executor.queries]
        self.assertLess(next(i for i,s in enumerate(sqls) if 'tasks WHERE' in s),
                        next(i for i,s in enumerate(sqls) if 'evidence_version FROM' in s))
        self.assertFalse(any(s.startswith('UPDATE findings') for s in sqls))


class ProfileBoundaryReviewTests(unittest.TestCase):
    def test_redacted_public_url_cannot_overwrite_private_configuration(self):
        from artex.db.profiles import LLMProfile, ProfileStore, ProfileInvalid
        private = LLMProfile(id=7,name='fixture',model='fixture',
                             base_url='https://example.test/v1?token=fixture-secret',
                             proxy='http://user:fixture-pass@example.test:8080')
        public = private.public_dict()
        for field in ('base_url','proxy'):
            executor = Executor()
            profile = LLMProfile(id=7,name='fixture',model='fixture',**{field:public[field]})
            with self.subTest(field=field), self.assertRaises(ProfileInvalid):
                ProfileStore(executor).save(profile)
            self.assertEqual(executor.queries,[])

    def test_masked_key_cannot_be_saved_as_real_key(self):
        from artex.db.profiles import LLMProfile, ProfileStore, ProfileInvalid
        executor = Executor()
        with self.assertRaises(ProfileInvalid):
            ProfileStore(executor).save(LLMProfile(id=7,name='fixture',model='fixture',api_key='…1234'))
        self.assertEqual(executor.queries,[])
