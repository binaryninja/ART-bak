"""Policy/setup-only fixtures; registration lane requires reviewed runner seam."""
import unittest
from dataclasses import replace
from datetime import datetime,timezone
from artex.agent.timed_lifecycle import *
from artex.agent.task_runtime import TaskRuntimeAssembly
from artex.agent.task_timing import TaskTimingPolicy
from artex.agent.runtime import Grant
from artex.agent.engine_runner import EngineRunner
from artex.manager import RuntimeUnavailable
from artex.db.early_completion import EarlyCompletionGrant
import test_task_runtime_application as fixture

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.calls=[];self.grants={}
        timing=TaskTimingPolicy('timed-fixture',lambda:100.,lambda:datetime(2026,1,1,tzinfo=timezone.utc),lambda *a:self.calls.append('stamp') or True,lambda *a:self.calls.append('dispatch') or Grant(True,True,True))
        old=self.f.runtime
        self.f.runtime=TaskRuntimeAssembly(old.executor,providers=old.providers,transcript=old.transcript,prompts=old._prompts,policy_revision=old._revision,current=old._current,lifecycle_guard=old._guard,authorize=old._authorize,asset_authorize=old._assets,validate_input=old._input,graph_catalog=old._catalog,cleanup_authorize=lambda *a:True,timing_policy=timing)
        def issue(r):
            self.calls.append('issue');grant=EarlyCompletionGrant(r);self.grants[id(r)]=grant;return grant
        self.policy=TimedLifecyclePolicy('lifecycle-fixture',timing,issue,lambda r,op:self.grants[id(r)],lambda r:True,lambda r:True)
    def test_policy_constructor_is_inert_and_exact(self):
        self.assertEqual(self.calls,[]);self.assertEqual(self.f.p.calls,[])
        self.assertTrue(self.policy.validate_runtime(self.f.runtime));self.assertEqual(self.calls,[])
        with self.assertRaises(ValueError):replace(self.policy,timing_policy=replace(self.policy.timing_policy)).validate_runtime(self.f.runtime)
    def test_tracking_and_type_refusal_before_inventory_reads(self):
        before=list(self.f.f.db.calls)
        with self.assertRaises(ValueError):self.f.build(timed_lifecycle_policy=self.policy)
        with self.assertRaises(ValueError):self.f.build(timed_lifecycle_policy=object(),track_manual_actions=True)
        self.assertEqual(self.f.f.db.calls,before);self.assertEqual(self.calls,[])
    def test_default_application_has_no_lifecycle(self):
        bundle=self.f.build();self.assertIsNone(bundle.timed_lifecycle);self.assertIsNone(bundle.manual_actions)
        self.assertEqual(self.calls,[])
    def test_setup_requires_actual_seam_or_is_inert_incomplete(self):
        if not hasattr(EngineRunner,'install_timed_lifecycle'):
            before=list(self.f.f.db.calls)
            with self.assertRaises(RuntimeUnavailable):self.f.build(timed_lifecycle_policy=self.policy,track_manual_actions=True)
            self.assertEqual(self.f.f.db.calls,before)
        else:
            bundle=self.f.build(timed_lifecycle_policy=self.policy,track_manual_actions=True)
            self.assertIs(bundle.timed_lifecycle.application,bundle)
            status=bundle.timed_lifecycle.status()
            self.assertFalse(status.finalization_configured);self.assertFalse(status.admission_ready)
            self.assertEqual(status.registered,0)
        self.assertEqual(self.calls,[]);self.assertEqual(self.f.p.calls,[])
    def test_mutated_policy_denied_before_inventory_reads(self):
        before=list(self.f.f.db.calls)
        object.__setattr__(self.policy,'freeze_authorize',None)
        with self.assertRaises(ValueError):self.f.build(timed_lifecycle_policy=self.policy,track_manual_actions=True)
        self.assertEqual(self.f.f.db.calls,before)

class ActualRegistrationTests(unittest.TestCase):
    """Real JWT goals→outer→host-owned registration, synthetic timeout pin only."""
    setUp=Tests.setUp
    def prepare(self,issue=None):
        f=self.f
        if issue is not None:self.policy=replace(self.policy,issue_frontier_grant=issue)
        original_factory=f.runtime.engine_factory
        def factory(*args):
            child=original_factory(*args)
            handle=f.bundle.runner.handles()[0]
            # Natural positive admission remains rejected. Only the prepared
            # configuration pins are adjusted for this protocol fixture.
            handle._configuration=(*handle._configuration[:-1],10)
            handle._task['timeout_seconds']=10
            f.bundle.manager._tasks['5']['timeout_seconds']=10
            return child
        f.runtime.engine_factory=factory
        old_query=f.f.db.fetch_all
        def query(q):
            if q.sql.startswith('SELECT timeout_seconds,first_run_at,deadline_at,\nCASE'):
                return [(10,None,None,None)]
            if q.sql.startswith('SELECT 1 FROM public.artex_task_bootstrap'):
                r=f.f.db.receipt
                return [(1,)] if r is not None and tuple(q.params)==tuple(r[:7]) else []
            if q.sql.startswith('SELECT id,kind,CASE WHEN octet_length(state)'):
                return [(n[0],n[1],n[4]) for n in f.f.db.rows if n[1] in ('goal','intent')]
            return old_query(q)
        f.f.db.fetch_all=query
        build=f.build
        def build_tracked(**kw):
            return build(timed_lifecycle_policy=self.policy,track_manual_actions=True,manual_action_capacity=getattr(self,'capacity',1024),**kw)
        f.build=build_tracked
        f.test_jwt_create_then_explicit_tick_uses_actual_goals_transport()
        return f.bundle
    def test_actual_registration_uses_same_bundle_and_never_stamps(self):
        bundle=self.prepare();h=bundle.runner.handles()[0]
        actions=bundle.timed_lifecycle.actions(h)
        self.assertIs(actions.handle,h);self.assertIs(actions.receipt,h._receipt)
        self.assertIs(actions.timing,h._child.timing)
        self.assertIs(actions.observer.host,bundle.host);self.assertIs(actions.frontier.host,bundle.host)
        self.assertIs(actions.frontier._manual_owner,bundle.manual_actions)
        self.assertIsNone(actions.finalizer);self.assertIsNone(actions.timing.store.lease)
        self.assertEqual(self.calls,['issue']);self.assertEqual(self.f.p.calls,[1,1])
        self.assertFalse(bundle.timed_lifecycle.status().admission_ready)
        with self.assertRaises(Exception):bundle.timed_lifecycle.register_prepared(h)
    def test_partial_registration_retained_and_next_tick_fenced(self):
        self.capacity=1
        with self.assertRaises(Exception):self.prepare()
        bundle=self.f.bundle;h=bundle.runner.handles()[0]
        self.assertTrue(bundle.timed_lifecycle.failed(h))
        entry=bundle.timed_lifecycle._entries[h]
        self.assertGreaterEqual(len(entry.products),2)
        count=len(self.f.p.calls)
        with self.assertRaises(Exception):bundle.host.advance_owned(lambda:bundle.runner.advance(h.task_id,h.token,1.))
        self.assertEqual(len(self.f.p.calls),count)
        self.assertIn(h,bundle.runner.handles())
        self.assertFalse(bundle.host.drain(0).all_settled)
    def test_missing_permit_is_retained_not_retried(self):
        with self.assertRaises(Exception):self.prepare(lambda r:True)
        bundle=self.f.bundle;h=bundle.runner.handles()[0]
        self.assertTrue(bundle.timed_lifecycle.failed(h));count=len(self.f.p.calls)
        with self.assertRaises(Exception):bundle.host.advance_owned(lambda:bundle.runner.advance(h.task_id,h.token,1.))
        self.assertEqual(len(self.f.p.calls),count)

    def test_incomplete_final_configuration_blocks_actual_next_owned_step(self):
        bundle=self.prepare();h=bundle.runner.handles()[0];count=len(self.f.p.calls)
        with self.assertRaises(RuntimeUnavailable):bundle.host.advance_owned(lambda:bundle.runner.advance(h.task_id,h.token,1.))
        self.assertEqual(len(self.f.p.calls),count);self.assertIsNone(h._child.timing.store.lease)
        self.assertIn(h,bundle.runner.handles());self.assertFalse(bundle.host.drain(0).all_settled)
