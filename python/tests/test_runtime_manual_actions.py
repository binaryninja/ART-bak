"""Manual registry composition/shutdown; inert providers/SQL, no socket bind."""
import json,unittest
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch
from datetime import datetime,timezone,timedelta
from artex.server.task_runtime_setup import build_task_runtime_application
from artex.server.runtime_listener import RuntimeListener,RuntimeResourceOwner
from artex.agent.manual_action_owner import ManualActionOwner,ManualActionSnapshot
from test_application import TestCodec
import test_runtime_listener as fixture

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        old=self.f.bundle
        self.bundle=build_task_runtime_application(old.runtime,b'x'*32,TestCodec(),
            live_last_activity=lambda id:0,profile_available=lambda id:True,single_process=True,
            track_manual_actions=True,manual_action_capacity=10)
        self.manual=self.bundle.manual_actions
        self.listener=RuntimeListener(self.bundle,self.f.owner)
    def test_optin_same_host_claimed_inert_and_default_none(self):
        self.assertIsNone(self.f.bundle.manual_actions)
        self.assertIs(type(self.manual),ManualActionOwner)
        self.assertIs(self.manual.host,self.bundle.host)
        self.assertIs(self.manual._application,self.bundle)
        self.assertTrue(self.manual.snapshot().drained)
        self.assertFalse(self.manual.snapshot().sealed)
        self.assertEqual(self.f.calls,[]);self.assertEqual(self.f.f.p.calls,[])
    def test_invalid_options_fail_before_startup_queries(self):
        before=list(self.f.f.f.db.calls)
        with self.assertRaises(ValueError):
            build_task_runtime_application(self.bundle.runtime,b'x'*32,TestCodec(),live_last_activity=lambda id:0,
                single_process=True,track_manual_actions=1)
        self.assertEqual(self.f.f.f.db.calls,before)
    def test_sealed_before_external_admission_callback_and_callbacks_unlocked(self):
        seen=[]
        def admissions():
            seen.append(self.manual.snapshot().sealed)
            with ThreadPoolExecutor(max_workers=1) as pool:
                seen.append(pool.submit(self.listener.stop,0).result(timeout=1).reason)
            return True
        owner=RuntimeResourceOwner(self.f.pool,admissions,self.f.owner.cancel_owned,self.f.owner.close_resources)
        self.listener=RuntimeListener(self.bundle,owner)
        self.assertTrue(self.listener.stop(1).stopped)
        self.assertEqual(seen,[True,'shutdown_busy'])
    def test_replaced_registry_wait_cannot_authorize_pool_close(self):
        with patch.object(self.manual,'wait',return_value=ManualActionSnapshot(0,0,True)),patch.object(self.f.pool,'close') as close:
            result=self.listener.stop(0)
            self.assertEqual(result.reason,'ownership_changed');close.assert_not_called()
            self.assertEqual(self.f.calls,[])
        self.assertTrue(self.listener.stop(1).stopped)
    def test_foreign_or_replaced_owner_denied(self):
        from dataclasses import replace
        foreign=ManualActionOwner(self.f.bundle.host)
        with self.assertRaises(Exception):RuntimeListener(replace(self.bundle,manual_actions=foreign),self.f.owner)
        object.__setattr__(self.bundle,'manual_actions',None)
        self.assertEqual(self.listener.stop(0).reason,'ownership_changed')
        self.assertEqual(self.f.calls,[])

    def prepare_finalizer(self):
        """Use real bundle host/pool, then existing inert final SQL fixture."""
        from artex.providers import Response
        messages=[{'role':'assistant','content':None,'tool_calls':[{'id':'g1','type':'function','function':{'name':'set_goals','arguments':json.dumps({'goals':[{'text':'explicit outcome','vulnclass':''}]})}}]},
                  {'role':'assistant','content':'goals submitted'}]
        def transport(config):
            def invoke(*a,**kw):
                msg=messages.pop(0)
                return Response(200,json.dumps({'choices':[{'message':msg,'finish_reason':'tool_calls' if 'tool_calls' in msg else 'stop'}],'usage':{'prompt_tokens':1,'completion_tokens':1}}))
            return invoke
        self.f.f.p.factory._transport_factory=transport
        self.bundle.facade.start('5');self.bundle.tick()
        handle=self.bundle.runner.handles()[0]
        self.assertEqual(handle.phase,'engine')
        # Protocol-only positive pins; actual application admission stays closed.
        handle._configuration=(*handle._configuration[:-1],10);handle._task['timeout_seconds']=10
        self.bundle.manager._tasks['5']['timeout_seconds']=10
        start=datetime(2026,1,1,tzinfo=timezone.utc);end=start+timedelta(seconds=10)
        outer=self
        class ObserverFixture(unittest.TestCase):
            def setUp(inner):
                from artex.db.task_deadline import TaskDeadlineStore
                from artex.agent.deadline import OwnedGenerationClock
                from artex.agent.expiry_observer import OwnedExpiryObserver
                inner.host=outer.bundle.host;inner.handle=handle;inner.expired=False
                old=outer.f.pool.fetch_all
                def query(q):
                    if 'deadline_at<=clock_timestamp()' in q.sql:return [(10,start,end,inner.expired)]
                    if q.sql.startswith('SELECT timeout_seconds,first_run_at'):return [(10,start,end)]
                    if q.sql.startswith('SELECT timeout_seconds'):return [(10,)]
                    if q.sql.startswith('SELECT 1 FROM public.artex_task_bootstrap'):return [(1,)]
                    if q.sql.startswith('UPDATE tasks SET first_run_at'):return [(start,end)]
                    return old(q)
                outer.f.pool.fetch_all=query
                inner.store=TaskDeadlineStore(outer.f.pool,manager=inner.host.manager,handle=handle,receipt=handle._receipt,
                    lifecycle_guard=handle.coordinator.guard,authorize=lambda *a:True)
                inner.kernel=OwnedGenerationClock(inner.store,10,monotonic_clock=lambda:100.)
                inner.observer=OwnedExpiryObserver(inner.host,inner.kernel,authorize_observation=lambda r:True,authorize_freeze=lambda r:True)
            def stamp(inner):return inner.kernel.read_or_stamp()
        import test_owned_finalization as finalfixture
        with patch.object(finalfixture.fixture,'Tests',ObserverFixture):
            final=finalfixture.Tests();final.setUp()
        self.addCleanup(final.doCleanups)
        self.manual.register_observer(final.f.observer);self.manual.register_finalizer(final.controller)
        return final
    def test_actual_post_manager_retirement_tail_retains_executor_until_return(self):
        from artex.db.final_settlement import FinalSettlementStore
        final=self.prepare_finalizer();entered=Event();release=Event();original=FinalSettlementStore.settle
        def settle(store):
            receipt=original(store);entered.set()
            if not release.wait(3):raise RuntimeError('fixture tail release timeout')
            return receipt
        with patch.object(FinalSettlementStore,'settle',settle),ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(final.controller.run_once)
            try:
                self.assertTrue(entered.wait(2))
                self.assertEqual(self.bundle.manager.deadline_owners(),())
                self.assertEqual(self.manual.snapshot().active,1)
                with patch.object(self.f.pool,'close') as close:
                    result=self.listener.stop(0)
                    self.assertFalse(result.stopped)
                    self.assertNotIn('resources',self.f.calls);close.assert_not_called()
            finally:release.set()
            self.assertEqual(future.result(timeout=3).phase,'settled')
        self.assertTrue(self.manual.wait(0).drained)
        self.assertTrue(self.listener.stop(1).stopped)
    def test_actual_cancelled_final_action_remains_unresolved_and_storage_retained(self):
        final=self.prepare_finalizer()
        final.hook=lambda:final.controller.cancel()
        with self.assertRaises(Exception):final.controller.run_once()
        self.assertEqual(self.manual.snapshot().unresolved,1)
        with patch.object(self.f.pool,'close') as close:
            self.assertFalse(self.listener.stop(0).stopped)
            self.assertFalse(self.listener.stop(0).stopped)
            close.assert_not_called();self.assertNotIn('resources',self.f.calls)
