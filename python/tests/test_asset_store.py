"""Offline injected-executor checks; no live PostgreSQL or target traffic."""
from contextlib import contextmanager
from pathlib import Path
import re
import unittest
from artex.db.asset_store import (AssetStore, AssetInvalid, AssetUnavailable, AssetUnsupported,
                                  domain_key, validate_ip, _ROOT_UPSERT, _IP_UPSERT)


def asset_row(key=7):
    row = [None] * 32
    row[0:4] = [key, 'root_domain', None, '[1]']
    row[4:8] = ['example.test', 'example.test', '', '']
    row[31] = '2026-11-15 00:00:00+00'
    return row


class Executor:
    def __init__(self):
        self.queries = []; self.events = []; self.rows = [asset_row()]
        self.provenance = []; self.one = (7,); self.affected = 1; self.error = None
    def execute(self, query):
        self.queries.append(query)
        if self.error: raise self.error
        return self.affected
    def fetch_one(self, query):
        self.queries.append(query)
        if self.error: raise self.error
        return self.one
    def fetch_all(self, query):
        self.queries.append(query)
        if self.error: raise self.error
        return self.provenance if 'FROM task_asset_links' in query.sql else self.rows
    @contextmanager
    def transaction(self):
        self.events.append('begin')
        try:
            yield self
        except Exception:
            self.events.append('rollback'); raise
        else:
            self.events.append('commit')


class AssetStoreTests(unittest.TestCase):
    def setUp(self):
        self.executor = Executor()
        self.guard_calls = []; self.resolver_calls = []
        def guard(tx, asset, task_id):
            self.guard_calls.append((tx,asset,task_id))
        def resolver(tx, domain, ip, icp):
            self.resolver_calls.append((tx,domain,ip,icp)); return 4
        self.store = AssetStore(self.executor, allow_global=True, company_resolver=resolver, mutation_guard=guard)

    def test_global_requires_explicit_enablement(self):
        store = AssetStore(self.executor)
        for operation in (store.query, store.count, store.counts_by_type, lambda: store.get(7)):
            with self.assertRaises(AssetUnavailable): operation()
        self.assertEqual(self.executor.queries, [])

    def test_declared_scope_never_falls_back_to_global(self):
        for task_id in (0,-1,False,'1'):
            with self.subTest(task_id=task_id), self.assertRaises(AssetInvalid):
                self.store.query(task_id=task_id,in_scope=True)
        self.assertEqual(self.executor.queries, [])

    def test_or_is_enclosed_before_company_and_task_filters(self):
        self.store.query('port=80 OR port=443', task_id=1, company_id=4)
        query = self.executor.queries[0]
        self.assertIn('WHERE ((port = $1) OR (port = $2)) AND $3 = ANY(task_ids) AND company_id = $6', query.sql)
        self.assertEqual(query.params, (80,443,1,50,0,4))

    def test_declared_scope_and_bound_lookup(self):
        self.store.get(7,task_id=1,in_scope=True)
        query = self.executor.queries[0]
        self.assertTrue(query.sql.startswith('WITH '))
        self.assertIn('WHERE id IN ($2) AND id IN (SELECT id FROM target)', query.sql)
        self.assertEqual(query.params,(1,7))

    def test_provenance_is_hydrated_without_changing_membership(self):
        self.executor.provenance = [(7,'api','Registered via asset API',9)]
        result = self.store.query(task_id=1)
        self.assertEqual(result[0]['task_source_node_id'],9)
        self.assertEqual(self.executor.queries[-1].params,(1,'{7}'))
        self.assertNotIn('company_id',result[0])
        self.assertNotIn('port',result[0])
        self.assertIn('task_ids',result[0])

    def test_untrusted_hydrator_cannot_expand_results(self):
        self.store.hydrate_task_sources = lambda task,rows: rows+[{'id':999}]
        with self.assertRaises(AssetUnavailable): self.store.query(task_id=1)

    def test_count_matches_filters(self):
        self.executor.one=(12,)
        self.assertEqual(self.store.count('domain==example.test',company_id=4),12)
        query=self.executor.queries[-1]
        self.assertIn('WHERE (domain = $1) AND company_id = $2',query.sql)
        self.assertEqual(query.params,('example.test',4))
        self.executor.rows=[('root_domain',2),('ip',1)]
        self.assertEqual(self.store.counts_by_type(task_id=1),{'root_domain':2,'ip':1})
        self.assertIn('$1 = ANY(task_ids)',self.executor.queries[-1].sql)

    def test_invalid_filters_and_dsl_do_not_query(self):
        for kwargs in ({'typ':'unrecognized'},{'company_id':0},{'dsl':None},{'dsl':'port=abc'}):
            with self.subTest(kwargs=kwargs),self.assertRaises(AssetInvalid): self.store.query(**kwargs)
        self.assertEqual(self.executor.queries,[])

    def test_root_upsert_lock_guard_resolver_and_merge(self):
        self.assertEqual(self.store.upsert_root_domain(' EXAMPLE.TEST. ', 'ICP', 1),7)
        self.assertEqual(self.executor.events,['begin','commit'])
        self.assertEqual(self.executor.queries[0].params,(7337741003,))
        self.assertEqual(self.resolver_calls[0],(self.executor,'example.test','','ICP'))
        query=self.executor.queries[1]
        self.assertEqual(query.params,('example.test','ICP',4,'{1}'))
        self.assertIn("assets.company_source = 'explicit'",query.sql)
        self.assertEqual(self.executor.queries[2].params,(1,7,'api','Registered via asset API',None))

    def test_provenance_failure_rolls_back_upsert(self):
        self.executor.affected=0
        with self.assertRaises(AssetUnavailable): self.store.upsert_root_domain('example.test',task_id=1)
        self.assertEqual(self.executor.events,['begin','rollback'])

    def test_ip_normalization_arrays_and_cidr(self):
        self.store.upsert_ip('192.0.2.99',['a"b\\c'],[{'port':443,'service':'https'}])
        query=self.executor.queries[-1]
        self.assertEqual(query.params[1],'192.0.2.0/24')
        self.assertEqual(query.params[2],'{"a\\"b\\\\c"}')
        self.assertIn('443',query.params[3])
        self.store.upsert_ip('2001:db8:1234:5678::1')
        self.assertEqual(self.executor.queries[-1].params[1],'2001:db8:1234::/48')

    def test_invalid_ip_ports_and_fields_preflight_no_writes(self):
        invalid=[{'type':'ip','ip':'host.test'}, {'type':'ip','ip':'fe80::1%eth0'},
                 {'type':'ip','ip':'192.0.2.1','open_ports':[{'port':True}]},
                 {'type':'root_domain','domain':''}, {'type':[]}, {'type':'ip','ip':'192.0.2.1','bound_domains':[None]}]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(AssetInvalid): self.store.validate_upsert(value)
        for value in ({'type':'service'},{'type':'root_domain','domain':'x','company_id':1}):
            with self.assertRaises(AssetUnsupported): self.store.validate_upsert(value)
        self.assertEqual(self.executor.queries,[])

    def test_missing_mutation_adapters_fail_before_transaction(self):
        self.store.company_resolver=None
        with self.assertRaises(AssetUnavailable): self.store.upsert_root_domain('example.test')
        self.assertEqual(self.executor.events,[])

    def test_guard_denial_prevents_upsert(self):
        def deny(*args): raise AssetInvalid('outside authorized scope')
        self.store.mutation_guard=deny
        with self.assertRaises(AssetInvalid): self.store.upsert_ip('192.0.2.1')
        self.assertEqual(len(self.executor.queries),1)
        self.assertEqual(self.executor.events,['begin','rollback'])

    def test_source_sql_matches_upstream(self):
        root=Path(__file__).resolve().parents[2]
        source=(root/'db/assets.go').read_text()
        for function,expected in [('UpsertRootDomain',_ROOT_UPSERT),('UpsertIP',_IP_UPSERT)]:
            part=source[source.index('func (s *AssetStore) '+function+'('):]
            self.assertEqual(re.search(r'`(\nINSERT INTO assets.*?)`',part,re.S)[1],expected)

    def test_domain_and_ip_helpers(self):
        self.assertEqual(domain_key(' EXAMPLE.TEST. '),'example.test')
        self.assertEqual(str(validate_ip('192.0.2.1')),'192.0.2.1')
        with self.assertRaises(AssetInvalid): validate_ip('192.0.2.001')
        with self.assertRaises(AssetInvalid): domain_key('\x00')

    def test_unimplemented_mutations_are_explicit(self):
        with self.assertRaises(AssetUnsupported): self.store.update(7,{'type':'app'})
        with self.assertRaises(AssetUnsupported): self.store.delete(7)


if __name__=='__main__': unittest.main()
