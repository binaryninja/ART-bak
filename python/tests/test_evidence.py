import hashlib
import io
import os
from dataclasses import replace
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest

from artex.evidence import Store, Snapshot, TrafficRef, hash_path, snapshot_id, normalize_traffic_refs


class Repository:
    def __init__(self):
        self.locked = False
        self.refs = set()
    def with_evidence_tx(self, fn):
        assert not self.locked
        self.locked = True
        try:
            return fn(self)
        finally:
            self.locked = False
    def collect_evidence_references(self, now, cutoff):
        self.times = now, cutoff
        return self.refs
    def finding_traffic(self, finding_id):
        return SimpleNamespace(bindings=[SimpleNamespace(id=7)])


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Repository()
        self.store = Store(self.db, directory=Path(self.temp.name) / 'evidence')

    def fixture(self):
        request, response = b'{}', bytes(range(256)) * 8192
        rh = self.store.write_body(io.BytesIO(request), len(request))
        ph = self.store.write_body(io.BytesIO(response), len(response))
        s = Snapshot(req_hash=rh, resp_hash=ph, req_len=len(request), resp_len=len(response))
        return replace(s, id=snapshot_id(s)), response

    def test_hash_rejects_traversal_uppercase_and_invalid(self):
        for value in ('../x', 'A' * 64, 'g' * 64, 'a' * 63, 'a' * 64 + '\n'):
            with self.assertRaises(ValueError):
                hash_path('.', value)

    def test_write_read_binary_and_private_permissions(self):
        s, response = self.fixture()
        stream, length = self.store.open_body(s, 'response')
        with stream:
            self.assertEqual(stream.read(), response)
        self.assertEqual(length, len(response))
        self.assertEqual(hash_path(self.store.directory, s.req_hash).stat().st_mode & 0o777, 0o600)
        self.assertEqual(list((self.store.directory / '.staging').iterdir()), [])

    def test_failed_write_does_not_publish(self):
        for length, digest in ((4, ''), (3, '0' * 64)):
            with self.assertRaises(ValueError):
                self.store.write_body(io.BytesIO(b'abc'), length, digest)
        self.assertFalse((self.store.directory / 'blobs').exists())
        self.assertEqual(list((self.store.directory / '.staging').iterdir()), [])

    def test_existing_corruption_not_silently_overwritten(self):
        digest = self.store.write_body(io.BytesIO(b'abc'), 3)
        hash_path(self.store.directory, digest).write_bytes(b'xyz')
        with self.assertRaises(ValueError):
            self.store.write_body(io.BytesIO(b'abc'), 3)
        with self.assertRaises(ValueError):
            self.store.open_body(Snapshot(req_hash=digest, req_len=3), 'request')

    def test_copy_real_files_dedup_and_reject_corrupted_destination(self):
        s, _ = self.fixture()
        dest = Path(self.temp.name) / 'export'
        self.store.copy_snapshots([s, s], dest)
        target = hash_path(dest, s.req_hash)
        self.assertNotEqual(target.stat().st_ino, hash_path(self.store.directory, s.req_hash).stat().st_ino)
        target.write_bytes(b'xx')
        with self.assertRaises(ValueError):
            self.store.copy_snapshots([s], dest)

    def test_install_validates_metadata_and_holds_lock_through_restore(self):
        s, _ = self.fixture()
        target = Store(self.db, directory=Path(self.temp.name) / 'restore')
        self.assertEqual(target.with_installed_snapshots([s], self.store.directory,
                                                       lambda: self.db.locked), True)
        with self.assertRaises(ValueError):
            target.install_snapshots([replace(s, url='altered')], self.store.directory)
        self.assertFalse(self.db.locked)

    def test_gc_keeps_referenced_and_young_blobs(self):
        s, _ = self.fixture()
        now = time.time()
        old = hash_path(self.store.directory, s.req_hash)
        os.utime(old, (now - 90000, now - 90000))
        self.db.refs = {s.req_hash}
        self.store.collect(now)
        self.assertTrue(old.exists())
        self.db.refs = set()
        self.store.collect(now)
        self.assertFalse(old.exists())
        self.assertTrue(hash_path(self.store.directory, s.resp_hash).exists())
        self.assertEqual(self.db.times, (now, now - 86400))

    def test_binding_lock_release_and_not_found(self):
        self.store.with_binding(1, 7, lambda b: self.assertTrue(self.db.locked))
        self.assertEqual(self.store.binding(1, 7).id, 7)
        self.assertFalse(self.db.locked)
        with self.assertRaises(LookupError):
            self.store.binding(1, 8)

    def test_metadata_requires_adapter(self):
        with self.assertRaises(RuntimeError):
            Store().copy_snapshots([], '.')

    def test_ref_normalization_dedupe_and_duplicate_validation(self):
        refs = normalize_traffic_refs([TrafficRef(' x '), TrafficRef('x', 'proof')])
        self.assertEqual(refs, [TrafficRef('x', 'supporting')])
        for refs in ([TrafficRef(' ')], [TrafficRef('x'), TrafficRef('x', 'invalid')]):
            with self.assertRaises(ValueError):
                normalize_traffic_refs(refs)

    def test_snapshot_hash_ignores_generated_fields_and_normalizes_text(self):
        s = Snapshot(url=b'http://test/\xff\xfe\0<>&', req_head='x\0')
        cleaned = s.normalize()
        self.assertEqual(cleaned.url, 'http://test/\ufffd<>&')
        self.assertEqual(snapshot_id(s), snapshot_id(replace(cleaned, id='anything', created_at='later')))
        self.assertNotEqual(snapshot_id(s), snapshot_id(replace(s, req_len=1)))
        raw = b'{"id":"","source_traffic_id":"","captured_at":0,"url":"","method":"","status":0,"content_type":"","req_hash":"","resp_hash":"","req_len":0,"resp_len":0,"created_at":"0001-01-01T00:00:00Z"}'
        self.assertEqual(snapshot_id(Snapshot()), hashlib.sha256(raw).hexdigest())

    def test_prepare_reads_and_preserves_bodies(self):
        s, response = self.fixture()
        exchange = SimpleNamespace(id='x', ts=123, url='http://test', method='GET', status=200,
                                   content_type='binary', req_head='', resp_head='', req_len=2,
                                   resp_len=len(response), req_hash=s.req_hash, resp_hash=s.resp_hash,
                                   request=io.BytesIO(b'{}'), response=io.BytesIO(response))
        self.store.traffic = SimpleNamespace(read_evidence=lambda ids, callback: callback(exchange))
        prepared = self.store.prepare([TrafficRef('x', 'proof')])
        self.assertEqual(prepared[0].snapshot.resp_hash, s.resp_hash)
        self.assertEqual(prepared[0].snapshot.id, snapshot_id(prepared[0].snapshot))
        self.assertEqual(prepared[0].ref.role, 'proof')


if __name__ == '__main__':
    unittest.main()
