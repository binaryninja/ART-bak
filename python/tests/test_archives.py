"""Archive state SQL tests; no archive files, task execution or live database."""
from contextlib import contextmanager
from datetime import datetime,timezone,timedelta
from pathlib import Path
import unittest
from artex.db.archives import ArchiveStore,ArchiveConflict,ArchiveMissing,ArchiveUnavailable,_QUEUE,_TASK,_DEPENDENTS,_SOURCES
NOW=datetime(2026,11,15,tzinfo=timezone.utc)

def row(state='archive_queued',task=2):
    return (1,task,state,'queued',0,'','[]',3,'private/path','',0,0,'name','description','goal','done',None,'','[]',0,'{}','{}',None,NOW,NOW,NOW)

class Executor:
    def __init__(self):
        self.calls=[];self.events=[];self.task=[('name','description','goal','done',None,'',False,False,None)]
        self.dependencies=[];self.sources=[];self.archive=[row()];self.claim=[(1,'archive_queued')]
    @contextmanager
    def transaction(self):
        self.events.append('begin')
        try:yield self
        except Exception:self.events.append('rollback');raise
        else:self.events.append('commit')
    def execute(self,q):self.calls.append(q);return 1
    def fetch_all(self,q):
        self.calls.append(q)
        if q.sql==_TASK:return self.task
        if q.sql==_DEPENDENTS:return self.dependencies
        if q.sql==_SOURCES:return self.sources
        if q.sql.startswith('SELECT id,state'):return self.claim
        return self.archive

class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.db=Executor();self.guards=[]
        @contextmanager
        def guard(task,operation):self.guards.append((task,operation));yield
        self.store=ArchiveStore(self.db,2,lifecycle_guard=guard,clock=lambda:NOW)
    def test_source_queue_sql_unchanged(self):
        source=(Path(__file__).resolve().parents[2]/'db/task_archives.go').read_text()
        for sql in (_QUEUE,_TASK,_DEPENDENTS,_SOURCES):self.assertIn('`'+sql+'`',source)
    def test_get_scope_and_no_path_dto(self):
        item=self.store.get(1)
        self.assertEqual(self.db.calls[-1].params,(2,1));self.assertNotIn('archive_path',item.to_dict())
        self.assertEqual(item.to_dict()['warnings'],[])
    def test_queue_locked_lifecycle_format3(self):
        item=self.store.queue();self.assertEqual(item.state,'archive_queued')
        self.assertIn('FOR UPDATE OF t',self.db.calls[0].sql)
        insertion=next(q for q in self.db.calls if q.sql==_QUEUE)
        self.assertEqual(insertion.params[:3],(2,'archive_queued',3))
        self.assertIn("task_archives.state IN ('archive_failed')",insertion.sql)
        self.assertEqual(self.db.events,['begin','commit'])
    def test_queue_remaining_clock_and_sources(self):
        self.db.task=[('n','d','g','running',3,'cat',True,False,NOW+timedelta(seconds=30.9))];self.db.sources=[(5,),(7,)]
        self.store.queue();query=next(q for q in self.db.calls if q.sql==_QUEUE)
        self.assertEqual(query.params[-2:],([5,7],30))
    def test_queue_paused_terminal_and_dependents_guards(self):
        for paused,queued,status in ((False,True,'done'),(False,False,'running')):
            self.db.task=[('n','d','g',status,None,'',paused,queued,None)]
            with self.assertRaises(ArchiveConflict):self.store.queue()
        self.db.task=[('n','d','g','done',None,'',False,False,None)];self.db.dependencies=[(9,)]
        with self.assertRaises(ArchiveConflict):self.store.queue()
        self.assertFalse(any(q.sql==_QUEUE for q in self.db.calls))
    def test_missing_parent_and_ready_state_never_requeue(self):
        self.db.task=[]
        with self.assertRaises(ArchiveMissing):self.store.queue()
        self.db.task=[('n','d','g','done',None,'',False,False,None)];self.db.archive=[row('ready')]
        with self.assertRaises(ArchiveConflict):self.store.queue()
        self.assertEqual(self.db.events[-1],'rollback')
    def test_claim_scoped_skip_locked_statecheck(self):
        self.db.archive=[row('archiving')];item=self.store.claim()
        self.assertEqual(item.state,'archiving')
        self.assertIn('FOR UPDATE SKIP LOCKED',self.db.calls[0].sql)
        self.assertEqual(self.db.calls[0].params,(2,))
        self.assertEqual(self.db.calls[1].params,(1,2,'archive_queued'))
    def test_empty_claim_no_update(self):
        self.db.claim=[];self.assertIsNone(self.store.claim());self.assertEqual(len(self.db.calls),1)
    def test_progress_revision_and_clamp(self):
        self.db.archive=[row('archiving')];item=self.store.get()
        self.store.progress(item,'writing',111)
        query=self.db.calls[-1];self.assertEqual(query.params,(1,2,'archiving',NOW,'writing',100))
        self.assertIn('updated_at=$4',query.sql)
        self.db.archive=[]
        with self.assertRaises(ArchiveConflict):self.store.progress(item,'writing',50)
    def test_failure_preserves_active_state_cas(self):
        self.db.archive=[row('archiving')];item=self.store.get();self.db.archive=[row('archive_failed')]
        failed=self.store.fail(item,'redacted failure')
        self.assertEqual(failed.state,'archive_failed')
        self.assertEqual(self.db.calls[-1].params,(1,2,'archiving',NOW,'archive_failed','redacted failure'))
        with self.assertRaises(ArchiveConflict):self.store.fail(failed)
    def test_missing_runtime_barrier_no_writes(self):
        store=ArchiveStore(self.db,2)
        with self.assertRaises(ArchiveUnavailable):store.queue()
        with self.assertRaises(ArchiveUnavailable):store.claim()
        self.assertEqual(self.db.calls,[])
    def test_crossscope_and_no_fake_completion(self):
        self.db.archive=[row(task=99)]
        with self.assertRaises(ArchiveUnavailable):self.store.get()
        for operation in (self.store.restore,self.store.delete,self.store.complete):
            with self.assertRaises(ArchiveUnavailable):operation(1)

if __name__=='__main__':unittest.main()
