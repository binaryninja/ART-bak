"""Independent scope metadata admission/partial-effect review; no live database."""
import json,unittest
from artex.agent.runtime import RunStopped,Grant
import test_task_scope_tools as fixtures
from test_task_scope import DB

class ScopeToolReview(unittest.TestCase):
    setUp=fixtures.Tests.setUp
    gate=fixtures.Tests.gate
    approve=fixtures.Tests.approve
    call=fixtures.Tests.call
    def test_executor_rebind_during_current_cannot_redirect_scope_write(self):
        other=DB();self.approve('keyword','safe')
        def current(scope):self.store.executor=other;return True
        self.tools.current=current
        with self.assertRaises(RunStopped):self.call({'kind':'keyword','value':'safe'})
        self.assertEqual(self.db.rows,[]);self.assertEqual(other.rows,[])
    def test_actual_parent_exploration_must_match_context(self):
        self.approve('keyword','safe')
        def parent(query):
            self.db.calls.append(query)
            return (1,None,99) if 'exploration_id' in query.sql else (1,None)
        self.db.fetch_one=parent
        result=self.call({'kind':'keyword','value':'safe'})
        self.assertIsNone(result['added']);self.assertEqual(self.db.rows,[])
    def test_cancelled_second_entry_preserves_only_confirmed_first(self):
        self.approve('keyword','first');self.approve('keyword','second')
        def gate(context,action):
            if action.arguments()['value']=='second':context.cancelled=True
            return Grant(True,True,True)
        self.tools.gate=gate
        with self.assertRaises(RunStopped):self.call({'entries':[{'kind':'keyword','value':'first'},{'kind':'keyword','value':'second'}]})
        self.assertEqual(len(self.db.rows),1);self.assertEqual(self.db.rows[0][5],'first')
    def test_company_callback_scope_mutation_rolls_back_without_added(self):
        self.approve('company','7')
        def company(task,company):self.context['run_id']=999;return True
        self.store.company_allowed=company
        with self.assertRaises(RunStopped):self.call({'kind':'company','value':'7'})
        self.assertEqual(self.db.rows,[])
    def test_iterator_and_shared_batch_mutation_do_not_broaden_input(self):
        with self.assertRaises(ValueError):self.call({'entries':iter([{'kind':'keyword','value':'x'}])})
        self.approve('keyword','safe');args={'entries':[{'kind':'keyword','value':'safe'}]}
        def gate(context,action):args['entries'].append({'kind':'keyword','value':'other'});return Grant(True,True,True)
        self.tools.gate=gate
        result=self.call(args)
        self.assertEqual(len(result['added']),1);self.assertEqual(len(self.db.rows),1)
