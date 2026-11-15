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
        for value in ({'type':'unknown'},{'type':'root_domain','domain':'x','company_id':1}):
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

    def test_all_extended_sql_matches_source(self):
        from artex.db import asset_store as module
        source=(Path(__file__).resolve().parents[2]/'db/assets.go').read_text()
        for name in ('_SUBDOMAIN_UPSERT','_APP_BUNDLE_UPSERT','_APP_NAME_UPSERT','_HTTP_UPSERT','_OTHER_UPSERT','_ENDPOINT_UPSERT'):
            self.assertIn('`'+getattr(module,name)+'`',source)

    def test_psl_required_before_writes(self):
        for asset in ({'type':'subdomain','domain':'a.example.test'}, {'type':'endpoint','url':'https://example.test','method':'get'}):
            with self.assertRaises(AssetUnavailable): self.store.upsert(asset)
        self.assertEqual(self.executor.events,[])
        self.store.root_domain_resolver=lambda host:'unrelated.test'
        with self.assertRaises(AssetUnavailable): self.store.upsert({'type':'subdomain','domain':'a.example.test'})

    def test_subdomain_relationships_share_transaction(self):
        self.store.root_domain_resolver=lambda host:'example.test'
        self.store.upsert({'type':'subdomain','domain':' A.EXAMPLE.TEST. ', 'record_type':'A',
                           'record_value':['bad, 192.0.2.1','2001:db8::1'], 'icp':'registration'},task_id=1)
        self.assertEqual(self.executor.events,['begin','commit'])
        inserts=[q for q in self.executor.queries if q.sql.startswith('\nINSERT INTO assets')]
        self.assertEqual(len(inserts),4)
        self.assertEqual(inserts[-1].params[:3],('a.example.test','example.test','A'))
        self.assertEqual(inserts[-1].params[4:7],('192.0.2.1','192.0.2.0/24','registration'))
        self.assertEqual(len(self.guard_calls),4)
        self.assertEqual(sum('INSERT INTO task_asset_links' in q.sql for q in self.executor.queries),1)

    def test_app_natural_keys_and_icp(self):
        self.store.upsert({'type':'app','app_name':'Example','app_icp':'registration'})
        query=self.executor.queries[-1]
        self.assertIn('ON CONFLICT (app_name)',query.sql)
        self.assertEqual(query.params,('Example',None,None,'registration',4,'scope','{}'))
        self.store.upsert({'type':'app','app_name':'Example','bundle_id':'org.example'})
        self.assertIn('ON CONFLICT (bundle_id)',self.executor.queries[-1].sql)
        self.assertEqual(self.executor.queries[-1].params[:2],('org.example','Example'))

    def test_http_normalization_and_linked_port(self):
        self.store.root_domain_resolver=lambda host:'example.test'
        self.store.upsert({'type':'service','url':'HTTPS://A.EXAMPLE.TEST/', 'service_ip':'192.0.2.5',
                          'technologies':['demo'], 'status_code':200,'auth':[{'user':'demo'}]})
        query=self.executor.queries[1]
        self.assertEqual(query.params[:6],('https://a.example.test','HTTPS','a.example.test','192.0.2.5',443,'example.test'))
        self.assertEqual(query.params[9],200)
        self.assertIn('443',self.executor.queries[-1].params[3])
        self.assertEqual(self.executor.events,['begin','commit'])

    def test_other_service_ip_only_requires_no_psl(self):
        self.store.upsert({'type':'service','ip':'2001:db8::1','port':22,'service_name':' SSH '})
        query=self.executor.queries[1]
        self.assertEqual(query.params[:6],('ssh',None,'2001:db8::1',22,None,'2001:db8::/48'))
        self.assertEqual(len(self.guard_calls),2)

    def test_endpoint_params_and_ip_literal(self):
        self.store.upsert({'type':'endpoint','url':'https://192.0.2.1/a?x=1','method':'get',
                          'params':[{'location':'query','name':'x','value':'1'}], 'ip':'192.0.2.1'})
        query=self.executor.queries[1]
        self.assertEqual(query.params[:6],('https://192.0.2.1/a?x=1','GET','192.0.2.1','192.0.2.1',443,'192.0.2.1'))
        self.assertIn('location',query.params[6])
        self.assertIn("DISTINCT ON ((elem->>'location'), (elem->>'name'))",query.sql)

    def test_url_normalization_preserves_raw_path_query(self):
        norm=AssetStore._url
        self.assertEqual(norm('https://EXAMPLE.TEST:443/a%2Fb?b=2&a=1')[0], 'https://example.test:443/a%2Fb?b=2&a=1')
        self.assertEqual(norm('https://EXAMPLE.TEST/?')[0],'https://example.test/?')
        for raw in ('https://x/a%xy','https://u:p@x/','//example.test/','https://x:0/','https://x/a b','https://x/é','https://x\\evil/'):
            with self.subTest(raw=raw), self.assertRaises(AssetInvalid): norm(raw)

    def test_extended_invalid_inputs_before_write(self):
        values=[{'type':'app','app_name':''}, {'type':'endpoint','url':'https://192.0.2.1','method':''},
                {'type':'service','ip':'192.0.2.1','service_name':'ssh','port':True},
                {'type':'service','url':'https://192.0.2.1','auth':[{'x':float('nan')}]},
                {'type':'service','url':'https://192.0.2.1','status_code':True},
                {'type':'endpoint','url':'https://192.0.2.1','method':'GET','params':[{'x':{'bad':object()}}]}]
        for value in values:
            with self.subTest(value=value), self.assertRaises(AssetInvalid): self.store.upsert(value)
        self.assertEqual(self.executor.events,[])

    def test_linked_asset_denial_rolls_back_parent(self):
        def guard(tx,n,task):
            if n['type']=='root_domain': raise AssetInvalid('denied linked host')
        self.store.mutation_guard=guard
        with self.assertRaises(AssetInvalid):
            self.store.upsert({'type':'endpoint','url':'https://192.0.2.1','method':'GET'},task_id=1)
        self.assertEqual(self.executor.events,['begin','rollback'])
        self.assertFalse(any('task_asset_links' in q.sql for q in self.executor.queries))


if __name__=='__main__': unittest.main()
