"""Independent archive boundary review, injected SQL executor only."""
from contextlib import contextmanager
from datetime import timedelta
import unittest
from artex.db.archives import ArchiveStore,ArchiveConflict,ArchiveUnavailable
from test_archives import Executor,row,NOW

class ArchiveReviewTests(unittest.TestCase):
    def test_guard_encloses_transaction_commit_and_rollback(self):
        db=Executor()
        @contextmanager
        def guard(task,operation):
            db.events.append('guard_enter')
            try: yield
            finally: db.events.append('guard_exit')
        store=ArchiveStore(db,2,lifecycle_guard=guard)
        store.queue()
        self.assertEqual(db.events,['guard_enter','begin','commit','guard_exit'])
        db.events=[];db.task=[]
        with self.assertRaises(LookupError):store.queue()
        self.assertEqual(db.events,['guard_enter','begin','rollback','guard_exit'])

    def test_unavailable_or_rejecting_guard_never_opens_transaction(self):
        db=Executor()
        @contextmanager
        def guard(task,operation):
            raise ArchiveConflict('workers not drained')
            yield
        store=ArchiveStore(db,2,lifecycle_guard=guard)
        for call in (store.queue,store.claim):
            with self.assertRaises(ArchiveConflict):call()
        self.assertFalse(db.calls);self.assertFalse(db.events)

    def test_fresh_revision_is_required_for_second_write(self):
        class CASExecutor(Executor):
            def __init__(self):
                super().__init__();self.archive=[row('archiving')]
            def fetch_all(self,q):
                if q.sql.startswith('UPDATE task_archives'):
                    self.calls.append(q)
                    current=list(self.archive[0])
                    if q.params[:4]!=(current[0],current[1],current[2],current[-1]):return []
                    current[-1]+=timedelta(microseconds=1)
                    current[3],current[4]=q.params[4:6]
                    self.archive=[tuple(current)]
                    return self.archive
                return super().fetch_all(q)
        db=CASExecutor()
        @contextmanager
        def guard(*args):yield
        store=ArchiveStore(db,2,lifecycle_guard=guard)
        first=store.get();second=store.progress(first,'writing',10)
        self.assertNotEqual(first.updated_at,second.updated_at)
        with self.assertRaises(ArchiveConflict):store.progress(first,'writing',20)
        third=store.progress(second,'writing',20)
        self.assertNotEqual(second.updated_at,third.updated_at)

    def test_non_active_revision_cannot_mutate(self):
        db=Executor()
        @contextmanager
        def guard(*args):yield
        store=ArchiveStore(db,2,lifecycle_guard=guard)
        for state in ('ready','archive_queued','archive_failed','restore_queued','delete_queued'):
            db.archive=[row(state)];item=store.get();db.calls=[]
            for call in (lambda:store.progress(item,'finished',100),lambda:store.fail(item,'error')):
                with self.assertRaises(ArchiveConflict):call()
            self.assertFalse(db.calls)

    def test_claim_scope_and_exposed_mutations_do_not_complete(self):
        db=Executor();store=ArchiveStore(db,2)
        for call in (store.complete,store.restore,store.delete):
            with self.assertRaises(ArchiveUnavailable):call(1)
        self.assertFalse(db.calls)
        db.archive=[row('ready')]
        public=store.get().to_dict()
        self.assertNotIn('archive_path',public)
        self.assertNotIn('private/path',repr(public))

    def test_budget_floor_expired_paused_task(self):
        db=Executor()
        @contextmanager
        def guard(*args):yield
        db.task=[('n','d','g','running',None,'',True,False,NOW-timedelta(seconds=3))]
        store=ArchiveStore(db,2,lifecycle_guard=guard,clock=lambda:NOW)
        store.queue()
        insert=next(q for q in db.calls if q.sql.startswith('INSERT INTO task_archives'))
        self.assertEqual(insert.params[-1],0)

if __name__=='__main__':unittest.main()
