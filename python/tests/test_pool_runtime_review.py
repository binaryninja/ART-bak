"""Independent inert pool/runtime lifecycle checks; no connections/providers."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import unittest
from artex.db.pool import ExecutorPool, PoolClosed
from artex.db.core import Query
from test_db_pool import Executor
from test_runtime_setup import Database
from test_application import TestCodec
from artex.server.runtime_setup import build_profile_runtime_application
from artex.health import Registry

class Cancelled(BaseException):
    pass

class PoolRuntimeReviewTests(unittest.TestCase):
    def test_factory_cancellation_propagates_and_restores_capacity(self):
        executor = Executor(1)
        attempts = []
        def factory():
            attempts.append(1)
            if len(attempts) == 1:
                raise Cancelled()
            return executor
        pool = ExecutorPool(factory, max_size=1)
        try:
            with self.assertRaises(Cancelled):
                pool.execute(Query('cancelled'))
            self.assertEqual(pool.state().total, 0)
            self.assertEqual(pool.state().creating, 0)
            self.assertEqual(pool.execute(Query('next')), 1)
        finally:
            self.assertTrue(pool.close(timeout=0).drained)

    def test_cancellation_retires_lease_without_reuse(self):
        executor = Executor(1)
        pool = ExecutorPool(lambda: executor, max_size=1)
        try:
            with self.assertRaises(Cancelled):
                with pool.transaction():
                    pool.execute(Query('inert'))
                    raise Cancelled()
            self.assertTrue(executor.closed)
            self.assertEqual(executor.close_count, 1)
            self.assertEqual(pool.state().total, 0)
        finally:
            self.assertTrue(pool.close(timeout=0).drained)

    def test_concurrent_close_does_not_close_same_idle_executor_twice(self):
        entered, release = Event(), Event()
        class SlowClose(Executor):
            def close(self):
                entered.set()
                if not release.wait(2):
                    raise AssertionError('test release missing')
                super().close()
        executor = SlowClose(1)
        pool = ExecutorPool(lambda: executor)
        pool.execute(Query('inert'))
        with ThreadPoolExecutor(max_workers=1) as workers:
            future = workers.submit(pool.close, timeout=0)
            try:
                self.assertTrue(entered.wait(1))
                second = pool.close(timeout=0)
                self.assertFalse(second.drained)
                self.assertEqual(second.state.closing, 1)
                with self.assertRaises(PoolClosed):
                    pool.execute(Query('never'))
            finally:
                release.set()
            self.assertTrue(future.result(2).drained)
        self.assertEqual(executor.close_count, 1)

    def test_failed_runtime_reload_can_recover_only_by_explicit_reload(self):
        from artex.db.profiles import LLMProfile
        from artex.server.profiles import ProfileApplyPending
        database = Database()
        provider_calls = []
        def factory(config):
            def transport(*args):
                provider_calls.append(args)
                raise AssertionError('must never execute')
            return transport
        bundle = build_profile_runtime_application(database, b'x'*32, TestCodec(),
            transport_factory=factory, health=Registry())
        coordinator = bundle.profile_coordinator
        database.fail_pool = True
        with self.assertRaises(ProfileApplyPending):
            coordinator.save(LLMProfile(name='fixture', model='dummy', api_key='inert-key'))
        self.assertEqual(len(database.profiles), 1)
        self.assertFalse(coordinator.snapshot()['configured'])
        database.fail_pool = False
        coordinator.reload()
        coordinator.set_active(1)
        self.assertTrue(coordinator.snapshot()['configured'])
        self.assertEqual(provider_calls, [])
