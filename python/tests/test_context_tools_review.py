"""Independent native context scope/wiring tests, inert database only."""
import unittest,json
from artex.agent.worker import WorkerScope
from artex.agent.runtime import Limits,Grant,RunStopped
from artex.sidequestion import Model
import test_context_tools as fixtures

class ContextReview(unittest.TestCase):
    setUp=fixtures.ContextToolTests.setUp
    adapter=fixtures.ContextToolTests.adapter

    def test_actual_worker_identity_can_use_same_run_context_tools(self):
        scope=WorkerScope(3,9,1,'revision',7,2,'work#1',Model(identity='fixture'),'inert')
        context=fixtures.Context(scope.identity(Limits()))
        result=json.loads(self.adapter().call('graph_overview',context,{}))
        self.assertEqual(result['scope']['run_id'],1)

    def test_revocation_at_transaction_read_stops_result(self):
        original=self.db.fetch_all
        def fetch(q):
            rows=original(q)
            if 'exploration_nodes' in q.sql:self.live=False
            return rows
        self.db.fetch_all=fetch
        with self.assertRaises(Exception):self.adapter().call('graph_overview',self.context,{})
        self.assertEqual(self.db.events[-1],'rollback')

    def test_foreign_graph_parent_cannot_return_authorized_metadata(self):
        self.db.results=[[]]
        with self.assertRaises(Exception):self.adapter().call('graph_overview',self.context,{})
        self.assertEqual(len(self.db.calls),1)
        self.assertEqual(self.db.calls[0][1].params,(3,9))

    def test_catalog_mutation_cannot_enable_new_native_handler(self):
        adapter=self.adapter();metadata=adapter.catalog(agents=['planner'])
        metadata[0].agents.append('worker')
        metadata[0].schema['properties']['foreign_selector']={}
        self.assertEqual({t.name for t in adapter.tools()},{'graph_overview','expand_digest'})
        self.assertEqual(adapter.catalog(agents=['planner'])[0].agents,['planner'])
