"""Company SQL contract tests: fake executors, no database or registry IO."""
from contextlib import contextmanager
import unittest
from artex.db.companies import (CompanyStore,CompanyInvalid,CompanyUnavailable,CompanyNotFound,
                               CompanyNameConflict,company_nkey,normalize_icp)

ROW=(3,'Example','example',None,'created','updated')


class Executor:
    def __init__(self,results=()):self.results=list(results);self.queries=[];self.events=[]
    def fetch_all(self,q):
        self.queries.append(q)
        item=self.results.pop(0)
        if isinstance(item,Exception):raise item
        return item
    def execute(self,q):self.queries.append(q);return 1
    @contextmanager
    def transaction(self):
        self.events.append('begin')
        try:yield self
        except BaseException:
            self.events.append('rollback');raise
        else:self.events.append('commit')


class CompanyTests(unittest.TestCase):
    def test_go_name_and_icp_normalization(self):
        self.assertEqual(company_nkey(' \tEXAMPLE\u3000İNC\n'),'example inc')
        self.assertEqual(normalize_icp(' AB\u3000C 12 '),'abc12')
        self.assertEqual(company_nkey('a\x1cb'),'a\x1cb')  # not Go whitespace
        for value in (None,'bad\x00value','\ud800'):
            with self.assertRaises(CompanyInvalid):company_nkey(value)

    def test_get_and_name_bound_scope(self):
        ex=Executor([[ROW],[ROW]])
        store=CompanyStore(ex,company_ids=[3,3])
        self.assertEqual(store.get(3).to_dict()['name'],'Example')
        self.assertNotIn('logo',store.get_by_name(' Example ').to_dict())
        self.assertEqual(ex.queries[0].params,(3,'{3}'))
        self.assertEqual(ex.queries[1].params,('example','{3}'))
        self.assertIn('id=ANY($2::bigint[])',ex.queries[0].sql)
        self.assertIsNone(store.get(4));self.assertEqual(len(ex.queries),2)

    def test_list_search_scope_rules_and_count(self):
        ex=Executor([[ROW+(2,)],[(9,3,'domain','example.test','','','example.test','manual')]])
        out=CompanyStore(ex,company_ids=[3]).search("50%_' OR 1=1")
        self.assertEqual(out[0].asset_count,2)
        self.assertEqual(out[0].to_dict()['scope'][0]['domain'],'example.test')
        query=ex.queries[0]
        self.assertNotIn('OR 1=1',query.sql)
        self.assertEqual(query.params,('{3}',"%50\\%\\_' OR 1=1%"))
        self.assertIn('COUNT(DISTINCT a.id)',query.sql)
        self.assertEqual(ex.queries[1].params,(3,))

    def test_empty_scope_is_not_global(self):
        ex=Executor([[],[],[]])
        store=CompanyStore(ex,company_ids=[])
        self.assertEqual(store.list(),[])
        self.assertIsNone(store.get_by_name('Example'))
        self.assertIsNone(store.resolve('example.test'))
        self.assertTrue(all('{}' in q.params for q in ex.queries))
        with self.assertRaises(CompanyUnavailable):store.create('New')
        self.assertEqual(ex.events,[])

    def test_upsert_bound_locked_preserves_logo(self):
        ex=Executor([[(3,False)]])
        self.assertEqual(CompanyStore(ex).upsert(' Example '),(3,False))
        self.assertEqual(ex.events,['begin','commit'])
        self.assertEqual(ex.queries[0].params,(7337741003,))
        self.assertEqual(ex.queries[1].params,(' Example ','example',None))
        self.assertIn('COALESCE(EXCLUDED.logo,companies.logo)',ex.queries[1].sql)
        self.assertNotIn(' Example ',ex.queries[1].sql)

    def test_scoped_upsert_cannot_create_or_modify_outside(self):
        ex=Executor([[]])
        with self.assertRaises(CompanyNotFound):CompanyStore(ex,company_ids=[3]).upsert('Other')
        self.assertEqual(ex.events,['begin','rollback'])
        self.assertFalse(any('INSERT' in q.sql or q.sql.startswith('UPDATE') for q in ex.queries))
        ex=Executor([[ROW],[(3,)]])
        self.assertEqual(CompanyStore(ex,company_ids=[3]).upsert('EXAMPLE','logo'),(3,False))
        self.assertEqual(ex.queries[-1].params,('EXAMPLE','logo',3))

    def test_create_conflict_and_write_failure_roll_back(self):
        for results,error in (([[]],CompanyNameConflict),([RuntimeError('offline')],RuntimeError)):
            ex=Executor(results)
            with self.assertRaises(error):CompanyStore(ex).create('Example')
            self.assertEqual(ex.events,['begin','rollback'])
        ex=Executor([[(4,)]])
        self.assertEqual(CompanyStore(ex).create('New'),4)
        self.assertEqual(ex.events,['begin','commit'])

    def test_validation_before_transactions(self):
        ex=Executor();store=CompanyStore(ex)
        for name in ('',' \t','bad\x00'):
            with self.assertRaises(CompanyInvalid):store.upsert(name)
        with self.assertRaises(CompanyUnavailable):store.create('New',scope_inputs=['example.test'])
        for method in (store.delete,store.add_scope,store.update_scope):
            with self.assertRaises(CompanyUnavailable):method(3)
        self.assertEqual(ex.events,[]);self.assertEqual(ex.queries,[])

    def test_resolver_priority_short_circuit_and_tie_order(self):
        ex=Executor([[(3,)]])
        store=CompanyStore(ex)
        self.assertEqual(store.resolve('example.test','192.0.2.4','ICP'),3)
        self.assertEqual(len(ex.queries),1)
        self.assertIn('ORDER BY length(domain) DESC, company_id',ex.queries[0].sql)
        ex=Executor([[],[(4,)]])
        self.assertEqual(CompanyStore(ex).resolve('example.test','192.0.2.4','ICP'),4)
        self.assertEqual(ex.queries[-1].params,('192.0.2.4',))
        self.assertIn('net >>= $1::inet',ex.queries[-1].sql)
        self.assertIn('masklen(net) DESC, company_id',ex.queries[-1].sql)

    def test_resolver_transaction_adapter_and_scope(self):
        outer=Executor();tx=Executor([[],[],[(3,)]])
        self.assertEqual(CompanyStore(outer,company_ids=[3]).resolve_with_executor(tx,'example.test','192.0.2.4',' I C P '),3)
        self.assertEqual(outer.queries,[])
        self.assertTrue(all('{3}' in q.params for q in tx.queries))
        self.assertEqual(tx.queries[-1].params,('icp','{3}'))
        self.assertEqual(tx.events,[])  # caller owns transaction/lock

    def test_bad_ip_skipped_but_storage_error_not_swallowed(self):
        ex=Executor([[(3,)]])
        self.assertEqual(CompanyStore(ex).resolve(ip='not-ip',icp='ICP'),3)
        self.assertEqual(len(ex.queries),1)
        self.assertIn("kind='icp'",ex.queries[0].sql)
        ex=Executor([RuntimeError('offline')])
        with self.assertRaises(RuntimeError):CompanyStore(ex).resolve('example.test','192.0.2.4','ICP')
        self.assertEqual(len(ex.queries),1)


if __name__=='__main__':unittest.main()
