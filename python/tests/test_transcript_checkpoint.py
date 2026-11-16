import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from artex.transcripts import TranscriptStore, TranscriptError
from artex.transcript_checkpoint import inspect_checkpoint


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.store=TranscriptStore(self.root,max_bytes=4096)
    def seed(self):
        self.store.record_message('s',{'role':'user','content':[]})
        self.store.record_boundary('s',{'trigger':'auto'})
    def test_roundtrip_and_sidechain(self):
        self.seed();raw=(self.root/'s.jsonl').read_bytes()
        value=inspect_checkpoint(self.store,'s')
        self.assertEqual((value.sha256,value.byte_count,value.record_count),(hashlib.sha256(raw).hexdigest(),len(raw),2))
        self.assertFalse(value.recovery_authorized)
        self.store.record_message('s',{'role':'assistant'},agent_id='worker')
        self.assertEqual(inspect_checkpoint(self.store,'s',agent_id='worker').record_count,1)
        self.assertEqual((self.root/'s.jsonl').read_bytes(),raw)
    def test_missing_and_empty_no_creation(self):
        with self.assertRaises(TranscriptError):inspect_checkpoint(self.store,'s')
        self.assertEqual(list(self.root.iterdir()),[])
        self.store.begin('s')
        with self.assertRaises(TranscriptError):inspect_checkpoint(self.store,'s',agent_id='missing')
        self.assertFalse((self.root/'s').exists())
        (self.root/'s.jsonl').write_bytes(b'')
        self.assertEqual(inspect_checkpoint(self.store,'s').record_count,0)
    def test_malformed(self):
        self.seed();path=self.root/'s.jsonl';original=path.read_bytes();rows=[json.loads(x) for x in original.splitlines()]
        variants=[b'secret',b'{}\n',original.rstrip(b'\n'),b'\n',b'{"uuid":"a","uuid":"b"}\n']
        for index,key,value in [(0,'session_id','other'),(0,'agent_id','x'),(0,'is_sidechain',True),(1,'uuid',rows[0]['uuid']),(1,'parent_uuid','wrong'),(0,'type','unknown'),(0,'message',[]),(0,'uuid','')]:
            copy=json.loads(json.dumps(rows));copy[index][key]=value
            variants.append(b''.join(json.dumps(x).encode()+b'\n' for x in copy))
        for raw in variants:
            with self.subTest(raw=raw):
                path.write_bytes(raw)
                with self.assertRaisesRegex(TranscriptError,'^Transcript checkpoint unavailable$'):inspect_checkpoint(self.store,'s')
                self.assertEqual(path.read_bytes(),raw)
    def test_non_utf8_rejected(self):
        self.seed();path=self.root/'s.jsonl'
        record=json.loads(path.read_bytes().splitlines()[0])
        text=json.dumps(record)
        for encoding in ('utf-16-le','utf-16-be','utf-32-le','utf-32-be'):
            raw=text.encode(encoding)+b'\n'
            path.write_bytes(raw)
            with self.subTest(encoding=encoding):
                with self.assertRaises(TranscriptError):inspect_checkpoint(self.store,'s')
                self.assertEqual(path.read_bytes(),raw)
    def test_limits_and_unsafe(self):
        self.seed();path=self.root/'s.jsonl'
        with self.assertRaises(TranscriptError):inspect_checkpoint(self.store,'s',max_records=1)
        self.store.max_bytes=1
        with self.assertRaises(TranscriptError):inspect_checkpoint(self.store,'s')
        self.store.max_bytes=4096
        path.rename(self.root/'target');path.symlink_to(self.root/'target')
        with self.assertRaises(TranscriptError):inspect_checkpoint(self.store,'s')
        path.unlink();os.link(self.root/'target',path)
        with self.assertRaises(TranscriptError):inspect_checkpoint(self.store,'s')
    def test_inplace_mutation_detected(self):
        self.seed();real=os.read;changed=False
        def read(fd,n):
            nonlocal changed
            raw=real(fd,n)
            if not changed:
                changed=True
                with (self.root/'s.jsonl').open('ab') as file:file.write(b'\n')
            return raw
        with patch('artex.transcript_checkpoint.os.read',read):
            with self.assertRaises(TranscriptError):inspect_checkpoint(self.store,'s')
    def test_writer_waits_for_observation_lock(self):
        self.seed();entered=threading.Event();release=threading.Event();written=threading.Event();real=os.read;results=[]
        def read(fd,n):
            entered.set();self.assertTrue(release.wait(3));return real(fd,n)
        def inspect():results.append(inspect_checkpoint(self.store,'s'))
        def write():
            self.store.record_message('s',{'role':'assistant'});written.set()
        with patch('artex.transcript_checkpoint.os.read',read):
            reader=threading.Thread(target=inspect);reader.start();self.assertTrue(entered.wait(3))
            writer=threading.Thread(target=write);writer.start()
            try:self.assertFalse(written.wait(.05))
            finally:release.set();reader.join(3);writer.join(3)
        self.assertFalse(reader.is_alive());self.assertFalse(writer.is_alive());self.assertTrue(written.is_set())
        self.assertEqual(results[0].record_count,2)
        self.assertEqual(inspect_checkpoint(self.store,'s').record_count,3)
