"""Independent graph/asset boundary regressions with inert transaction fakes."""
from contextlib import contextmanager
import unittest
from artex.db.asset_store import AssetStore, AssetUnavailable
from artex.db.exploration import ExplorationStore, InheritedWriteError
from artex.server.assets import _parse_insert, _query, InvalidAssetRequest, AssetService
from artex.server.core import Request


class ReviewExecutor:
    def __init__(self, provenance_count=1):
        self.events = []
        self.queries = []
        self.provenance_count = provenance_count
    @contextmanager
    def transaction(self):
        self.events.append('begin')
        try:
            yield self
        except Exception:
            self.events.append('rollback')
            raise
        else:
            self.events.append('commit')
    def execute(self, query):
        self.queries.append(query)
        if 'pg_advisory' in query.sql:
            self.events.append('lock')
        elif 'INSERT INTO task_asset_links' in query.sql:
            self.events.append('provenance')
            return self.provenance_count
        return 1
    def fetch_one(self, query):
        self.queries.append(query)
        self.events.append('write')
        return (8,)
    def fetch_all(self, query):
        self.queries.append(query)
        return []


class GraphAssetReviewTests(unittest.TestCase):
    def test_duplicate_body_scope_is_rejected_not_globalized(self):
        for body in (b'{"task_id":7,"task_id":0,"assets":[]}',
                     b'{"task_id":0,"task_id":7,"assets":[]}',
                     b'{"task_id":7,"assets":[{"type":"ip","ip":"127.0.0.1","ip":"127.0.0.2"}]}'):
            with self.subTest(body=body), self.assertRaises((InvalidAssetRequest, ValueError)):
                _parse_insert(body)

    def test_explicit_null_body_scope_is_rejected(self):
        with self.assertRaises((InvalidAssetRequest, ValueError)):
            _parse_insert(b'{"task_id":null,"assets":[]}')

    def test_ambiguous_body_scope_causes_no_store_calls(self):
        class NoCalls:
            def __getattr__(self, name):
                raise AssertionError('Unexpected store call: ' + name)
        service = AssetService(NoCalls())
        for body in (b'{"task_id":7,"task_id":0,"assets":[]}',
                     b'{"task_id":0,"task_id":7,"assets":[]}',
                     b'{"task_id":null,"assets":[]}',
                     b'{"task_id":7,"assets":[{"type":"ip","ip":"127.0.0.1","ip":"127.0.0.2"}]}'):
            with self.subTest(body=body):
                self.assertEqual(service._handle('insert', Request('POST','/api/assets',body=body)).status, 400)

    def test_malformed_query_scope_never_reaches_store(self):
        for query in ('task_id=', 'task_id=1&task_id=0', 'company_id=null',
                      'task_id=1&company_id=2', 'task_ids=1'):
            with self.subTest(query=query), self.assertRaises(InvalidAssetRequest):
                _query(Request('GET', '/api/assets?' + query))

    def test_global_requires_explicit_optin_before_queries(self):
        executor = ReviewExecutor()
        store = AssetStore(executor)
        for operation in (store.query, store.count, store.counts_by_type):
            with self.assertRaises(AssetUnavailable):
                operation()
        self.assertEqual(executor.queries, [])

    def test_compound_filter_remains_under_company_scope(self):
        executor = ReviewExecutor()
        AssetStore(executor).query('port=80 OR port=443', company_id=19)
        query = executor.queries[0]
        self.assertIn('WHERE ((port = $1) OR (port = $2)) AND company_id = $5', query.sql)
        self.assertEqual(query.params, (80, 443, 50, 0, 19))

    def test_provenance_failure_rolls_back_upsert(self):
        executor = ReviewExecutor(provenance_count=0)
        def guard(tx, asset, task_id):
            self.assertIs(tx, executor)
            executor.events.append('guard')
        def resolver(tx, domain, ip, icp):
            self.assertIs(tx, executor)
            executor.events.append('resolver')
            return None
        store = AssetStore(executor, mutation_guard=guard, company_resolver=resolver)
        with self.assertRaises(AssetUnavailable):
            store.upsert_root_domain('example.test', task_id=7)
        self.assertEqual(executor.events,
                         ['begin','lock','guard','resolver','write','provenance','rollback'])

    def test_guard_rejection_precedes_any_mutation(self):
        executor = ReviewExecutor()
        def guard(*args):
            raise AssetUnavailable('rejected')
        store = AssetStore(executor, mutation_guard=guard, company_resolver=lambda *args: None)
        with self.assertRaises(AssetUnavailable):
            store.upsert_root_domain('example.test', task_id=7)
        self.assertEqual(executor.events, ['begin','lock','rollback'])

    def test_inherited_graph_rejects_writes_before_executor(self):
        executor = ReviewExecutor()
        graph = ExplorationStore(executor, 12, read_only=True, source_task_id=4)
        for operation in (lambda: graph.anchor(3, 8), lambda: graph.link(3,'spawns',4),
                          lambda: graph.update_payload(3, {}), lambda: graph.claim_intent(3,'x')):
            with self.assertRaises(InheritedWriteError):
                operation()
        self.assertEqual(executor.queries, [])

    def test_edge_and_node_updates_bind_exploration(self):
        executor = ReviewExecutor()
        graph = ExplorationStore(executor, 12)
        graph.link(3, 'spawns', 4)
        self.assertIn('src.exploration_id=$1 AND dst.exploration_id=$1', executor.queries[-1].sql)
        self.assertEqual(executor.queries[-1].params, (12, 3, 'spawns', 4))
        graph.update_payload(3, {'text':'example'})
        self.assertIn('WHERE id=$2 AND exploration_id=$3', executor.queries[-1].sql)
        self.assertEqual(executor.queries[-1].params[-2:], (3,12))
