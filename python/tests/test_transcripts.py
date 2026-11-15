import json
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from artex.transcripts import TranscriptStore,TranscriptConflict,TranscriptError,TranscriptUnavailable,WorkerTranscriptBackend,messages

class TranscriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.store=TranscriptStore(self.root)
    def tearDown(self):self.tmp.cleanup()
    def record(self,id='a',**kw):return dict(uuid=id,session_id='s',timestamp='2026-01-01T00:00:00Z',type='message',message={'role':'user','content':[{'type':'text','text':'hello'}]},**kw)
    def test_main_roundtrip_unknown_and_usage(self):
        self.store.commit(self.store.begin('s'),[self.record(future={'deep':[1]},usage={'input_tokens':3})])
        self.store.record_boundary('s',{'trigger':'auto'})
        snap=self.store.record_message('s',{'role':'assistant','content':[]},{'output_tokens':2})
        self.assertEqual(snap.records[1]['parent_uuid'],'a')
        self.assertEqual(snap.records[0]['future'],{'deep':[1]})
        msg,usage=messages(snap.records);self.assertEqual(len(msg),2);self.assertEqual(usage['input_tokens'],3);self.assertEqual(usage['output_tokens'],2)
        self.assertEqual((self.root/'s.jsonl').stat().st_mode&0o777,0o600)
    def test_sidechain(self):
        self.store.record_message('s',{'role':'user'},agent_id='worker')
        self.assertTrue((self.root/'s/subagents/agent-worker.jsonl').exists())
        self.assertEqual(self.store.read('s'),[])
        self.assertTrue(self.store.read('s',agent_id='worker')[0]['is_sidechain'])
    def test_stale_revision(self):
        a=self.store.begin('s');b=self.store.begin('s');self.store.commit(a,[self.record()])
        with self.assertRaises(TranscriptConflict):self.store.commit(b,[self.record('b')])
        self.assertEqual(len(self.store.read('s')),1)
    def test_two_instances_concurrent_cas(self):
        snapshot=self.store.begin('s')
        def attempt(i):
            try:TranscriptStore(self.root).commit(snapshot,[self.record(str(i))]);return True
            except TranscriptConflict:return False
        with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(attempt,range(8)))
        self.assertEqual(sum(results),1)
    def test_identity_and_duplicate(self):
        with self.assertRaises(ValueError):self.store.commit(self.store.begin('other'),[self.record()])
        with self.assertRaises(TranscriptConflict):self.store.commit(self.store.begin('s'),[self.record(),self.record()])
        self.assertFalse((self.root/'s.jsonl').exists())
    def test_traversal(self):
        for value in ('../x','a/b','.', '', '/tmp/a','a\\b','a\x00b'):
            with self.assertRaises(ValueError):self.store.begin(value)
    def test_symlink_file_and_root(self):
        target=self.root/'target';target.write_text('private')
        (self.root/'s.jsonl').symlink_to(target)
        with self.assertRaises(OSError):self.store.begin('s')
        link=self.root/'link';link.symlink_to(self.root,target_is_directory=True)
        with self.assertRaises(OSError):TranscriptStore(link).begin('s')
        self.assertEqual(target.read_text(),'private')
    def test_symlink_sidechain_lock_and_hardlink(self):
        (self.root/'s').symlink_to(self.root,target_is_directory=True)
        with self.assertRaises(OSError):self.store.begin('s','a')
        (self.root/'.lock-other').symlink_to(self.root/'bad')
        with self.assertRaises(OSError):self.store.begin('other')
        (self.root/'target').write_text('{}\n');os.link(self.root/'target',self.root/'third.jsonl')
        with self.assertRaises(TranscriptError):self.store.begin('third')
    def test_corruption_redacted(self):
        for raw in ('secret-not-json','{"uuid":"a","uuid":"b"}', 'NaN', '[]'):
            (self.root/'s.jsonl').write_text(raw)
            with self.assertRaises(TranscriptError) as caught:self.store.begin('s')
            self.assertNotIn('secret',str(caught.exception))
    def test_bound_failure_preserves_old(self):
        store=TranscriptStore(self.root,max_bytes=500)
        store.commit(store.begin('s'),[self.record()]);old=(self.root/'s.jsonl').read_bytes()
        with self.assertRaises(TranscriptError):store.commit(store.begin('s'),[self.record('b',extra='z'*500)])
        self.assertEqual((self.root/'s.jsonl').read_bytes(),old)
        self.assertEqual(list(self.root.glob('.tmp-*')),[])
    def test_generation_and_idempotency_adapter(self):
        calls=[]
        @contextmanager
        def guard(session,version):
            calls.append((session,version))
            if version!=4:raise TranscriptConflict('stale')
            yield
        backend=WorkerTranscriptBackend(self.store,generation_guard=guard)
        raw=json.dumps(self.record());backend.append('s',raw,4);backend.append('s',raw,4)
        self.assertEqual(len(backend.read('s')),1)
        with self.assertRaises(TranscriptConflict):backend.append('s',raw,3)
        altered=self.record();altered['message']={}
        with self.assertRaises(TranscriptConflict):backend.append('s',json.dumps(altered),4)
        with self.assertRaises(TranscriptUnavailable):WorkerTranscriptBackend(self.store).append('s',raw,4)
    def test_unsupported_and_limits(self):
        with self.assertRaises(ValueError):self.store.read('s',0)
        with self.assertRaises(TranscriptUnavailable):self.store.delete('s')
        with self.assertRaises(TranscriptUnavailable):self.store.export('s')
        with self.assertRaises(ValueError):TranscriptStore('relative')
