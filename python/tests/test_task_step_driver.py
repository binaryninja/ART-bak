"""Actual synchronous assembly over an existing in-memory task and fake model.

No task creation, schema bootstrap, listener, pump thread, live provider or PG.
The SQL double starts with task 5 and origin node 1, as in published fixtures.
"""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import test_task_runtime_application as fixture
from artex.agent.runtime import Grant
from artex.agent.task_runtime import TaskRuntimeAssembly
from artex.agent.task_step_driver import drive_existing_task
from artex.agent import task_step_driver as driver
from artex.agent.pump import TickResult
from artex.agent.host import PollResult
from artex.agent.bootstrap import BootstrapUnresolved
from artex.manager import RuntimeUnavailable
from artex.db.tool_catalog import CatalogTool
from artex.providers import Response
from artex.server.task_runtime_setup import build_task_runtime_application
from artex.tools.graph import SCHEMAS
from test_application import TestCodec


def call(name, arguments, identifier):
    return {'role':'assistant','content':None,'tool_calls':[{
        'id':identifier,'type':'function','function':{'name':name,'arguments':json.dumps(arguments)}}]}


class TaskStepDriverTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixture.Tests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        f=self.fixture
        self.db=f.f.db
        original_execute=self.db.execute
        def execute(query):
            # Model the existing product set_node_state SQL, not a manual goal
            # completion shortcut. It is reached only through actual prove_goal.
            if query.sql.startswith('UPDATE exploration_nodes SET state=$1, blocked_reason=NULL, content_version=content_version+1\nWHERE id=$2'):
                self.db.queries.append(query)
                for index,row in enumerate(self.db.rows):
                    if row[0]==query.params[1]:
                        changed=list(row);changed[4]=query.params[0]
                        self.db.rows[index]=tuple(changed);return 1
                return 0
            return original_execute(query)
        self.db.execute=execute
        def catalog(name,role):
            return CatalogTool(name,True,'Explicit inert fixture tool',SCHEMAS[name],[role],True,'builtin',{},False)
        self.allowed=True
        self.runtime=TaskRuntimeAssembly(self.db,providers=f.resolver,
            transcript=f.f.f.planner.transcript,
            prompts={'goals':'Explicit goals','planner':'Explicit planner','worker':'Explicit worker'},
            policy_revision='example-r1',current=lambda *a:self.allowed,
            lifecycle_guard=f.f.f.guard,authorize=lambda *a:Grant(True,True,True),
            asset_authorize=lambda *a:Grant(True,True,True),validate_input=lambda *a:True,
            graph_catalog={'planner':[catalog('prove_goal','planner')],
                           'worker':[catalog('record_fact','worker')]})
        self.messages=[
            call('set_goals',{'goals':[{'text':'Record the synthetic fixture count','vulnclass':''}]},'goals'),
            {'role':'assistant','content':'Goals submitted'},
            call('add_intent',{'summary':'Count two supplied inert records'},'intent'),
            {'role':'assistant','content':'Work queued'},
            call('record_fact',{'summary':'Synthetic fixture count is two','confidence':'observed'},'fact'),
            {'role':'assistant','content':'Fact recorded'},
            call('prove_goal',{'goal_id':2,'evidence_id':4,'reason':'Synthetic fixture fact'},'proof'),
            {'role':'assistant','content':'Goal proven'},
        ]
        self.provider_calls=[]
        def transport(config):
            def invoke(*args,**kwargs):
                self.provider_calls.append(1)
                message=self.messages.pop(0)
                return Response(200,json.dumps({'choices':[{'message':message,
                    'finish_reason':'tool_calls' if 'tool_calls' in message else 'stop'}],
                    'usage':{'prompt_tokens':1,'completion_tokens':1}}))
            return invoke
        f.p.factory._transport_factory=transport
        self.bundle=build_task_runtime_application(self.runtime,b'x'*32,TestCodec(),
            live_last_activity=lambda identifier:0,profile_available=lambda identifier:True,
            single_process=True,pump_clock=lambda:0)

    def test_existing_task_runs_goals_planner_worker_proof_done(self):
        with (patch('socket.socket',side_effect=AssertionError('No network')),
              patch('threading.Thread.start',side_effect=AssertionError('No threads'))):
            result=drive_existing_task(self.bundle,'5',max_ticks=8)
        self.assertTrue(result.completed,result)
        self.assertEqual((result.state,result.ticks,result.advanced,result.status,result.started),
                         ('completed',4,4,'done',True))
        self.assertEqual(len(self.provider_calls),8)
        self.assertEqual(self.bundle.runner.handles(),())
        self.assertFalse(self.bundle.manager.retirement_snapshot('5')[2])
        self.assertEqual(self.db.receipt[7],'prepared')
        self.assertEqual([(r[1],r[4]) for r in self.db.rows],
                         [('fact','origin'),('goal','met'),('intent','done'),('fact','confirmed')])
        self.assertIn((9,3,'yields',4),self.db.links)
        self.assertIn((9,4,'proves',2),self.db.links)
        queries=self.db.calls+self.db.queries
        self.assertFalse(any(q.sql.startswith(('INSERT INTO tasks','INSERT INTO explorations')) for q in queries))
        self.assertIsNone(self.bundle.pump._thread)

    def test_invalid_arguments_before_any_admission(self):
        before=len(self.db.calls)
        for identifier in (5,'0','05','-5','+5','５','5 '*100,'9'*20,str(1<<63)):
            with self.assertRaises(ValueError):drive_existing_task(self.bundle,identifier)
        for budget in (0,-1,True,1.0,1001,None):
            with self.assertRaises(ValueError):drive_existing_task(self.bundle,'5',max_ticks=budget)
        with self.assertRaises(ValueError):drive_existing_task(object(),'5')
        self.assertEqual(len(self.db.calls),before)
        self.assertEqual(self.provider_calls,[])
        self.assertEqual(self.bundle.runner.handles(),())

    def test_raw_running_row_is_not_fresh_even_when_status_says_created(self):
        self.bundle.manager._tasks['5']['status']='running'
        self.assertEqual(self.bundle.manager.status('5'),'created')
        with self.assertRaisesRegex(RuntimeUnavailable,'not a fresh created task'):
            drive_existing_task(self.bundle,'5')
        self.assertEqual(self.bundle.runner.handles(),())
        self.assertEqual(self.provider_calls,[])

    def test_nonliteral_admission_success_never_authorizes_a_tick(self):
        for returned in (False,None,1,'yes'):
            with patch.object(self.bundle.facade,'start',return_value=returned) as start:
                with patch.object(type(self.bundle),'tick',side_effect=AssertionError('No tick after unconfirmed start')) as tick:
                    with self.assertRaisesRegex(RuntimeUnavailable,'did not confirm'):
                        drive_existing_task(self.bundle,'5')
                    self.assertEqual(start.call_count,1)
                    self.assertEqual(tick.call_count,0)
        self.assertEqual(self.bundle.runner.handles(),())
        self.assertEqual(self.provider_calls,[])

    def test_false_admission_return_after_real_start_retains_owner_without_tick(self):
        original=self.bundle.facade.start
        retained=[]
        def start(identifier):
            self.assertIs(original(identifier),True)
            handle=self.bundle.runner.handles()[0]
            retained.append((handle,handle.token))
            return False
        with patch.object(self.bundle.facade,'start',side_effect=start) as call_start:
            with patch.object(type(self.bundle),'tick',side_effect=AssertionError('No tick after false start')) as tick:
                with self.assertRaisesRegex(RuntimeUnavailable,'did not confirm'):
                    drive_existing_task(self.bundle,'5')
                self.assertEqual((call_start.call_count,tick.call_count),(1,0))
        self.assertIs(self.bundle.runner.handles()[0],retained[0][0])
        self.assertIs(retained[0][0].token,retained[0][1])
        self.assertEqual(self.bundle.manager.status('5'),'running')
        self.assertEqual(self.provider_calls,[])
        self.assertIsNone(self.db.receipt)

    def test_preexisting_terminal_is_not_new_completion(self):
        for state in ('done','failed','timeout'):
            self.bundle.manager._tasks['5']['status']=state
            result=drive_existing_task(self.bundle,'5')
            self.assertEqual((result.state,result.ticks,result.status,result.started),
                             ('already_terminal',0,state,False))
            self.assertFalse(result.completed)
        self.assertEqual(self.provider_calls,[])

    def test_paused_and_queued_do_not_resume(self):
        row=self.bundle.manager._tasks['5']
        for key in ('paused','queued'):
            row[key]=True
            try:
                result=drive_existing_task(self.bundle,'5')
                self.assertEqual((result.state,result.ticks,result.started),('inactive',0,False))
            finally:row[key]=False
        self.assertEqual(self.bundle.runner.handles(),())

    def test_archive_delete_metadata_refuses_admission(self):
        row=self.bundle.manager._tasks['5']
        for key in ('archived_at','deleted_at'):
            row[key]='synthetic marker'
            try:
                with self.assertRaises(RuntimeUnavailable):drive_existing_task(self.bundle,'5')
            finally:row.pop(key)
        self.assertEqual(self.bundle.runner.handles(),())

    def test_positive_or_invalid_timeout_is_not_overridden(self):
        for timeout in (1,20,True,None,-1):
            self.bundle.manager._tasks['5']['timeout_seconds']=timeout
            with self.assertRaisesRegex(RuntimeUnavailable,'zero-timeout'):
                drive_existing_task(self.bundle,'5')
        self.assertEqual(self.provider_calls,[])
        self.assertEqual(self.bundle.runner.handles(),())

    def test_prior_goal_refuses_during_original_bootstrap_and_retains_owner(self):
        from test_planner_cycle import node
        self.db.rows.append(node(2,'goal',{'text':'already initialized'},'open'))
        self.assert_bootstrap_refusal_retained()

    def test_prior_receipt_cannot_be_adopted(self):
        self.db.receipt=(5,9,71,'old','x'*64,'y'*64,1,'prepared',(2,))
        self.assert_bootstrap_refusal_retained()

    def assert_bootstrap_refusal_retained(self):
        with patch.object(self.bundle.facade,'start',wraps=self.bundle.facade.start) as start:
            with self.assertRaises(BootstrapUnresolved):drive_existing_task(self.bundle,'5')
            self.assertEqual(start.call_count,1)
        handle=self.bundle.runner.handles()[0];token=handle.token
        self.assertTrue(handle.unresolved)
        self.assertEqual(self.provider_calls,[])
        result=drive_existing_task(self.bundle,'5')
        self.assertEqual((result.state,result.ticks,result.started),('unresolved',0,False))
        self.assertIs(self.bundle.runner.handles()[0],handle)
        self.assertIs(handle.token,token)
        self.assertEqual(self.bundle.manager.status('5'),'running')

    def test_clock_failure_after_start_is_not_retried_or_cleaned(self):
        error=RuntimeError('inert clock failure')
        with patch.object(self.bundle.pump,'clock',side_effect=error) as clock:
            with patch.object(self.bundle.facade,'start',wraps=self.bundle.facade.start) as start:
                with self.assertRaises(RuntimeError) as got:drive_existing_task(self.bundle,'5')
                self.assertIs(got.exception,error)
                self.assertEqual((clock.call_count,start.call_count),(1,1))
        handle=self.bundle.runner.handles()[0];token=handle.token
        self.assertEqual(drive_existing_task(self.bundle,'5').state,'unresolved')
        self.assertIs(self.bundle.runner.handles()[0],handle)
        self.assertIs(handle.token,token)
        self.assertEqual(self.provider_calls,[])
        self.assertIsNone(self.bundle.pump._thread)

    def test_control_exception_after_start_retains_original_application(self):
        error=KeyboardInterrupt()
        with patch.object(self.bundle.pump,'clock',side_effect=error):
            try:drive_existing_task(self.bundle,'5')
            except KeyboardInterrupt as actual:self.assertIs(actual,error)
            else:self.fail('Expected interruption')
        self.assertEqual(len(self.bundle.runner.handles()),1)
        self.assertEqual(self.bundle.manager.status('5'),'running')
        self.assertEqual(self.provider_calls,[])

    def test_provider_failure_retains_failed_bootstrap_and_no_replay(self):
        def failed(config):
            def invoke(*a,**k):
                self.provider_calls.append(1)
                raise RuntimeError('synthetic provider failure')
            return invoke
        self.fixture.p.factory._transport_factory=failed
        with self.assertRaises(Exception):drive_existing_task(self.bundle,'5')
        handle=self.bundle.runner.handles()[0]
        calls=len(self.provider_calls)
        self.assertGreater(calls,0)
        self.assertEqual(drive_existing_task(self.bundle,'5').state,'unresolved')
        self.assertEqual(len(self.provider_calls),calls)
        self.assertIs(self.bundle.runner.handles()[0],handle)

    def test_settlement_failure_never_reports_done_or_retries(self):
        error=RuntimeError('synthetic settlement failure')
        with patch.object(self.bundle.manager,'complete',side_effect=error) as complete:
            with self.assertRaises(BootstrapUnresolved):drive_existing_task(self.bundle,'5')
            self.assertEqual(complete.call_count,1)
        handle=self.bundle.runner.handles()[0]
        self.assertEqual(len(self.provider_calls),8)
        self.assertNotEqual(self.bundle.manager.status('5'),'done')
        self.assertEqual(drive_existing_task(self.bundle,'5').state,'unresolved')
        self.assertIs(self.bundle.runner.handles()[0],handle)

    def test_preexisting_pump_thread_reference_refused_without_start(self):
        self.bundle.pump._thread=object()
        try:
            with self.assertRaisesRegex(RuntimeUnavailable,'never-threaded'):
                drive_existing_task(self.bundle,'5')
        finally:self.bundle.pump._thread=None
        self.assertEqual(self.provider_calls,[])
        self.assertEqual(self.bundle.runner.handles(),())

    def test_active_manual_tick_returns_busy_without_retry(self):
        self.bundle.pump._active=1
        try:
            result=drive_existing_task(self.bundle,'5')
            self.assertEqual((result.state,result.ticks,result.started),('busy',0,False))
        finally:self.bundle.pump._active=0
        self.assertEqual(self.bundle.runner.handles(),())

    def test_driver_lock_returns_busy_without_touching_runtime(self):
        with driver._locks_guard:
            lock=driver._locks.setdefault(self.bundle.pump,driver.Lock())
        lock.acquire()
        try:
            with patch.object(self.runtime,'validate_bindings',side_effect=AssertionError('must not inspect')):
                result=drive_existing_task(self.bundle,'5')
            self.assertEqual((result.state,result.ticks,result.started),('busy',0,False))
        finally:lock.release()

    def test_stopped_is_distinct_from_unresolved(self):
        self.bundle.pump._stop.set()
        self.assertEqual(drive_existing_task(self.bundle,'5').state,'stopped')
        self.bundle.pump._failure='engine_pump_unresolved'
        self.assertEqual(drive_existing_task(self.bundle,'5').state,'unresolved')
        self.assertEqual(self.provider_calls,[])

    def test_cancelled_owned_handle_is_not_resumed_or_forgotten(self):
        self.bundle.facade.start('5')
        handle=self.bundle.runner.handles()[0]
        handle.token.cancel()
        result=drive_existing_task(self.bundle,'5')
        self.assertEqual((result.state,result.ticks,result.started),('cancelled',0,False))
        self.assertIs(self.bundle.runner.handles()[0],handle)
        self.assertEqual(self.provider_calls,[])

    def test_unrelated_handle_before_admission_refused(self):
        foreign=SimpleNamespace(task_id='6')
        with patch.object(self.bundle.runner,'handles',return_value=(foreign,)):
            with self.assertRaisesRegex(RuntimeUnavailable,'unrelated'):
                drive_existing_task(self.bundle,'5')
        self.assertEqual(self.bundle.runner.handles(),())
        self.assertEqual(self.provider_calls,[])

    def test_unrelated_handle_after_start_refused_before_first_tick(self):
        original=self.bundle.facade.start
        handles=self.bundle.runner.handles
        def start(identifier):
            result=original(identifier)
            self.bundle.runner.handles=lambda:handles()+(SimpleNamespace(task_id='6'),)
            return result
        try:
            with patch.object(self.bundle.facade,'start',side_effect=start):
                with self.assertRaisesRegex(RuntimeUnavailable,'unrelated'):
                    drive_existing_task(self.bundle,'5')
        finally:self.bundle.runner.handles=handles
        self.assertEqual(len(handles()),1)
        self.assertEqual(self.provider_calls,[])
        self.assertIsNone(self.db.receipt)

    def test_retained_timed_ownership_refused(self):
        with patch.object(self.bundle.manager,'deadline_owners',return_value=(object(),)):
            with self.assertRaisesRegex(RuntimeUnavailable,'retained timed'):
                drive_existing_task(self.bundle,'5')
        self.assertEqual(self.provider_calls,[])
        self.assertEqual(self.bundle.runner.handles(),())

    def test_tick_exception_propagates_without_retry_or_releasing_handle(self):
        error=RuntimeError('direct tick sentinel')
        with patch.object(type(self.bundle),'tick',side_effect=error) as tick:
            with patch.object(self.bundle.facade,'start',wraps=self.bundle.facade.start) as start:
                with self.assertRaises(RuntimeError) as actual:drive_existing_task(self.bundle,'5')
                self.assertIs(actual.exception,error)
                self.assertEqual((tick.call_count,start.call_count),(1,1))
        self.assertEqual(len(self.bundle.runner.handles()),1)
        self.assertEqual(self.provider_calls,[])

    def test_last_tick_generation_replacement_is_observed(self):
        original_tick=type(self.bundle).tick
        original_handles=self.bundle.runner.handles
        replacement=SimpleNamespace(task_id='5',unresolved=False,token=SimpleNamespace(cancelled=False))
        def tick(application):
            result=original_tick(application)
            self.bundle.runner.handles=lambda:(replacement,)
            return result
        try:
            with patch.object(type(self.bundle),'tick',tick):
                with self.assertRaisesRegex(RuntimeUnavailable,'generation changed during tick'):
                    drive_existing_task(self.bundle,'5',max_ticks=1)
        finally:self.bundle.runner.handles=original_handles
        self.assertEqual(len(original_handles()),1)
        self.assertIsNot(original_handles()[0],replacement)
        self.assertEqual(len(self.provider_calls),2)

    def test_busy_tick_and_settlement_are_not_spun_or_claimed_done(self):
        for returned in (TickResult(busy=True),TickResult(settlement=PollResult(True))):
            with patch.object(type(self.bundle),'tick',return_value=returned) as tick:
                result=drive_existing_task(self.bundle,'5',max_ticks=100)
                self.assertEqual((result.state,result.ticks,result.advanced),('busy',1,0))
                self.assertEqual(tick.call_count,1)
            self.assertEqual(len(self.bundle.runner.handles()),1)
            self.assertFalse(result.completed)
        self.assertEqual(self.provider_calls,[])

    def test_unrelated_tick_receipt_is_not_reported_as_target_progress(self):
        with patch.object(type(self.bundle),'tick',return_value=TickResult(advanced=('6',))) as tick:
            with self.assertRaisesRegex(RuntimeUnavailable,'unrelated task work'):
                drive_existing_task(self.bundle,'5')
            self.assertEqual(tick.call_count,1)
        self.assertEqual(len(self.bundle.runner.handles()),1)
        self.assertEqual(self.provider_calls,[])

    def test_runtime_persisted_timeout_authority_is_not_bypassed_by_cached_zero(self):
        self.fixture.p.task.timeout_seconds=20
        self.assertEqual(self.bundle.manager.lifecycle_snapshot('5')['timeout_seconds'],0)
        with self.assertRaises(RuntimeUnavailable):drive_existing_task(self.bundle,'5')
        self.assertEqual(self.bundle.runner.handles(),())
        self.assertEqual(self.provider_calls,[])

    def test_tick_budget_continues_identical_live_handle_and_token(self):
        first=drive_existing_task(self.bundle,'5',max_ticks=1)
        self.assertEqual((first.state,first.ticks,first.started),('tick_limit',1,True))
        handle=self.bundle.runner.handles()[0];token=handle.token
        self.assertEqual(len(self.provider_calls),2)
        second=drive_existing_task(self.bundle,'5',max_ticks=1)
        self.assertEqual((second.state,second.ticks,second.started),('tick_limit',1,False))
        self.assertIs(self.bundle.runner.handles()[0],handle)
        self.assertIs(handle.token,token)
        final=drive_existing_task(self.bundle,'5',max_ticks=2)
        self.assertTrue(final.completed)
        self.assertFalse(final.started)
        self.assertEqual(len(self.provider_calls),8)


if __name__=='__main__':
    unittest.main()
