"""Offline task-scoped worker persistence and inert transcript adapters."""
from contextlib import contextmanager
from datetime import datetime,timezone
from unittest.mock import Mock
import unittest
from artex.db.workers import WorkerStore,WorkerUnavailable,WorkerMissing,WorkerConflict


def row(state='running',version=3):
    return (7,'intent','{}',0,state,'planner','worker','','',datetime(2026,11,15,tzinfo=timezone.utc),version)


class Executor:
    def __init__(self):self.queries=[];self.events=[];self.parent=[(1,)];self.rows=[row()];self.count=1
    @contextmanager
    def transaction(self):
        self.events.append('begin')
        try:yield self
        except Exception:self.events.append('rollback');raise
        else:self.events.append('commit')
    def fetch_all(self,q):self.queries.append(q);return self.parent if 'SELECT id FROM tasks' in q.sql else self.rows
    def fetch_one(self,q):self.queries.append(q);return (7,)
    def execute(self,q):self.queries.append(q);return self.count


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.db=Executor();self.guards=[]
        @contextmanager
        def guard(task,exploration,write):
            self.guards.append((task,exploration,write));yield
        self.transcripts=Mock()
        self.store=WorkerStore(self.db,1,10,lifetime_guard=guard,transcripts=self.transcripts)
    def test_get_checks_parent_and_scope(self):
        record=self.store.get(7)
        self.assertEqual(record.version,3);self.assertEqual(record.node.kind,'intent')
        parent,node=self.db.queries
        self.assertEqual(parent.params,(1,10));self.assertIn('deleted_at IS NULL AND archived_at IS NULL',parent.sql)
        self.assertIn('NOT EXISTS (SELECT 1 FROM task_archives',parent.sql)
        self.assertEqual(node.params,(10,7));self.assertIn("kind='intent'",node.sql)
    def test_parent_missing_stops_all_reads(self):
        self.db.parent=[]
        with self.assertRaises(WorkerMissing):self.store.read_transcript(7)
        self.transcripts.read.assert_not_called();self.assertEqual(len(self.db.queries),1)
        self.assertEqual(self.db.events,['begin','rollback'])
    def test_list_bound_cursor(self):
        self.store.list(before=99,limit=20)
        self.assertEqual(self.db.queries[-1].params,(10,99,20))
        self.assertIn('ORDER BY id DESC LIMIT $3',self.db.queries[-1].sql)
    def test_create_bound_json_and_priority(self):
        self.assertEqual(self.store.create({'text':"x');DROP --"},priority=2),7)
        query=self.db.queries[-1]
        self.assertNotIn('DROP',query.sql);self.assertIn('DROP',query.params[1])
        self.assertEqual(self.guards,[(1,10,True)])
        self.assertEqual(self.db.events,['begin','commit'])
    def test_claim_version_owner_scoped(self):
        self.assertTrue(self.store.claim(7,'worker1',3))
        query=self.db.queries[-1]
        self.assertEqual(query.params,('worker1',7,10,3))
        self.assertIn('content_version=content_version+1',query.sql)
        self.assertIn("state='open' AND content_version=$4",query.sql)
    def test_transition_cas_and_no_generation_overwrite(self):
        self.db.count=0
        self.assertFalse(self.store.transition(7,'running','done',2))
        self.assertEqual(self.db.queries[-1].params,('done',7,10,'running',2,True))
        self.assertIn('state=$4 AND content_version=$5',self.db.queries[-1].sql)
        with self.assertRaises(WorkerUnavailable):self.store.transition(7,'done','open',3)
    def test_transcript_key_and_read_guard(self):
        self.store.read_transcript(7,limit=20)
        self.transcripts.read.assert_called_once_with('exp10-worker-i7',20)
        self.assertEqual(self.guards,[(1,10,False)])
    def test_append_exact_generation_running_and_locked(self):
        self.store.append_transcript(7,{'role':'assistant'},expected_version=3)
        self.transcripts.append.assert_called_once_with('exp10-worker-i7','{"role": "assistant"}',3)
        self.assertIn('FOR UPDATE',self.db.queries[-1].sql)
        self.assertEqual(self.guards,[(1,10,True)])
    def test_stale_or_paused_never_appends(self):
        for state,version in [('running',2),('paused',3),('deleted',3)]:
            self.db.rows=[row(state,version)]
            with self.assertRaises(WorkerConflict):self.store.append_transcript(7,{},expected_version=3)
        self.transcripts.append.assert_not_called()
    def test_missing_adapters_and_invalid_ids(self):
        store=WorkerStore(self.db,1,10)
        with self.assertRaises(WorkerUnavailable):store.get(7)
        with self.assertRaises(WorkerUnavailable):store.read_transcript(7)
        for worker_id in (True,0,-1,'../escape'):
            with self.assertRaises(ValueError):self.store.get(worker_id)
        self.assertEqual(self.db.queries,[])
    def test_backend_error_preserves_explicit_failure(self):
        self.transcripts.append.side_effect=OSError('backend unavailable')
        with self.assertRaises(OSError):self.store.append_transcript(7,{},expected_version=3)
        self.assertEqual(self.db.events,['begin','rollback'])
    def test_destructive_paths_not_faked(self):
        with self.assertRaises(WorkerUnavailable):self.store.delete(7)
        with self.assertRaises(WorkerUnavailable):self.store.archive(7)
        self.assertEqual(self.db.queries,[])

if __name__=='__main__':unittest.main()
