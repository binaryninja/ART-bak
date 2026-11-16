import unittest,json
from contextlib import nullcontext
from artex.db.task_coverage import *
from test_tasks_store import Fake

PARENT=[(5,9,True)]
SOURCES=[(6,10,None,False)]

def asset(id,kind,*,company=0,domain='',root='',ip='',url='',port=0,tested=False):
    return (id,kind,company,domain,root,ip,url,port,'','','',0,tested)

class CoverageTests(unittest.TestCase):
    def make(self,rows,**kw):
        self.db=Fake(rows);self.valid=True;self.approved=[];self.connectors=[]
        def approve(s,t,e):self.approved.append((t,e));return True
        args=dict(authorize=approve,current=lambda s:self.valid,lifecycle_guard=lambda s:nullcontext(),connector_authorize=lambda s,k,v:self.connectors.append((k,v)) or True)
        args.update(kw);self.store=TaskCoverageStore(self.db,CoverageScope(5,9,'r1'),**args);return self.store
    def test_aggregates_union_direct_sources_and_native_inet_contract(self):
        store=self.make([PARENT,SOURCES,[(3,)],[('ip',2,1),('service',4,2)]])
        self.assertEqual(store.coverage(),dict(enabled=True,scope_rows=3,denominator=6,tested=3,pct=.5,by_type=[dict(type='ip',total=2,tested=1),dict(type='service',total=4,tested=2)]))
        self.assertEqual(self.approved,[(5,9),(6,10)]*4)
        q=[q for _,q in self.db.calls if q][-1];self.assertEqual(q.params,([5,6],));self.assertIn('ts.net >>= try_inet(a.ip)',q.sql)
        self.assertIn('UNION',q.sql);self.assertNotIn('RECURSIVE',q.sql)
    def test_disabled_short_circuit_no_aggregate(self):
        store=self.make([[(5,9,False)],[]]);result=store.coverage()
        self.assertFalse(result['enabled']);self.assertEqual(result['by_type'],[]);self.assertIsNone(result['pct'])
    def test_unauthorized_direct_source_blocks_all_content(self):
        store=self.make([PARENT,SOURCES],authorize=lambda s,t,e:t==5)
        with self.assertRaises(CoverageDenied):store.coverage()
        self.assertEqual(len([q for _,q in self.db.calls if q]),2)
    def test_archived_source_not_silently_partial(self):
        store=self.make([PARENT,[(6,10,'archived',False)]])
        with self.assertRaises(CoverageUnavailable):store.coverage()
    def test_no_source_recursion_or_global_scope(self):
        store=self.make([PARENT,[],[(0,)],[]]);result=store.coverage();self.assertIsNone(result['pct'])
        queries=[q for _,q in self.db.calls if q];self.assertEqual(queries[-1].params,([5],));self.assertIn('relation.task_id=$1',queries[1].sql)
    def test_untested_bound_page_types(self):
        store=self.make([PARENT,SOURCES,[(9,)],[(7,'ip','192.0.2.1')]])
        page,total=store.untested('ip',1,2);self.assertEqual(total,9);self.assertEqual(page,[dict(id=7,type='ip',label='192.0.2.1')])
        query=[q for _,q in self.db.calls if q][-1];self.assertEqual(query.params,([5,6],'ip',1,2,2097152))
    def test_refs_jsonb_provenance_and_summary_precedence(self):
        store=self.make([PARENT,SOURCES,[(12,'intent','done',{'summary':' ','text':'fallback'},6),(11,'fact','confirmed',json.dumps({'summary':'local','text':'other'}),5)]])
        refs=store.asset_refs(8)
        self.assertEqual(refs[0],dict(id=12,kind='intent',state='done',summary='fallback',source_task_id='6',inherited=True));self.assertNotIn('inherited',refs[1])
        q=[q for _,q in self.db.calls if q][-1];self.assertEqual(q.params,([5,6],8,5,2001,2097152));self.assertIn("('done','blocked','exhausted','stopped')",q.sql)
    def test_nonterminal_inherited_ref_fails_closed_even_bad_executor(self):
        store=self.make([PARENT,SOURCES,[(12,'intent','running',{},6)]])
        with self.assertRaises(CoverageUnavailable):store.asset_refs(8)
    def test_graph_real_connector_company_and_endpoint_parent(self):
        rows=[asset(1,'subdomain',domain='app.example.test',root='example.test'),asset(2,'service',domain='app.example.test',port=443,url='https://app.example.test'),asset(3,'endpoint',url='https://app.example.test/path',tested=True)]
        store=self.make([PARENT,[],rows,[(4,8)],[('Company',)]])
        graph=store.coverage_graph();self.assertEqual(len(graph['nodes']),5)
        self.assertEqual(graph['edges'],[dict(src='a:1',dst='a:4'),dict(src='a:2',dst='a:1'),dict(src='a:3',dst='a:2'),dict(src='a:4',dst='c:8')])
        self.assertEqual(self.connectors,[('root_domain','example.test'),('company',8)]*3)
        self.assertFalse(graph['nodes'][3]['in_scope']);self.assertTrue(graph['nodes'][2]['tested'])
    def test_connector_policy_required_before_global_query(self):
        store=self.make([PARENT,[],[asset(1,'subdomain',domain='app.example.test',root='example.test')]],connector_authorize=None)
        with self.assertRaises(CoverageDenied):store.coverage_graph()
        self.assertFalse(any(q and q.sql.startswith('SELECT id,COALESCE(company_id') for _,q in self.db.calls))
    def test_synthetic_root_when_authorized_real_root_missing(self):
        store=self.make([PARENT,[],[asset(1,'subdomain',domain='app.example.test',root='example.test')],[]])
        graph=store.coverage_graph();self.assertEqual(graph['nodes'][1]['key'],'r:example.test');self.assertEqual(graph['edges'],[dict(src='a:1',dst='r:example.test')])
    def test_overflow_and_final_authority_change_fail_closed(self):
        store=self.make([PARENT,[],[asset(1,'ip'),asset(2,'ip')]],max_rows=1)
        with self.assertRaises(CoverageUnavailable):store.coverage_graph()
        store=self.make([PARENT,[],[(0,)],[]]);original=self.db.fetch_all
        def query(q):
            result=original(q)
            if 'GROUP BY' in q.sql:self.valid=False
            return result
        self.db.fetch_all=query
        with self.assertRaises(CoverageDenied):store.coverage()

class TransferBoundsTests(unittest.TestCase):
    def test_null_label_sentinel_rejected(self):
        f=CoverageTests();store=f.make([PARENT,[],[(1,)],[(7,'ip',None)]])
        with self.assertRaises(CoverageUnavailable):store.untested()
    def test_null_payload_sentinel_rejected(self):
        f=CoverageTests();store=f.make([PARENT,[],[(1,'fact','confirmed',None,5)]])
        with self.assertRaises(CoverageUnavailable):store.asset_refs(7)
    def test_null_graph_sentinel_rejected(self):
        f=CoverageTests();row=list(asset(1,'ip'));row[3]=None
        store=f.make([PARENT,[],[row]])
        with self.assertRaises(CoverageUnavailable):store.coverage_graph()
    def test_null_company_sentinel_rejected(self):
        f=CoverageTests();store=f.make([PARENT,[],[asset(1,'ip',company=3)],[(None,)]])
        with self.assertRaises(CoverageUnavailable):store.coverage_graph()
