"""Exact durable scoped receipt reads over temporary JSONL only."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from artex.transcripts import TranscriptStore,TranscriptConflict,TranscriptError
from artex.chat_transcripts import ChatTranscriptAdapter,META
from artex.agent.runtime import RunResult
USER={'role':'user','content':[{'type':'text','text':'fixture'}]}
ANSWER={'role':'assistant','content':[{'type':'text','text':'answer'}]}
SCOPE={'role':'planner','task_id':5,'exploration_id':9,'run_id':71,'revision':'v1'}
class ScopedReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=TranscriptStore(self.temp.name);self.adapter=ChatTranscriptAdapter(self.store)
    def finalized(self,client='client',scope=SCOPE):
        turn=self.adapter.begin_scoped('s',client,USER,scope)
        result=RunResult('completed',turn.messages+[deepcopy(ANSWER)],{'input_tokens':3,'output_tokens':2},1,0,[])
        self.adapter.commit('s',turn,result);return turn,result
    def test_exact_receipt_without_original_prefix_and_no_write(self):
        turn,result=self.finalized();before=self.store.read('s')
        with patch.object(self.store,'_write',side_effect=AssertionError('read must not write')):
            receipt=self.adapter.finalized_scoped_result('s',turn.id,SCOPE)
        self.assertEqual(receipt.result,result);self.assertEqual(receipt.run_id,turn.run_id)
        self.assertEqual(receipt.prefix_length,len(turn.messages));self.assertEqual(receipt.result.messages[receipt.prefix_length:],[ANSWER])
        self.assertEqual(self.store.read('s'),before)
        with self.assertRaises(FrozenInstanceError):receipt.prefix_length=0
    def test_old_turn_later_history_never_included(self):
        turn,result=self.finalized();self.finalized('later')
        receipt=self.adapter.finalized_scoped_result('s',turn.id,SCOPE)
        self.assertEqual(receipt.result,result);self.assertEqual(len(receipt.result.messages),2)
    def test_mutating_result_does_not_change_store(self):
        turn,result=self.finalized();receipt=self.adapter.finalized_scoped_result('s',turn.id,SCOPE)
        receipt.result.messages.clear();receipt.result.usage.clear()
        self.assertEqual(self.adapter.finalized_scoped_result('s',turn.id,SCOPE).result,result)
    def test_missing_pending_and_wrong_scope(self):
        turn=self.adapter.begin_scoped('s','c',USER,SCOPE)
        self.assertIsNone(self.adapter.finalized_scoped_result('s',turn.id,SCOPE))
        self.assertIsNone(self.adapter.finalized_scoped_result('s','0'*32,SCOPE))
        with self.assertRaises(TranscriptConflict):self.adapter.finalized_scoped_result('s',turn.id,dict(SCOPE,run_id=72))
    def test_unscoped_legacy_not_treated_as_scope(self):
        turn=self.adapter.begin('s','c',USER)
        with self.assertRaises(TranscriptConflict):self.adapter.finalized_scoped_result('s',turn.id,SCOPE)
    def test_corrupt_digest_rejected(self):
        turn,result=self.finalized()
        with self.store._locked('s','') as (directory,name):
            _,rows=self.store._load(directory,name);rows[0][META]['result_digest']='bad';self.store._write(directory,name,rows)
        with self.assertRaises(TranscriptError):self.adapter.finalized_scoped_result('s',turn.id,SCOPE)
    def test_bad_turn_id_rejected_before_read(self):
        with patch.object(self.store,'begin',side_effect=AssertionError('must reject first')):
            for id in ('../x','',1,'a'*33):
                with self.assertRaises(TranscriptError):self.adapter.finalized_scoped_result('s',id,SCOPE)
