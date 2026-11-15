"""Inert providers, synchronous fake sinks and bounded event-driven threads only."""
from threading import Event
import unittest
from unittest.mock import patch

from artex.agent.runner import AgentRunner, RunPlan
from artex.agent.runtime import BoundedAgent, Grant
from artex.manager import CancellationToken, TaskConflict, TaskManager, RuntimeUnavailable
from test_manager import Repository, record, enrich


class Model:
    def __init__(self, entered=None, release=None):
        self.entered, self.release, self.calls = entered, release, 0
    def complete(self, ctx, request):
        self.calls += 1
        if self.entered: self.entered.set()
        if self.release and not self.release.wait(3): raise RuntimeError('test gate timed out')
        return {'role':'assistant', 'content':[{'type':'text','text':'done'}]}, 'end_turn', {'input_tokens':1,'output_tokens':1}


def bootstrap(task):
    return RunPlan({'messages':[{'role':'user','content':[{'type':'text','text': task.get('goal') or 'inert test'}]}]}, {'scope':'fixture-only'})


def make_runner(model=None, sink=None, **overrides):
    args = dict(agent=BoundedAgent(model or Model(), gate=lambda ctx, action: Grant(True,True,True)),
                validate_create=lambda *args: True, readiness=lambda task: True,
                bootstrap=bootstrap, result_sink=sink or (lambda *args: None))
    args.update(overrides)
    return AgentRunner(**args)


class RunnerTests(unittest.TestCase):
    def test_manager_fast_completion_settles_only_after_admission(self):
        saved=[]; runner=make_runner(sink=lambda task,token,result: saved.append((task,token,result)))
        manager=TaskManager(Repository(), runner=runner, dto_enricher=enrich)
        manager.load_existing()
        self.assertTrue(manager.start('1'))
        handle=runner.handles()[0]
        self.assertTrue(handle.wait(2))
        self.assertEqual(manager.status('1'),'running')
        self.assertEqual(runner.settle_completed(manager.complete),['1'])
        self.assertEqual(manager.status('1'),'done')
        self.assertEqual(len(saved),1)
        self.assertEqual(runner.settle_completed(manager.complete),[])

    def test_missing_prerequisites_reject_before_persistence(self):
        for key in ('agent','bootstrap','result_sink','readiness','validate_create'):
            with self.subTest(key=key):
                repo=Repository(); runner=make_runner(**{key:None})
                manager=TaskManager(repo,runner=runner,dto_enricher=enrich)
                with self.assertRaises(RuntimeUnavailable): manager.create_task('x','y')
                self.assertEqual(repo.created,0)
        repo=Repository(); manager=TaskManager(repo,runner=make_runner(),dto_enricher=enrich)
        with self.assertRaises(RuntimeUnavailable): manager.create_task('x','y',seed_first_intent=True)
        self.assertEqual(repo.created,0)

    def test_duplicate_start_same_token_never_launches_twice(self):
        entered,release=Event(),Event(); model=Model(entered,release)
        runner=make_runner(model); token=CancellationToken()
        handle=runner.start(record(),token)
        try:
            self.assertTrue(entered.wait(2))
            self.assertIs(runner.start(record(),token),handle)
            with self.assertRaises(TaskConflict): runner.start(record(),CancellationToken())
            self.assertEqual(model.calls,1)
        finally:
            release.set(); self.assertTrue(handle.wait(2))

    def test_cancel_ignoring_provider_cannot_claim_drained(self):
        entered,release=Event(),Event(); runner=make_runner(Model(entered,release))
        manager=TaskManager(Repository(),runner=runner,dto_enricher=enrich); manager.load_existing()
        self.assertTrue(manager.start('1')); handle=runner.handles()[0]
        try:
            self.assertTrue(entered.wait(2))
            with self.assertRaises(TaskConflict): manager.pause('1',timeout=0)
            self.assertFalse(handle.wait(0))
            self.assertFalse(manager.lifecycle_snapshot('1')['paused'])
            self.assertEqual(runner.settle_completed(manager.complete),[])
        finally:
            release.set(); self.assertTrue(handle.wait(2))
        self.assertEqual(handle.result.reason,'cancelled')
        self.assertTrue(manager.pause('1',timeout=1)['paused'])
        self.assertEqual(runner.settle_completed(manager.complete),['1'])
        self.assertNotEqual(manager.lifecycle_snapshot('1')['status'],'done')

    def test_sink_is_owned_work_and_failure_never_marks_done(self):
        entered,release=Event(),Event()
        def sink(*args):
            entered.set()
            if not release.wait(3): raise RuntimeError('test gate timed out')
            raise RuntimeError('private storage error')
        runner=make_runner(sink=sink); token=CancellationToken(); handle=runner.start(record(),token)
        try:
            self.assertTrue(entered.wait(2))
            self.assertFalse(handle.wait(0))
            self.assertEqual(runner.settle_completed(lambda *a:self.fail('must not settle running sink')),[])
        finally:
            release.set(); self.assertTrue(handle.wait(2))
        self.assertEqual(handle.failure,'result_persistence_failed')
        completions=[]
        self.assertEqual(runner.settle_completed(lambda *a: completions.append(a) or True),['1'])
        self.assertEqual(completions[0],('1',token,'failed'))

    def test_start_failure_leaves_no_worker_and_retry_can_start(self):
        runner=make_runner(); token=CancellationToken()
        with patch('artex.agent.runner.Thread.start', side_effect=RuntimeError('cannot start thread')):
            with self.assertRaises(RuntimeError): runner.start(record(),token)
        self.assertEqual(runner.handles(),())
        handle=runner.start(record(),token)
        self.assertTrue(handle.wait(2))
        runner.settle_completed(lambda *args: True)

    def test_cancel_before_start_launches_nothing(self):
        model=Model(); runner=make_runner(model); token=CancellationToken(); token.cancel()
        with self.assertRaises(TaskConflict): runner.start(record(),token)
        self.assertEqual(model.calls,0)
        self.assertEqual(runner.handles(),())

    def test_exact_generation_and_repeated_settle(self):
        runner=make_runner(); old=CancellationToken(); handle=runner.start(record(),old)
        self.assertTrue(handle.wait(2)); old.cancel()
        new=CancellationToken(); newer=runner.start(record(),new)
        self.assertTrue(newer.wait(2)); callbacks=[]
        self.assertEqual(runner.settle_completed(lambda task,token,status: callbacks.append(token) or (token is new)),['1'])
        self.assertEqual(callbacks,[new])
        self.assertEqual(runner.settle_completed(lambda *args:self.fail('duplicate')),[])

    def test_manager_old_generation_cannot_complete_resumed_task(self):
        entered,release=Event(),Event(); runner=make_runner(Model(entered,release))
        manager=TaskManager(Repository(),runner=runner,dto_enricher=enrich); manager.load_existing()
        self.assertTrue(manager.start('1')); old=runner.handles()[0]
        self.assertTrue(entered.wait(2)); old.cancel('paused_by_user'); release.set()
        self.assertTrue(old.wait(2)); manager.pause('1',timeout=1)
        self.assertTrue(manager.start('1')); new=runner.handles()[0]
        self.assertIsNot(old.token,new.token)
        self.assertTrue(new.wait(2))
        self.assertFalse(manager.complete('1',old.token,'done'))
        self.assertEqual(manager.status('1'),'running')
        self.assertEqual(runner.settle_completed(manager.complete),['1'])
        self.assertEqual(manager.status('1'),'done')

    def test_manager_start_failure_compensates_without_live_thread(self):
        runner=make_runner(); manager=TaskManager(Repository(),runner=runner,dto_enricher=enrich)
        manager.load_existing()
        with patch('artex.agent.runner.Thread.start',side_effect=RuntimeError('fixture startup failure')):
            with self.assertRaises(RuntimeError): manager.start('1')
        self.assertEqual(runner.handles(),())
        self.assertTrue(manager.lifecycle_snapshot('1')['paused'])

    def test_rejected_completion_keeps_generation_pending(self):
        runner=make_runner(); handle=runner.start(record(),CancellationToken()); self.assertTrue(handle.wait(2))
        self.assertEqual(runner.settle_completed(lambda *args:False),[])
        self.assertEqual(runner.handles(),(handle,))
        self.assertEqual(runner.settle_completed(lambda *args:True),['1'])

    def test_capacity_bounded_until_settlement(self):
        runner=make_runner(max_pending=1)
        handle=runner.start(record(1),CancellationToken()); self.assertTrue(handle.wait(2))
        with self.assertRaises(RuntimeUnavailable): runner.ready({'provisional':True})
        with self.assertRaises(RuntimeUnavailable): runner.start(record(2),CancellationToken())
        runner.settle_completed(lambda *args:True)
        next_handle=runner.start(record(2),CancellationToken()); self.assertTrue(next_handle.wait(2))
        runner.settle_completed(lambda *args:True)

    def test_bootstrap_validation_is_pure_and_rejects_invalid_plan(self):
        model=Model(); runner=make_runner(model)
        self.assertTrue(runner.ready({'provisional':True}))
        runner.validate_create('x','y',{},False)
        self.assertEqual(model.calls,0)
        bad=make_runner(bootstrap=lambda task:RunPlan({'messages':[{'role':'user','content':[{'type':'text','text':7}]}]}))
        with self.assertRaises(RuntimeUnavailable): bad.ready({'provisional':True})


if __name__=='__main__':unittest.main()
