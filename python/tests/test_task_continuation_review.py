"""Independent exact receipt and SQL contract negatives; no lifecycle clearance."""
import unittest
from copy import copy
from datetime import datetime,timezone
from types import SimpleNamespace
from artex.db.task_continuation import ContinuationPersistence,ContinuationConflict
import test_task_continuation as base

class Review(unittest.TestCase):
    def make(self):
        f=base.Tests();f.setUp();self.addCleanup(f.doCleanups);return f
    def test_copied_suspension_is_not_issued(self):
        f=self.make();cap=f.owner.reserve_pause(f.handle)
        with self.assertRaises(Exception):f.owner._record(copy(cap))
        self.assertTrue(f.owner.retained(f.handle))
    def test_original_receipt_field_change_denies_retained_authority(self):
        f=self.make();cap=f.owner.reserve_pause(f.handle);r=f.handle._receipt
        original=r.run_id
        try:
            object.__setattr__(r,'run_id',float(original))
            with self.assertRaises(Exception):f.owner._record(cap)
        finally:object.__setattr__(r,'run_id',original)
    def check_sql(self,stamp=None,identity=5):
        receipt=SimpleNamespace(task_id=5,exploration_id=9,run_id=1,request_id='r',config_digest='d',owner_token='t',revision='v')
        class Tx:
            def fetch_all(self,q):
                if q.sql.startswith('SELECT updated_at'):return [(stamp,)]
                if q.sql.startswith('SELECT task_id'):return [(identity,)]
                return []
        tx=Tx();return ContinuationPersistence(tx).check(tx,receipt,paused=True)
    def test_non_timestamp_sql_version_is_not_a_cas_receipt(self):
        for stamp in ('2026-01-01',True,1,datetime(2026,1,1)):
            with self.subTest(stamp=stamp),self.assertRaises(ContinuationConflict):self.check_sql(stamp)
    def test_bootstrap_sql_identity_requires_exact_integer(self):
        with self.assertRaises(ContinuationConflict):self.check_sql(datetime(2026,1,1,tzinfo=timezone.utc),5.0)
    def test_final_resume_read_revokes_grant_before_update(self):
        f=self.make();f.app.host.install_continuation(f.owner);f.app.host.pause('5',timeout=0)
        old=f.db.fetch_all;reads=[];writes=[]
        def fetch(q):
            result=old(q)
            if q.sql.startswith('SELECT id FROM exploration_nodes WHERE exploration_id='):
                reads.append(1)
                # commit_resume checks once, then resume rechecks immediately prewrite.
                if len(reads)==2:f.allowed=False
            if q.sql.startswith('UPDATE tasks SET paused=false'):writes.append(q)
            return result
        f.db.fetch_all=fetch
        with self.assertRaises(Exception):f.app.host.resume_suspended('5')
        self.assertEqual(writes,[],'Revoked continuation grant must prevent UPDATE')
    def test_changed_paused_timestamp_retains_old_owner_without_resume_write(self):
        from datetime import timedelta
        f=self.make();f.app.host.install_continuation(f.owner);f.app.host.pause('5',timeout=0)
        f.stamp+=timedelta(seconds=1);old=f.db.fetch_all;writes=[]
        def fetch(q):
            if q.sql.startswith('UPDATE tasks SET paused=false'):writes.append(q)
            return old(q)
        f.db.fetch_all=fetch
        with self.assertRaises(Exception):f.app.host.resume_suspended('5')
        self.assertEqual(writes,[]);self.assertTrue(f.owner.retained(f.handle))
        self.assertFalse(f.app.host.drain(0).all_settled)
    def test_runner_install_failure_after_commit_retains_both_without_replay(self):
        from unittest.mock import patch
        f=self.make();f.app.host.install_continuation(f.owner);f.app.host.pause('5',timeout=0)
        with patch.object(f.app.runner,'install_resumed_handle',side_effect=RuntimeError('inert installation failure')):
            with self.assertRaises(Exception):f.app.host.resume_suspended('5')
        slot=f.app.manager._suspensions['5']
        self.assertEqual(slot['phase'],'quarantined');self.assertIs(slot['old_handle'],f.handle)
        self.assertIsNotNone(slot['new_handle']);self.assertTrue(f.handle.token.cancelled)
        self.assertFalse(f.app.host.drain(0).all_settled)
        before=list(f.f.p.calls)
        with self.assertRaises(Exception):f.app.host.resume_suspended('5')
        self.assertEqual(f.f.p.calls,before)
    def test_resume_does_not_bulk_reopen_preexisting_paused_intent(self):
        f=self.make();f.f.f.f.seed()
        row=next(r for r in f.db.rows if r[0]==3);paused=list(row);paused[4]='paused'
        f.db.rows[f.db.rows.index(row)]=tuple(paused)
        f.app.host.install_continuation(f.owner);f.app.host.pause('5',timeout=0)
        f.app.host.resume_suspended('5');new=f.app.runner.handles()[0]
        f.app.host.advance_owned(lambda:f.app.runner.advance('5',new.token,1.))
        self.assertEqual(next(r for r in f.db.rows if r[0]==3)[4],'paused')
