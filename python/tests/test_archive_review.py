"""Independent offline archive review, no live database or extraction."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import nullcontext
from artex.archive_bundle import build_bundle, publish_bundle, BundleInvalid
from artex.archive_snapshot import ArchiveSnapshotExporter, SnapshotUnavailable
from artex.db.postgres import NoRowsError
import test_archive_bundle as bundle_fixture
import test_archive_snapshot as snapshot_fixture

class ArchiveReview(unittest.TestCase):
    def manifest(self):return bundle_fixture.BundleTests().manifest()
    def test_malformed_stream_counts_rejected_as_invalid_bundle(self):
        manifest=self.manifest()
        manifest.update(streamed_tables={'llm_records':'database/llm_records.ndjson'},data_counts=[])
        with self.assertRaises(BundleInvalid):
            build_bundle(manifest,{'database/llm_records.ndjson':b'{}\n'})
    def test_missing_live_task_real_driver_exception(self):
        db=snapshot_fixture.Executor()
        def missing(query):
            if 'pg_advisory_xact_lock' in query.sql:return (None,)
            raise NoRowsError('query returned no rows')
        db.fetch_one=missing
        exporter=ArchiveSnapshotExporter(db,quiescence_guard=lambda _:nullcontext(),metadata=lambda *args:{})
        with self.assertRaises(SnapshotUnavailable):exporter.export(1)
        self.assertEqual(db.index,0)
        self.assertEqual(db.events,['begin','rollback'])
    def test_post_link_sync_failure_is_uncertain_not_overwritten(self):
        import os
        data=build_bundle(self.manifest(),{})
        original=os.fsync;calls=[]
        def fail_directory(fd):
            calls.append(fd)
            if len(calls)==2:raise OSError('directory sync fixture')
            return original(fd)
        with tempfile.TemporaryDirectory() as root:
            with patch('artex.archive_bundle.os.fsync',fail_directory):
                with self.assertRaises(OSError):publish_bundle(root,'task-1-7.tar.zst',data)
            self.assertEqual((Path(root)/'task-1-7.tar.zst').read_bytes(),data)
            self.assertEqual(list(Path(root).glob('.archive-*')),[])
            with self.assertRaises(FileExistsError):publish_bundle(root,'task-1-7.tar.zst',data)
