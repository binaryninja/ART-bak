"""Independent optional execution navigation disclosure; inert rows/JWT only."""
import unittest
from unittest.mock import patch
import test_metadata_execution_setup as fixture
from artex.server.intercept_execution_read import InvalidExecutionRequest

class ExecutionSetupReview(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_response_start_correlation_retarget_never_releases_original(self):
        f=self.f
        def change():f.n.detail=(*f.n.detail[:4],'6',*f.n.detail[5:])
        with self.assertRaises(RuntimeError):f.t.request(f.path,start=change)
    def test_response_start_raw_revocation_blocks_body(self):
        f=self.f
        with self.assertRaises(RuntimeError):f.t.request(f.path,start=lambda:setattr(f.n,'raw',False))
    def test_404_snapshot_cannot_become_an_existing_record(self):
        f=self.f;original=f.n.detail;f.n.detail=None
        self.assertEqual(f.t.request(f.path)[0],404)
        with self.assertRaises(RuntimeError):f.t.request(f.path,start=lambda:setattr(f.n,'detail',original))
    def test_409_snapshot_cannot_become_success(self):
        f=self.f;valid=list(f.n.activity);f.n.activity=valid[:1]*3
        self.assertEqual(f.t.request(f.path)[0],409)
        with self.assertRaises(RuntimeError):f.t.request(f.path,start=lambda:setattr(f.n,'activity',valid))
    def test_410_hint_deletion_snapshot_cannot_change(self):
        f=self.f;f.n.detail=None;f.n.hint_exists=False
        with self.assertRaises(RuntimeError):f.t.request(f.path+'?conversation=12',start=lambda:setattr(f.n,'hint_exists',True))
    def test_final_jwt_callback_retarget_is_refused_before_body(self):
        f=self.f;owner=f.t.f.original.router.authorize.__self__;old=owner.clock
        # Same original callback identity, mutable trusted clock behavior.
        # Rebuild wrapper with the observed clock before pinning it.
        def clock():
            if f.n.queries:f.n.task_live=False
            return old()
        owner.clock=clock
        from artex.server.intercept_read_setup import bind_metadata_reads
        f.t.bundle=bind_metadata_reads(f.t.f.original,execution_service=f.n.service,authorize_global=lambda *a:True)
        status,body=f.t.request(f.path)
        self.assertEqual(status,403);self.assertNotIn(b'synthetic',body)
    def test_prepared_snapshot_bytes_and_canonical_selection_remain_exact(self):
        f=self.f
        for selection in ((True,None),(7,True),(8,None)):
            with self.assertRaises(InvalidExecutionRequest):f.n.service.prepare(f.n.request,selection=selection)
        self.assertFalse(f.n.queries)
        prepared=f.n.service.prepare(f.n.request,selection=(7,None))
        object.__setattr__(prepared.response,'body',b'changed')
        with self.assertRaises(PermissionError):prepared.validate()
