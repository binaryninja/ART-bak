import unittest
from contextlib import contextmanager
from artex.llmrec.postgres import *
from artex.llmrec import Usage

class DB:
    def __init__(self): self.rows=[]; self.events=[]; self.fail=False
    @contextmanager
    def transaction(self):
        self.events.append('begin'); start=len(self.rows)
        try:
            yield self
            self.events.append('commit')
        except BaseException:
            del self.rows[start:]; self.events.append('rollback'); raise
    def execute(self,q):
        if self.fail and 'INSERT INTO llm_records' in q.sql: raise RuntimeError('private DB detail')
        self.rows.append(q); return 1

class Provider:
    def __init__(self): self.calls=0; self.closed=False; self.fail=False; self.cancel=False
    def complete(self,ctx,req):
        self.calls+=1
        assert 'capture' not in ctx
        if self.fail: raise RuntimeError('private provider detail')
        if self.cancel: ctx['cancelled']=True
        return {'content':[{'type':'text','text':'fixture'}]},'end',Usage(2,3)
    def stream(self,ctx,req):
        self.calls+=1
        try:
            yield {'type':'message_start','usage':{'input_tokens':2}}
            yield {'type':'text_delta','text':'fixture'}
            if self.fail: raise RuntimeError('private provider detail')
            yield {'type':'message_delta','usage':{'output_tokens':3}}
            yield {'type':'message_stop'}
        finally: self.closed=True

class Tests(unittest.TestCase):
    def setUp(self):
        self.db=DB(); self.provider=Provider()
        self.scope=RecordingScope('task','conv-1','worker','model','profile',9)
        self.kwargs=dict(scope=self.scope,enabled=True,binding_validator=lambda scope,inner:True,redact=lambda kind,text,scope:'[redacted '+kind+']')
        self.store=LLMRecordStore(self.db,task_id='task')
        self.rec=PostgresRecorder(self.provider,self.store,**self.kwargs)
    def status(self): return self.db.rows[-1].params[10]
    def test_actual_store_atomic_exact_scope_and_no_headers(self):
        self.rec.complete({'capture':object(),'Authorization':'do not store'}, {'messages':[],'headers':{'secret':'never'}})
        self.assertEqual(self.db.events,['begin','commit']); self.assertEqual(len(self.db.rows),2)
        self.assertEqual(self.db.rows[0].params[:5],('task',9,'worker','model','profile'))
        p=self.db.rows[-1].params
        self.assertEqual(p[:5],('model','profile','conv-1','task','worker'))
        self.assertEqual(p[6:10],(2,3,0,0)); self.assertEqual(p[-4:],('[redacted request]','[redacted response]',None,None))
        self.assertNotIn('secret',repr(self.db.rows)); self.assertEqual(self.status(),'ok')
    def test_scope_and_enablement_preflight_no_provider(self):
        with self.assertRaises(ValueError): self.rec.complete({'task_id':'other'}, {})
        with self.assertRaises(ValueError): PostgresRecorder(self.provider,self.store,**dict(self.kwargs,enabled=False))
        with self.assertRaises(ValueError): PostgresRecorder(self.provider,LLMRecordStore(self.db),**self.kwargs)
        self.assertEqual(self.provider.calls,0); self.assertEqual(self.db.rows,[])
    def test_stream_full_and_partial_close(self):
        list(self.rec.stream({},{})); self.assertEqual(self.status(),'ok')
        self.assertEqual(self.db.rows[-2].params[6:8],(2,3))
        stream=self.rec.stream({},{}); next(stream); stream.close()
        self.assertTrue(self.provider.closed); self.assertEqual(self.status(),'cancelled')
        self.assertEqual(self.db.rows[-2].params[6:8],(2,0))
    def test_error_partial_usage_and_no_error_text(self):
        self.provider.fail=True
        with self.assertRaisesRegex(RuntimeError,'private provider'): list(self.rec.stream({},{}))
        self.assertEqual(self.status(),'error'); self.assertEqual(self.db.rows[-2].params[6],2)
        self.assertNotIn('private provider',repr(self.db.rows))
    def test_complete_cancelled_with_reported_usage(self):
        self.provider.cancel=True
        with self.assertRaises(RecordingCancelled): self.rec.complete({}, {})
        self.assertEqual(self.status(),'cancelled'); self.assertEqual(self.db.rows[-2].params[6:8],(2,3))
    def test_write_failure_atomic_and_no_retry(self):
        self.db.fail=True
        with self.assertRaises(RecordingPersistenceError) as raised: self.rec.complete({}, {})
        self.assertNotIn('private',str(raised.exception)); self.assertEqual(self.provider.calls,1)
        self.assertEqual(self.db.rows,[]); self.assertEqual(self.db.events,['begin','rollback'])
    def test_bound_stream_preserves_observed_usage(self):
        self.rec=PostgresRecorder(self.provider,self.store,**self.kwargs,max_events=1)
        with self.assertRaises(CaptureLimit): list(self.rec.stream({},{}))
        self.assertTrue(self.provider.closed); self.assertEqual(self.status(),'error')
        self.assertEqual(self.db.rows[-2].params[6],2)
    def test_unused_stream_and_redaction_failure(self):
        self.rec.stream({},{}).close(); self.assertEqual(self.provider.calls,0)
        self.assertEqual(self.db.rows,[])
        rec=PostgresRecorder(self.provider,self.store,**dict(self.kwargs,redact=lambda *a:None))
        with self.assertRaises(CaptureLimit): rec.complete({}, {})
        self.assertEqual(self.provider.calls,0)
    def test_incomplete_exhaustion_not_success(self):
        self.provider.stream=lambda *a: iter([{'type':'message_start','usage':{'input_tokens':1}}])
        list(self.rec.stream({},{})); self.assertEqual(self.status(),'incomplete')

    def test_external_cancellation_mapping_not_lost_by_capture_removal(self):
        ctx={}
        stream=self.rec.stream(ctx,{})
        next(stream); ctx['cancelled']=True
        with self.assertRaises(RecordingCancelled): next(stream)
        self.assertEqual(self.status(),'cancelled')
        self.assertTrue(self.provider.closed)

    def test_response_redactor_interruption_is_settlement_uncertainty(self):
        import asyncio
        def redact(kind,text,scope):
            if kind=='response': raise asyncio.CancelledError('private redactor detail')
            return text
        self.rec=PostgresRecorder(self.provider,self.store,**dict(self.kwargs,redact=redact))
        with self.assertRaises(RecordingPersistenceError) as raised: self.rec.complete({}, {})
        self.assertNotIn('private',str(raised.exception))
        self.assertEqual(self.provider.calls,1); self.assertEqual(self.db.rows,[])

    def test_preflight_interruption_preserved_without_provider_work(self):
        import asyncio
        def redact(*args): raise asyncio.CancelledError('preflight')
        self.rec=PostgresRecorder(self.provider,self.store,**dict(self.kwargs,redact=redact))
        with self.assertRaises(asyncio.CancelledError): self.rec.complete({}, {})
        self.assertEqual(self.provider.calls,0); self.assertEqual(self.db.rows,[])

    def test_binding_rejection_and_error_before_provider_or_database(self):
        for validator in (lambda *a:False,lambda *a:1,lambda *a:None):
            rec=PostgresRecorder(self.provider,self.store,**dict(self.kwargs,binding_validator=validator))
            with self.assertRaises(RecordingBindingError): rec.complete({}, {})
            with self.assertRaises(RecordingBindingError): next(rec.stream({}, {}))
        def broken(*args): raise RuntimeError('private binding configuration')
        rec=PostgresRecorder(self.provider,self.store,**dict(self.kwargs,binding_validator=broken))
        with self.assertRaises(RecordingBindingError) as raised: rec.complete({}, {})
        self.assertNotIn('private',str(raised.exception))
        self.assertEqual(self.provider.calls,0); self.assertEqual(self.db.rows,[])

    def test_scope_attested_once_and_frozen_for_inflight_stream(self):
        seen=[]
        rec=PostgresRecorder(self.provider,self.store,**dict(self.kwargs,
            binding_validator=lambda scope,inner:seen.append((scope,inner)) or True))
        stream=rec.stream({},{}); next(stream)
        rec.scope=RecordingScope('other','other','other','new-model','new-profile')
        list(stream)
        self.assertEqual(seen,[(self.scope,self.provider)])
        self.assertEqual(self.db.rows[-1].params[:5],('model','profile','conv-1','task','worker'))
