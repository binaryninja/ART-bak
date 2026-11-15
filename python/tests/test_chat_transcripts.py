"""Temporary fixture transcripts only; providers/tools are inert injected fakes."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch
from artex.transcripts import TranscriptStore,TranscriptConflict,TranscriptError,TranscriptUnavailable,messages
from artex.chat_transcripts import ChatTranscriptAdapter,META
from artex.agent.chat import ChatAgent,ChatBinding,ChatBusy,ChatPersistenceError
from artex.agent.runtime import RunResult,Grant,Tool
from artex.sidequestion import Model

USER={'role':'user','content':[{'type':'text','text':'hello'}]}
ANSWER={'role':'assistant','content':[{'type':'text','text':'done'}]}
MODEL=Model(model='fixture',identity='fixture-id')

class ChatTranscriptTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=TranscriptStore(self.root);self.adapter=ChatTranscriptAdapter(self.store)
    def result(self,turn,**kwargs):
        values=dict(reason='completed',messages=turn.messages+[deepcopy(ANSWER)],usage={'input_tokens':2,'output_tokens':1},turns=1,tool_calls=0,events=[])
        values.update(kwargs);return RunResult(**values)

    def test_admission_and_complete_norma_records_integer_metadata(self):
        turn=self.adapter.begin('s','client',USER)
        self.assertGreater(turn.run_id,0);self.assertIs(type(turn.revision),int)
        self.assertTrue(self.adapter.current('s',turn));self.assertFalse(turn.finalized)
        record=self.store.read('s')[0]
        self.assertEqual(record['type'],'message');self.assertEqual(record['message'],USER)
        self.assertEqual(record[META]['version'],1)
        result=self.result(turn);self.assertTrue(self.adapter.commit('s',turn,result))
        rows=self.store.read('s');self.assertEqual(len(rows),2);self.assertEqual(rows[0]['message'],USER)
        self.assertEqual(rows[0][META]['version'],2);self.assertEqual(rows[1]['parent_uuid'],rows[0]['uuid'])
        history,usage=messages(rows);self.assertEqual(history,result.messages);self.assertEqual(usage['input_tokens'],2)
        self.assertFalse(self.adapter.current('s',turn))

    def test_duplicate_pending_blocks_and_finalized_replay_does_not_execute(self):
        turn=self.adapter.begin('s','client',USER)
        for client in ('client','other'):
            with self.assertRaises(ChatBusy):ChatTranscriptAdapter(TranscriptStore(self.root)).begin('s',client,USER)
        with self.assertRaises(TranscriptConflict):self.adapter.begin('s','client',{'role':'user','content':[]})
        result=self.result(turn);self.adapter.commit('s',turn,result)
        duplicate=ChatTranscriptAdapter(TranscriptStore(self.root)).begin('s','client',USER)
        self.assertFalse(duplicate.created);self.assertTrue(duplicate.finalized)
        self.assertEqual(duplicate.revision,turn.revision);self.assertEqual(duplicate.run_id,turn.run_id)
        self.assertTrue(self.adapter.commit('s',turn,result))
        self.assertEqual(len(self.store.read('s')),2)
        with self.assertRaises(TranscriptConflict):self.adapter.commit('s',turn,self.result(turn,reason='different'))

    def test_public_finalized_result_accessor_verified_and_independent(self):
        turn=self.adapter.begin('s','client',USER)
        self.assertIsNone(self.adapter.finalized_result('s',turn))
        result=self.result(turn);self.adapter.commit('s',turn,result)
        duplicate=self.adapter.begin('s','client',USER)
        loaded=self.adapter.finalized_result('s',duplicate)
        self.assertEqual(loaded,result)
        loaded.messages[-1]['content'][0]['text']='changed in caller'
        self.assertEqual(self.adapter.finalized_result('s',duplicate),result)
        with self.assertRaises(TranscriptConflict):self.adapter.finalized_result('s',replace(turn,run_id=turn.run_id+1))
        records=[json.loads(line) for line in (self.root/'s.jsonl').read_text().splitlines()]
        records[0][META]['result_digest']='tampered'
        (self.root/'s.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in records))
        with self.assertRaises(TranscriptError):self.adapter.finalized_result('s',duplicate)

    def test_zero_output_terminal_no_new_record_or_boundary(self):
        turn=self.adapter.begin('s','client',USER)
        result=self.result(turn,messages=turn.messages,reason='permission_denied',turns=0,usage={})
        self.adapter.commit('s',turn,result)
        rows=self.store.read('s');self.assertEqual(len(rows),1);self.assertEqual(rows[0]['type'],'message')
        duplicate=self.adapter.begin('s','client',USER)
        self.assertTrue(duplicate.finalized);self.assertEqual(duplicate.revision,turn.revision)

    def test_next_turn_preserves_unknown_fields_and_prior_terminal_integrity(self):
        self.store.record_message('s',{'role':'assistant','content':[{'type':'text','text':'legacy'}],'extra':'retained'})
        turn=self.adapter.begin('s','first',USER);self.adapter.commit('s',turn,self.result(turn))
        later=self.adapter.begin('s','second',USER)
        self.assertEqual(len(later.messages),4);self.assertEqual(later.messages[0]['extra'],'retained')
        self.assertGreater(later.run_id,turn.run_id)
        self.adapter.commit('s',later,self.result(later))
        self.assertTrue(self.adapter.begin('s','first',USER).finalized)

    def test_external_append_and_clear_fence_late_commit(self):
        turn=self.adapter.begin('s','client',USER)
        self.store.record_message('s',ANSWER)
        self.assertFalse(self.adapter.current('s',turn));before=(self.root/'s.jsonl').read_bytes()
        self.assertFalse(self.adapter.commit('s',turn,self.result(turn)))
        self.assertEqual((self.root/'s.jsonl').read_bytes(),before)
        # Explicitly delete only this temporary test fixture to simulate external clear.
        (self.root/'s.jsonl').unlink()
        self.assertFalse(self.adapter.current('s',turn));self.assertFalse(self.adapter.commit('s',turn,self.result(turn)))
        self.assertFalse((self.root/'s.jsonl').exists())

    def test_atomic_tool_pairs_and_invalid_partial_never_written(self):
        turn=self.adapter.begin('s','client',USER)
        call={'role':'assistant','content':[{'type':'tool_use','id':'one','name':'fixture','input':{}}]}
        output={'role':'user','content':[{'type':'tool_result','tool_use_id':'one','content':[{'type':'text','text':'inert'}]}]}
        before=(self.root/'s.jsonl').read_bytes()
        with self.assertRaises(TranscriptError):self.adapter.commit('s',turn,self.result(turn,messages=turn.messages+[call]))
        self.assertEqual((self.root/'s.jsonl').read_bytes(),before)
        self.assertTrue(self.adapter.commit('s',turn,self.result(turn,messages=turn.messages+[call,output,ANSWER],tool_calls=1)))
        self.assertEqual(len(messages(self.store.read('s'))[0]),4)

    def test_untrusted_metadata_identity_and_digest_rejected(self):
        turn=self.adapter.begin('s','client',USER);original=(self.root/'s.jsonl').read_text()
        cases=[lambda r:r.__setitem__('session_id','other'),lambda r:r[META].__setitem__('run_id',True),
               lambda r:r[META].__setitem__('schema',99),lambda r:r[META].__setitem__('prefix_digest','0'*64),
               lambda r:r[META].__setitem__('user_digest','0'*64),lambda r:r.__setitem__('agent_id','foreign')]
        for change in cases:
            row=json.loads(original);change(row);(self.root/'s.jsonl').write_text(json.dumps(row)+'\n')
            with self.assertRaises(TranscriptError):self.adapter.begin('s','next',USER)
        (self.root/'s.jsonl').write_text(original)
        self.assertTrue(self.adapter.current('s',turn))

    def test_finalized_metadata_and_message_tampering_not_replayed(self):
        turn=self.adapter.begin('s','client',USER);self.adapter.commit('s',turn,self.result(turn))
        path=self.root/'s.jsonl';original=path.read_text()
        for change in (lambda rows:rows[0][META].__setitem__('result_digest','bad'),
                       lambda rows:rows[1]['message']['content'][0].__setitem__('text','changed'),
                       lambda rows:rows[0][META].__setitem__('unexpected_authority',True)):
            rows=[json.loads(line) for line in original.splitlines()];change(rows)
            path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
            with self.assertRaises(TranscriptError):self.adapter.begin('s','client',USER)
        path.write_text(original)
        with self.assertRaises(TranscriptConflict):self.adapter.commit('s',replace(turn,run_id=turn.run_id+1),self.result(turn))

    def test_two_instances_cannot_admit_two_running_turns(self):
        def admit(i):
            try:return ChatTranscriptAdapter(TranscriptStore(self.root)).begin('s',str(i),USER)
            except ChatBusy:return None
        with ThreadPoolExecutor(max_workers=4) as pool:turns=list(pool.map(admit,range(4)))
        self.assertEqual(sum(t is not None for t in turns),1);self.assertEqual(len(self.store.read('s')),1)

    def test_post_rename_failure_reconciles_without_duplicate_append(self):
        turn=self.adapter.begin('s','client',USER);result=self.result(turn)
        original=self.store._write
        def uncertain(*args):original(*args);raise OSError('inert directory fsync failure')
        with patch.object(self.store,'_write',side_effect=uncertain):
            with self.assertRaises(OSError):self.adapter.commit('s',turn,result)
        self.assertTrue(self.adapter.commit('s',turn,result));self.assertEqual(len(self.store.read('s')),2)

    def test_unresolved_admission_failure_and_guarded_recovery(self):
        original=self.store._write
        def uncertain(*args):original(*args);raise OSError('inert write uncertainty')
        with patch.object(self.store,'_write',side_effect=uncertain):
            with self.assertRaises(OSError):self.adapter.begin('s','client',USER)
        with self.assertRaises(ChatBusy):self.adapter.begin('s','client',USER)
        pending=self.adapter.pending('s');self.assertFalse(pending.finalized)
        with self.assertRaises(TranscriptUnavailable):self.adapter.recover_interrupted('s',pending)
        entered=[]
        @contextmanager
        def guard(session,turn):
            entered.append((session,turn.id));yield
        recovered=ChatTranscriptAdapter(self.store,recovery_guard=guard)
        self.assertTrue(recovered.recover_interrupted('s',pending));self.assertEqual(entered,[('s',pending.id)])
        self.assertIsNone(recovered.pending('s'));self.assertTrue(recovered.begin('s','client',USER).finalized)
        self.assertEqual(self.store.read('s')[0][META]['result']['reason'],'interrupted_unknown_effects')

    def test_sidechain_isolation_and_invalid_paths(self):
        side=ChatTranscriptAdapter(self.store,agent_id='worker')
        turn=side.begin('s','client',USER);side.commit('s',turn,self.result(turn))
        self.assertEqual(self.store.read('s'),[])
        self.assertEqual(len(self.store.read('s',agent_id='worker')),2)
        for session in ('../escape','a:b','a.b'):
            with self.assertRaises(ValueError):self.adapter.begin(session,'client',USER)

    def test_real_chat_agent_bounded_loop_and_idempotent_replay(self):
        callbacks=[]
        tool=Tool('fixture','inert',{},lambda ctx,args:callbacks.append(args) or 'inert-result',lambda args:args=={})
        class Provider:
            def __init__(self):self.calls=0
            def complete(self,context,request):
                self.calls+=1
                if self.calls==1:return {'role':'assistant','content':[{'type':'tool_use','id':'one','name':'fixture','input':{}}]},'tool_use',{'input_tokens':2,'output_tokens':1}
                return deepcopy(ANSWER),'end_turn',{'input_tokens':3,'output_tokens':1}
        provider=Provider();agent=ChatAgent(self.adapter,lambda m:ChatBinding(m,provider),gate=lambda *args:Grant(True,True,True),tools=[tool])
        outcome=agent.run('s','client','hello',model=MODEL)
        self.assertTrue(outcome.persisted);self.assertEqual(callbacks,[{}]);self.assertEqual(provider.calls,2)
        self.assertEqual(len(messages(self.store.read('s'))[0]),4)
        replay=agent.run('s','client','hello',model=MODEL)
        self.assertTrue(replay.persisted);self.assertTrue(replay.reused);self.assertEqual(provider.calls,2)

    def test_chat_denied_gate_records_durable_zero_output(self):
        class Provider:
            def complete(self,*args):raise AssertionError('must not call')
        agent=ChatAgent(self.adapter,lambda m:ChatBinding(m,Provider()),gate=lambda *args:Grant())
        result=agent.run('s','client','hello',model=MODEL)
        self.assertTrue(result.persisted);self.assertEqual(result.result.reason,'permission_denied')
        self.assertTrue(self.adapter.begin('s','client',USER).finalized)

if __name__=='__main__':unittest.main()

class ScopedTranscriptTests(ChatTranscriptTests):
    def test_scope_is_durable_and_mismatch_rejected_after_reload(self):
        scope={'task_id':1,'revision':'a','model':{'identity':'fixture'}}
        turn=self.adapter.begin_scoped('s','client',USER,scope)
        self.assertTrue(self.adapter.commit('s',turn,self.result(turn)))
        reloaded=ChatTranscriptAdapter(self.store)
        duplicate=reloaded.begin_scoped('s','client',USER,scope)
        self.assertTrue(duplicate.finalized);self.assertFalse(duplicate.created)
        self.assertEqual(reloaded.finalized_result('s',duplicate).text,'done')
        with self.assertRaises(TranscriptConflict):reloaded.begin_scoped('s','client',USER,dict(scope,revision='b'))
        with self.assertRaises(TranscriptConflict):reloaded.begin('s','client',USER)
        self.assertEqual(len(self.store.read('s')),2)
    def test_unscoped_cannot_be_promoted_and_bad_identity_not_saved(self):
        turn=self.adapter.begin('s','client',USER);self.adapter.commit('s',turn,self.result(turn))
        with self.assertRaises(TranscriptConflict):self.adapter.begin_scoped('s','client',USER,{'scope':'x'})
        for scope in ({},{'x':float('nan')},{1:'x'},{'x':object()}):
            with self.assertRaises(TranscriptError):self.adapter.begin_scoped('other','client',USER,scope)
        self.assertEqual(self.store.read('other'),[])

    def test_scoped_preflight_is_nonmutating_and_rejects_changed_identity(self):
        scope={'scope':'a'}
        self.adapter.check_scoped('s','client',USER,scope)
        self.assertEqual(self.store.read('s'),[])
        turn=self.adapter.begin_scoped('s','client',USER,scope)
        self.adapter.commit('s',turn,self.result(turn))
        before=self.store.read('s')
        self.assertIsNone(self.adapter.check_scoped('s','client',USER,scope))
        with self.assertRaises(TranscriptConflict):self.adapter.check_scoped('s','client',USER,{'scope':'b'})
        self.assertEqual(self.store.read('s'),before)
