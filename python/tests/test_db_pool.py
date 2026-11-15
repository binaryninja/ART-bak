"""Controlled-thread pool contracts, with no database connections."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event,Lock,get_ident
import unittest
from unittest.mock import patch
from artex.db.pool import ExecutorPool,PoolClosed,PoolTimeout,PoolFactoryError
from artex.db.core import Query

class Executor:
    def __init__(self,id):
        self.id=id;self.calls=[];self.closed=False;self.close_count=0;self.depth=0
        self.fail=False;self.fail_commit=False;self.fail_rollback=False;self.fail_close=False
    @contextmanager
    def transaction(self):
        self.depth+=1;self.calls.append(('begin',self.depth,get_ident()))
        try:
            yield self
            if self.fail_commit:raise RuntimeError('inert commit failure')
        except BaseException:
            self.calls.append(('rollback',self.depth,get_ident()))
            if self.fail_rollback:raise RuntimeError('inert rollback failure')
            raise
        else:self.calls.append(('commit',self.depth,get_ident()))
        finally:self.depth-=1
    def operation(self,mode,q):
        assert not self.closed
        self.calls.append((mode,q,get_ident()))
        if self.fail:raise RuntimeError('inert SQL failure')
        return self.id
    def fetch_one(self,q):return (self.operation('one',q),)
    def fetch_all(self,q):return [(self.operation('all',q),)]
    def execute(self,q):return self.operation('execute',q)
    def close(self):
        self.close_count+=1
        if self.fail_close:raise RuntimeError('inert close failure')
        self.closed=True

class Factory:
    def __init__(self):self.items=[];self.lock=Lock()
    def __call__(self):
        with self.lock:
            item=Executor(len(self.items)+1);self.items.append(item);return item

class PoolTests(unittest.TestCase):
    def make(self,**kwargs):
        factory=Factory();pool=ExecutorPool(factory,**kwargs)
        self.addCleanup(lambda:pool.close(timeout=1))
        return pool,factory

    def test_lazy_creation_query_identity_and_reuse(self):
        pool,factory=self.make();self.assertEqual(factory.items,[])
        q=Query('SELECT $1',(123,));self.assertEqual(pool.fetch_one(q),(1,));self.assertEqual(pool.fetch_all(q),[(1,)])
        self.assertEqual(len(factory.items),1);self.assertIs(factory.items[0].calls[0][1],q)
        self.assertEqual(pool.state().idle,1)
        state=pool.close();self.assertTrue(state.drained);self.assertEqual(factory.items[0].close_count,1)
        with self.assertRaises(PoolClosed):pool.execute(q)

    def test_nested_transactions_pin_connection_and_savepoints(self):
        pool,factory=self.make(max_size=2)
        with pool.transaction() as tx:
            self.assertIs(tx,pool);self.assertEqual(pool.execute(Query('outer')),1)
            with pool.transaction():self.assertEqual(pool.execute(Query('inner')),1)
            self.assertEqual(pool.state().leased,1);self.assertEqual(pool.state().idle,0)
        self.assertEqual(len(factory.items),1)
        self.assertEqual([call[:2] for call in factory.items[0].calls if call[0] in ('begin','commit')],
                         [('begin',1),('begin',2),('commit',2),('commit',1)])

    def test_cross_thread_transactions_never_share_leased_executor(self):
        pool,factory=self.make(max_size=2);entered=Event();release=Event()
        def worker():
            with pool.transaction():
                value=pool.execute(Query('worker'));entered.set();release.wait(2);return value
        with ThreadPoolExecutor(max_workers=1) as threads:
            future=threads.submit(worker)
            try:
                self.assertTrue(entered.wait(1))
                with pool.transaction():self.assertEqual(pool.execute(Query('main')),2)
                self.assertEqual(pool.state().leased,1)
            finally:release.set()
            self.assertEqual(future.result(2),1)
        self.assertNotEqual(factory.items[0].calls[0][2],factory.items[1].calls[0][2])

    def test_capacity_timeout_and_waiter_wakeup(self):
        pool,factory=self.make(max_size=1,acquire_timeout=0.02);entered=Event();release=Event()
        def hold():
            with pool.transaction():entered.set();release.wait(2)
        with ThreadPoolExecutor(max_workers=1) as threads:
            future=threads.submit(hold)
            try:
                self.assertTrue(entered.wait(1))
                with self.assertRaises(PoolTimeout):pool.execute(Query('blocked'))
                self.assertEqual(len(factory.items),1)
            finally:release.set()
            future.result(2)
        self.assertEqual(pool.execute(Query('available')),1)

    def test_operation_error_retires_and_replaces(self):
        pool,factory=self.make(max_size=1);pool.execute(Query('first'));factory.items[0].fail=True
        with self.assertRaises(RuntimeError):pool.fetch_one(Query('failure'))
        self.assertTrue(factory.items[0].closed);self.assertEqual(pool.state().total,0)
        self.assertEqual(pool.execute(Query('replacement')),2)

    def test_transaction_and_rollback_failure_never_reused(self):
        for rollback in (False,True):
            pool,factory=self.make(max_size=1)
            with self.assertRaises(RuntimeError):
                with pool.transaction():
                    factory.items[0].fail_rollback=rollback
                    raise RuntimeError('inert body failure')
            self.assertTrue(factory.items[0].closed);self.assertEqual(pool.state().total,0)
            self.assertEqual(pool.execute(Query('replacement')),2)

    def test_caught_nested_failure_retires_only_after_outer_completion(self):
        pool,factory=self.make(max_size=1)
        with pool.transaction():
            try:
                with pool.transaction():raise ValueError('inert savepoint failure')
            except ValueError:pass
            self.assertFalse(factory.items[0].closed);self.assertEqual(pool.execute(Query('after savepoint')),1)
        self.assertTrue(factory.items[0].closed)
        self.assertEqual(pool.execute(Query('replacement')),2)

    def test_commit_failure_and_close_failure_remain_visible(self):
        pool,factory=self.make(max_size=1,acquire_timeout=0)
        with self.assertRaises(RuntimeError):
            with pool.transaction():factory.items[0].fail_commit=True;factory.items[0].fail_close=True
        state=pool.state();self.assertEqual(state.failed_closes,1);self.assertEqual(state.total,1)
        with self.assertRaises(PoolTimeout):pool.execute(Query('no reuse'))
        self.assertFalse(pool.close(timeout=0).drained)
        factory.items[0].fail_close=False
        self.assertTrue(pool.close(timeout=0).drained);self.assertTrue(factory.items[0].closed)

    def test_close_reports_live_lease_and_wakes_waiter(self):
        pool,factory=self.make(max_size=1,acquire_timeout=2);entered=Event();release=Event();waiting=Event()
        def hold():
            with pool.transaction():entered.set();release.wait(2);pool.execute(Query('drain existing'))
        def wait():waiting.set();return pool.execute(Query('waiting'))
        with ThreadPoolExecutor(max_workers=2) as threads:
            held=threads.submit(hold);self.assertTrue(entered.wait(1));blocked=threads.submit(wait);waiting.wait(1)
            try:
                result=pool.close(timeout=0.01);self.assertFalse(result.drained);self.assertEqual(result.state.leased,1)
                with self.assertRaises(PoolClosed):blocked.result(1)
            finally:release.set()
            held.result(2)
        self.assertTrue(pool.close(timeout=1).drained);self.assertTrue(factory.items[0].closed)

    def test_close_inside_transaction_does_not_deadlock(self):
        pool,factory=self.make()
        with pool.transaction():
            self.assertFalse(pool.close(timeout=1).drained)
            pool.execute(Query('finish'))
        self.assertTrue(pool.state().closed);self.assertEqual(pool.state().total,0)

    def test_close_while_factory_blocked_retires_on_return(self):
        entered=Event();release=Event();executor=Executor(1)
        def factory():entered.set();release.wait(2);return executor
        pool=ExecutorPool(factory,max_size=1);self.addCleanup(lambda:pool.close(timeout=1))
        with ThreadPoolExecutor(max_workers=1) as threads:
            future=threads.submit(pool.execute,Query('unused'));self.assertTrue(entered.wait(1))
            try:
                report=pool.close(timeout=0);self.assertFalse(report.drained);self.assertEqual(report.state.creating,1)
            finally:release.set()
            with self.assertRaises(PoolClosed):future.result(2)
        self.assertTrue(executor.closed);self.assertTrue(pool.close(timeout=0).drained)

    def test_factory_failure_releases_capacity_and_redacts(self):
        count=[0];executor=Executor(1)
        def factory():
            count[0]+=1
            if count[0]==1:raise RuntimeError('dummy-secret-dsn')
            return executor
        pool=ExecutorPool(factory,max_size=1);self.addCleanup(lambda:pool.close(timeout=1))
        with self.assertRaises(PoolFactoryError) as caught:pool.execute(Query('first'))
        self.assertNotIn('dummy-secret',str(caught.exception));self.assertEqual(pool.state().total,0)
        self.assertEqual(pool.execute(Query('second')),1)

    def test_duplicate_factory_object_cannot_be_shared_or_closed(self):
        executor=Executor(1);pool=ExecutorPool(lambda:executor,max_size=2)
        self.addCleanup(lambda:pool.close(timeout=1))
        with pool.transaction():
            with ThreadPoolExecutor(max_workers=1) as threads:
                with self.assertRaises(PoolFactoryError):threads.submit(pool.execute,Query('duplicate')).result(1)
            self.assertFalse(executor.closed);self.assertEqual(pool.state().leased,1)
        self.assertEqual(pool.state().idle,1)

    def test_postgres_convenience_is_lazy_and_configuration_bounded(self):
        with patch('artex.db.postgres.PostgresExecutor.connect') as connect:
            pool=ExecutorPool.postgres('dummy-dsn',connect_timeout=2);self.assertEqual(connect.call_count,0)
            pool.close(timeout=0)
        for kwargs in ({'max_size':0},{'max_size':True},{'acquire_timeout':float('nan')},{'acquire_timeout':-1}):
            with self.assertRaises(ValueError):ExecutorPool(Factory(),**kwargs)

if __name__=='__main__':unittest.main()
