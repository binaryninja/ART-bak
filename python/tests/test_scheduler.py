"""Tick coordination using inert adapters and the authored bounded agent fixture."""
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
import unittest
from artex.scheduler import FIFOScheduler,TaskSnapshot,LocalHostAdapter
from artex.manager import RuntimeUnavailable,TaskConflict
from test_agent_host import host_for
from test_agent_runner import make_runner
from test_manager import Repository,record,NOW


def task(key,**kwargs): return TaskSnapshot(key,'generation-'+str(key),float(key),queued=True,queue_mode='resume',**kwargs)


class Adapter:
    def __init__(self,rows):
        self.rows=rows;self.complete=True;self.running=0;self.pending=0;self.busy=False
        self.admitted=[];self.expired=[];self.unready=set();self.outcome={};self.gate=None
    def scheduler_snapshot(self,limit): return self.rows,self.complete
    def poll(self): return SimpleNamespace(busy=self.busy,running=self.running,pending=self.pending)
    def ready(self,row): return row.task_id not in self.unready
    def admit_queued(self,row):
        if self.gate: self.gate()
        result=self.outcome.get(row.task_id,'admitted')
        if result=='admitted': self.admitted.append(row.task_id);self.running+=1
        return result
    def settle_deadline(self,row,now): self.expired.append(row.task_id);return 'deferred'
    def drain(self,timeout):
        if self.gate:self.gate()
        return False


class SchedulerTests(unittest.TestCase):
    def make(self,rows,**kwargs):
        adapter=Adapter(rows);return adapter,FIFOScheduler(adapter,adapter,clock=lambda:100,**kwargs)
    def test_fifo_timestamp_and_numeric_tie(self):
        adapter,scheduler=self.make([task(8,queued_at=3),task(2,queued_at=3),task(1,queued_at=9)],limit=3)
        self.assertEqual(scheduler.tick().admitted,(2,8,1))
    def test_unready_paused_terminal_deleted_skip(self):
        rows=[task(1),replace(task(2),paused=True),replace(task(3),status='done'),replace(task(4),deleted=True),task(5)]
        adapter,scheduler=self.make(rows);adapter.unready={1}
        result=scheduler.tick();self.assertEqual(result.admitted,(5,));self.assertEqual(result.deferred,(1,))
    def test_pending_drain_reserves_slot(self):
        adapter,scheduler=self.make([task(1)]);adapter.pending=1
        self.assertEqual(scheduler.tick().admitted,())
    def test_duplicate_lagging_snapshot_not_readmitted(self):
        adapter,scheduler=self.make([task(1)])
        scheduler.tick();adapter.running=0
        self.assertEqual(scheduler.tick().admitted,());self.assertEqual(adapter.admitted,[1])
        adapter.rows=[replace(task(1),generation='new')]
        self.assertEqual(scheduler.tick().admitted,(1,))
    def test_deadline_stamped_paused_is_deferred_never_started(self):
        adapter,scheduler=self.make([task(1,deadline_at=90),replace(task(2,deadline_at=99),paused=True),task(3)],limit=3)
        result=scheduler.tick();self.assertEqual(result.admitted,(3,));self.assertEqual(result.deferred,(1,2))
        self.assertEqual(adapter.expired,[1,2]);self.assertEqual(result.settled_deadlines,())
    def test_bounded_actions_and_complete_snapshot(self):
        adapter,scheduler=self.make([task(1),task(2)],limit=2,max_actions=1)
        self.assertEqual(scheduler.tick().admitted,(1,))
        adapter.complete=False
        with self.assertRaises(RuntimeUnavailable):scheduler.tick()
    def test_stale_admission_does_not_consume_slot(self):
        adapter,scheduler=self.make([task(1),task(2)]);adapter.outcome[1]='stale'
        self.assertEqual(scheduler.tick().admitted,(2,))
    def test_unknown_mode_never_inferred(self):
        adapter,scheduler=self.make([replace(task(1),queue_mode='')])
        self.assertEqual(scheduler.tick().deferred,(1,));self.assertEqual(adapter.admitted,[])
    def test_concurrent_tick_and_drain_fences(self):
        adapter,scheduler=self.make([task(1)]);entered=Event();release=Event()
        adapter.gate=lambda:(entered.set(),release.wait(2))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(scheduler.tick)
            self.assertTrue(entered.wait(1));self.assertTrue(scheduler.tick().busy)
            release.set();future.result()
        entered.clear();release.clear()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(scheduler.drain,1)
            self.assertTrue(entered.wait(1));self.assertTrue(scheduler.tick().busy)
            release.set();self.assertFalse(future.result())
    def test_actual_local_host_queue_launch_and_settlement(self):
        host=host_for(make_runner(),Repository(record(queued=True,queued_at=NOW,queue_mode='resume')))
        with self.assertRaises(RuntimeUnavailable):LocalHostAdapter(host)
        adapter=LocalHostAdapter(host,single_process=True)
        scheduler=FIFOScheduler(adapter,adapter,clock=lambda:NOW.timestamp())
        self.assertEqual(scheduler.tick().admitted,(1,))
        self.assertFalse(host.manager.lifecycle_snapshot('1')['queued'])
        host.drain(2)
        self.assertEqual(scheduler.tick().admitted,())
        self.assertEqual(host.manager.status('1'),'done')
    def test_actual_local_snapshot_change_rejects_old_generation(self):
        host=host_for(make_runner(),Repository(record(queued=True,queued_at=NOW,queue_mode='resume')))
        adapter=LocalHostAdapter(host,single_process=True)
        rows,_=adapter.scheduler_snapshot(10)
        host.pause('1')
        self.assertEqual(adapter.admit_queued(rows[0]),'stale')
        self.assertEqual(host.runner.handles(),())
    def test_expected_readiness_failure_does_not_starve_fifo(self):
        host=host_for(make_runner(),Repository(record(1,queued=True,queued_at=NOW,queue_mode='resume'),record(2,queued=True,queued_at=NOW,queue_mode='resume')))
        original=host.runner.ready
        def readiness(row):
            if str(row['id'])=='1': raise RuntimeUnavailable('profile unavailable')
            return original(row)
        host.runner.ready=readiness
        adapter=LocalHostAdapter(host,single_process=True)
        scheduler=FIFOScheduler(adapter,adapter,clock=lambda:NOW.timestamp())
        result=scheduler.tick()
        self.assertEqual(result.admitted,(2,));self.assertEqual(result.deferred,(1,))
        host.drain(2)

    def test_deferred_admission_live_handle_reserves_capacity(self):
        adapter,scheduler=self.make([task(1),task(2)])
        def attempt(row):
            adapter.running=1
            return 'deferred'
        adapter.admit_queued=attempt
        result=scheduler.tick()
        self.assertEqual(result.deferred,(1,));self.assertEqual(result.admitted,())

    def test_reentrant_drain_fails_instead_of_deadlocking(self):
        adapter,scheduler=self.make([task(1)])
        adapter.gate=lambda:scheduler.drain(0)
        with self.assertRaises(RuntimeUnavailable):scheduler.tick()
        adapter.gate=None
        self.assertEqual(scheduler.tick().admitted,(1,))

    def test_unexpected_readiness_error_propagates(self):
        host=host_for(make_runner(),Repository(record(queued=True,queued_at=NOW,queue_mode='resume')))
        adapter=LocalHostAdapter(host,single_process=True)
        def readiness(row): raise OSError('unexpected adapter failure')
        host.runner.ready=readiness
        rows,_=adapter.scheduler_snapshot(10)
        with self.assertRaises(OSError): adapter.ready(rows[0])

    def test_manager_queue_requires_exact_snapshot(self):
        host=host_for(make_runner(),Repository(record(queued=True,queued_at=NOW,queue_mode='resume')))
        with self.assertRaises(TaskConflict):host.start('1')
        snapshot=host.manager.lifecycle_snapshot('1');snapshot['queue_mode']='bootstrap'
        with self.assertRaises(TaskConflict):host.start_queued('1',snapshot)
        self.assertEqual(host.runner.handles(),())

if __name__=='__main__':unittest.main()
