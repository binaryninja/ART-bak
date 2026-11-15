"""Explicit bounded synchronous pool for PostgresExecutor-compatible factories.

No connections/threads are created at import or construction. A factory transfers
exclusive ownership of a fresh executor; its transaction() must commit/rollback
before exit, support nested savepoints, and its close() must release its connection.
Native Query $n parameters pass unchanged. Per-thread transaction leases are held
through outer commit/rollback; nested scopes pin the same executor. Passing the
pool to another thread does NOT transfer an existing transaction to that thread.

Acquisition timeout bounds waiting for pool capacity, not an uninterruptible
factory/connect callback. Configure the driver's connection timeout explicitly.
Any operation/transaction exception conservatively retires that executor after
rollback/outer release, including caught nested errors. No uncertain connection
is returned to the idle pool. Close errors remain tracked for explicit retry.
close(timeout) bounds waiting for active leases/factories, not a blocking driver
close callback; it reports unresolved work rather than claiming forced shutdown.
No automatic retry of SQL, mutations, failed transactions, or ambiguous commits.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Condition,local
import math
import time


class PoolClosed(RuntimeError):pass
class PoolTimeout(TimeoutError):pass
class PoolFactoryError(RuntimeError):pass


@dataclass(frozen=True)
class PoolState:
    total: int
    idle: int
    leased: int
    creating: int
    closing: int
    failed_closes: int
    closed: bool


@dataclass(frozen=True)
class PoolDrain:
    drained: bool
    state: PoolState


@dataclass
class _Lease:
    executor: object
    healthy: bool = True


def _timeout(value):
    if type(value) not in (int,float) or not math.isfinite(value) or value<0:
        raise ValueError('Timeout must be finite and nonnegative')
    return float(value)


class ExecutorPool:
    def __init__(self,factory,*,max_size=4,acquire_timeout=5.0):
        if not callable(factory):raise TypeError('Explicit executor factory required')
        if type(max_size) is not int or not 1<=max_size<=128:raise ValueError('Invalid maximum pool size')
        self._factory=factory;self._maximum=max_size;self._timeout=_timeout(acquire_timeout)
        self._condition=Condition();self._local=local();self._idle=[];self._failed=[];self._owned=set()
        self._total=0;self._leased=0;self._creating=0;self._closing=0;self._closed=False

    @classmethod
    def postgres(cls,dsn,*,max_size=4,acquire_timeout=5.0,**connect_options):
        """Explicit lazy psycopg convenience; no connect until first acquisition.

        Set connect_timeout in connect_options to bound connection establishment.
        No credentials are logged or included in the pool's representation.
        """
        from .postgres import PostgresExecutor
        return cls(lambda:PostgresExecutor.connect(dsn,**connect_options),max_size=max_size,acquire_timeout=acquire_timeout)

    def state(self):
        with self._condition:
            return PoolState(self._total,len(self._idle),self._leased,self._creating,self._closing,len(self._failed),self._closed)

    def _acquire(self):
        deadline=time.monotonic()+self._timeout
        with self._condition:
            while True:
                if self._closed:raise PoolClosed('Executor pool is closed')
                if self._idle:
                    self._leased+=1;return self._idle.pop()
                if self._total<self._maximum:
                    self._total+=1;self._creating+=1;break
                remaining=deadline-time.monotonic()
                if remaining<=0:raise PoolTimeout('Executor pool acquisition timed out')
                self._condition.wait(remaining)
        try:
            executor=self._factory()
        except BaseException as error:
            with self._condition:
                self._creating-=1;self._total-=1;self._condition.notify_all()
            # Process termination and cooperative cancellation are control flow,
            # not ordinary driver errors. Capacity is restored in either case.
            if not isinstance(error,Exception):raise
            raise PoolFactoryError('Executor factory failed') from None
        with self._condition:
            self._creating-=1
            if id(executor) in self._owned:
                # Do not close a duplicate: another thread may be using it.
                self._total-=1;self._condition.notify_all()
                raise PoolFactoryError('Factory returned an already owned executor')
            self._owned.add(id(executor))
            valid=all(callable(getattr(executor,name,None)) for name in ('fetch_one','fetch_all','execute','transaction','close'))
            if not self._closed and valid:
                self._leased+=1;self._condition.notify_all();return executor
            closed=self._closed
            self._condition.notify_all()
        self._retire(executor)
        if closed:raise PoolClosed('Executor pool closed during connection creation')
        raise PoolFactoryError('Factory returned an invalid executor')

    def _retire(self,executor):
        with self._condition:self._closing+=1;self._condition.notify_all()
        success=False
        try:executor.close();success=True
        except BaseException:pass  # retained in failed_closes; never silently reused
        finally:
            with self._condition:
                self._closing-=1
                if success:
                    self._total-=1;self._owned.remove(id(executor))
                else:self._failed.append(executor)
                self._condition.notify_all()

    def _release(self,executor,healthy):
        with self._condition:
            self._leased-=1
            if healthy and not self._closed:
                self._idle.append(executor);self._condition.notify_all();return
            self._condition.notify_all()
        self._retire(executor)

    @contextmanager
    def transaction(self):
        lease=getattr(self._local,'lease',None)
        if lease is not None:
            try:
                with lease.executor.transaction():yield self
            except BaseException:
                lease.healthy=False;raise
            return
        executor=self._acquire();lease=_Lease(executor);self._local.lease=lease
        try:
            try:
                with executor.transaction():yield self
            except BaseException:
                lease.healthy=False;raise
        finally:
            del self._local.lease
            self._release(executor,lease.healthy)

    @contextmanager
    def archive_snapshot_transaction(self):
        """Pin one fresh executor snapshot through commit; nested upgrades rejected."""
        if getattr(self._local,'lease',None) is not None:
            raise RuntimeError('Archive snapshot requires no existing pool transaction')
        executor=self._acquire();lease=_Lease(executor);self._local.lease=lease
        try:
            try:
                capability=getattr(executor,'archive_snapshot_transaction',None)
                if not callable(capability):raise PoolFactoryError('Executor lacks archive snapshot capability')
                with capability():yield self
            except BaseException:
                lease.healthy=False;raise
        finally:
            del self._local.lease
            self._release(executor,lease.healthy)

    def _operation(self,name,query):
        lease=getattr(self._local,'lease',None)
        if lease is not None:
            try:return getattr(lease.executor,name)(query)
            except BaseException:lease.healthy=False;raise
        executor=self._acquire();healthy=False
        try:
            result=getattr(executor,name)(query);healthy=True;return result
        finally:self._release(executor,healthy)

    def fetch_one(self,query):return self._operation('fetch_one',query)
    def fetch_all(self,query):return self._operation('fetch_all',query)
    def execute(self,query):return self._operation('execute',query)

    def close(self,*,timeout=5.0):
        timeout=_timeout(timeout);deadline=time.monotonic()+timeout
        with self._condition:
            self._closed=True
            retire=self._idle+self._failed;self._idle=[];self._failed=[]
            self._condition.notify_all()
        for executor in retire:self._retire(executor)
        # Waiting inside our own lease cannot make it drain; report honestly.
        if getattr(self._local,'lease',None) is None:
            with self._condition:
                while self._total and not self._failed:
                    remaining=deadline-time.monotonic()
                    if remaining<=0:break
                    self._condition.wait(remaining)
        state=self.state()
        return PoolDrain(state.total==0,state)

    def __enter__(self):
        with self._condition:
            if self._closed:raise PoolClosed('Executor pool is closed')
        return self

    def __exit__(self,*args):
        result=self.close()
        if not result.drained:raise PoolClosed('Executor pool has undrained leases or failed connection closes')
        return False
