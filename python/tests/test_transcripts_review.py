"""Independent temp-only filesystem failure contracts; no user transcripts."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
from artex.transcripts import TranscriptStore, TranscriptError, WorkerTranscriptBackend


class TranscriptReviewTests(unittest.TestCase):
    def test_post_replace_fsync_uncertainty_uuid_retry_is_idempotent(self):
        with tempfile.TemporaryDirectory() as root:
            store = TranscriptStore(root)
            @contextmanager
            def guard(session, version):
                self.assertEqual((session, version), ('session', 4))
                yield
            backend = WorkerTranscriptBackend(store, generation_guard=guard)
            record = dict(uuid='stable', session_id='session', type='message',
                          message={'role': 'user', 'content': []})
            raw = json.dumps(record)
            original = os.fsync
            def fail_directory(fd):
                if stat.S_ISDIR(os.fstat(fd).st_mode):
                    raise OSError('inert post-rename durability failure')
                return original(fd)
            with patch('artex.transcripts.os.fsync', side_effect=fail_directory):
                with self.assertRaises(OSError):
                    backend.append('session', raw, 4)
            # Failure does not promise rollback: renamed bytes already exist.
            self.assertEqual(store.read('session'), [record])
            backend.append('session', raw, 4)
            self.assertEqual(store.read('session'), [record])
            self.assertEqual(list(Path(root).glob('.tmp-*')), [])

    def test_hardlinked_lock_rejected_without_transcript_mutation(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / 'source'
            source.write_bytes(b'untouched')
            os.link(source, Path(root) / '.lock-session')
            with self.assertRaises(TranscriptError):
                TranscriptStore(root).begin('session')
            self.assertEqual(source.read_bytes(), b'untouched')
            self.assertFalse((Path(root) / 'session.jsonl').exists())
