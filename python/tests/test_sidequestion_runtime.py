"""Inert providers and persistence doubles only; no model or network access."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime,timezone
import unittest
from artex.db.sidequestions import (Parent,Model,Snapshot,Exchange,Memory,SideQuestionStore,SideParentGone,SideBusy,SideInvalid)
from artex.sidequestion import Capture,Parent as CaptureParent,Model as CaptureModel
from artex.sidequestion.runtime import (SideQuestionCoordinator,ProviderBinding,SideUnavailable,SideCapacity,SidePersistenceError)

NOW=datetime(2026,11,15,tzinfo=timezone.utc)
MODEL=Model(profile_id=7,model='dummy',identity='identity-fixture',window_tokens=200000)
SNAP=Snapshot(Parent(conversation_id=3),1,1,NOW,MODEL,{'messages':[{'role':'user','content':[{'type':'text','text':'main context'}]}],'tools':[{'name':'never-run'}],'tool_choice':'auto'})

class Store:
    """Test-only semantic double; real bound SQL contracts tested separately."""
    def __init__(self,snapshot=SNAP):
        self.snapshot_value=deepcopy(snapshot);self.parent=snapshot.parent;self.key=self.parent.key()
        self.row=None;self.generation=0;self.writes=[];self.saved_memory=Memory();self.fail_writes=0;self.starts=0
    def existing(self,client):return deepcopy(self.row) if self.row and self.row.client_id==client else None
    def snapshot(self):return deepcopy(self.snapshot_value)
    def start(self,snapshot,client,question):
        self.starts+=1
        if self.row and self.row.status=='running':raise SideBusy()
        self.row=Exchange('req-'+self.key,self.starts,self.key,self.generation,client,question,'','running','',snapshot.model,NOW,NOW,0,{}, {})
        return deepcopy(self.row),True
    def request(self,id):return deepcopy(self.row)
    def memory(self,e):
        if not self.row or self.row.generation!=e.generation:raise SideParentGone()
        return deepcopy(self.saved_memory)
    def replay_page(self,e,after):return []
    def save_memory(self,e,memory):
        if not self.row or self.row.generation!=e.generation:raise SideParentGone()
        self.saved_memory=deepcopy(memory)
    def update(self,e):
        if self.fail_writes:
            self.fail_writes-=1;raise RuntimeError('dummy-secret-storage-detail')
        if not self.row or self.row.status!='running' or self.row.generation!=e.generation or e.sequence<=self.row.sequence:return False
        self.row=deepcopy(e);self.writes.append(deepcopy(e));return True
    def save_snapshot(self,s):
        if s.parent!=self.parent:raise SideInvalid()
        self.snapshot_value=deepcopy(s);return True
    def clear_for_test(self):self.generation+=1;self.row=None

class Provider:
    def __init__(self,action=None):self.calls=[];self.action=action
    def complete(self,request):
        self.calls.append(deepcopy(request))
        if self.action:self.action()
        return {'role':'assistant','content':[{'type':'text','text':'answer'}]},'end_turn',{'input_tokens':5,'output_tokens':2}

class RuntimeTests(unittest.TestCase):
    def coordinator(self,p=None,**kwargs):
        p=p or Provider()
        return SideQuestionCoordinator(lambda model:ProviderBinding(model,p),**kwargs),p

    def test_actual_service_bridge_success_and_replay_identity(self):
        store=Store();c,p=self.coordinator();a=c.admit(store,'client',' question ')
        self.assertTrue(a.created);self.assertEqual(p.calls,[])
        outcome=c.execute(a.handle)
        self.assertTrue(outcome.persisted);self.assertEqual(store.row.status,'completed')
        self.assertEqual(store.row.answer,'answer');self.assertEqual(store.row.usage,{'input_tokens':5,'output_tokens':2})
        self.assertNotIn('tools',p.calls[0]);self.assertNotIn('tool_choice',p.calls[0])
        self.assertIn('main context',str(p.calls[0]));self.assertEqual(SNAP.request['tools'],[{'name':'never-run'}])
        self.assertEqual(len(p.calls),1)
        seq=[e.sequence for e in store.writes];self.assertEqual(seq,sorted(set(seq)))
        duplicate=c.admit(store,'client','question')
        self.assertFalse(duplicate.created);self.assertIsNone(duplicate.handle);self.assertEqual(store.starts,1)
        with self.assertRaises(SideInvalid):c.execute(a.handle)
        with self.assertRaises(SideInvalid):c.admit(store,'client','different')

    def test_missing_snapshot_profile_and_changed_binding_fail_before_admission(self):
        store=Store();store.snapshot_value=None;c,p=self.coordinator()
        with self.assertRaises(SideUnavailable):c.admit(store,'client','question')
        self.assertEqual(store.starts,0)
        store=Store()
        for resolver in (lambda m:None,lambda m:ProviderBinding(replace(m,identity='changed'),p),lambda m:ProviderBinding(replace(m,profile_id=8),p)):
            c=SideQuestionCoordinator(resolver)
            with self.assertRaises(SideUnavailable):c.admit(store,'client','question')
        self.assertEqual(store.starts,0);self.assertEqual(p.calls,[])

    def test_profile_rechecked_before_delayed_execution(self):
        provider=Provider();changed=[False]
        def resolve(m):return ProviderBinding(replace(m,identity='changed') if changed[0] else m,provider)
        c=SideQuestionCoordinator(resolve);store=Store();a=c.admit(store,'client','question');changed[0]=True
        result=c.execute(a.handle)
        self.assertTrue(result.persisted);self.assertEqual(result.exchange.status,'failed');self.assertEqual(provider.calls,[])

    def test_explicit_cancel_queued_never_calls_provider(self):
        c,p=self.coordinator();store=Store();a=c.admit(store,'client','question')
        self.assertTrue(c.cancel(a.handle));self.assertEqual(store.row.status,'running')
        result=c.execute(a.handle)
        self.assertEqual(result.exchange.status,'cancelled');self.assertTrue(result.persisted);self.assertEqual(p.calls,[])
        self.assertFalse(c.cancel(a.handle))

    def test_cancel_during_completion_preserves_usage_and_answer(self):
        cancelled=[False];p=Provider(lambda:cancelled.__setitem__(0,True));c,_=self.coordinator(p)
        store=Store();a=c.admit(store,'client','question',cancelled=lambda:cancelled[0]);result=c.execute(a.handle)
        self.assertEqual(result.exchange.status,'cancelled');self.assertEqual(store.row.answer,'answer')
        self.assertEqual(store.row.usage,{'input_tokens':5,'output_tokens':2})

    def test_stream_failure_retains_partial_text_usage_no_error_leak(self):
        class Stream(Provider):
            def stream(self,request):
                self.calls.append(request)
                yield {'type':'message_start','usage':{'input_tokens':9}}
                yield {'type':'text_delta','text':'partial'}
                raise RuntimeError('dummy-provider-secret')
        store=Store(replace(SNAP,model=replace(MODEL,streaming=True)));c,p=self.coordinator(Stream())
        result=c.execute(c.admit(store,'client','question').handle)
        self.assertEqual(result.exchange.status,'failed');self.assertEqual(store.row.answer,'partial')
        self.assertEqual(store.row.usage,{'input_tokens':9});self.assertNotIn('dummy-provider-secret',store.row.error)

    def test_clear_during_provider_cannot_resurrect_history(self):
        store=Store();c,p=self.coordinator(Provider(store.clear_for_test))
        result=c.execute(c.admit(store,'client','question').handle)
        self.assertTrue(result.stale);self.assertFalse(result.persisted);self.assertIsNone(store.row)
        self.assertEqual(len(p.calls),1)
        second=c.admit(store,'new','fresh');self.assertEqual(second.exchange.generation,1)
        c.cancel(second.handle);c.execute(second.handle)

    def test_clear_before_execution_stops_provider(self):
        store=Store();c,p=self.coordinator();a=c.admit(store,'client','question');store.clear_for_test()
        result=c.execute(a.handle)
        self.assertFalse(result.persisted);self.assertTrue(result.stale);self.assertEqual(p.calls,[])

    def test_capacity_and_per_parent_busy_release(self):
        c,p=self.coordinator(max_running=1);store=Store();a=c.admit(store,'client','question')
        with self.assertRaises(SideBusy):c.admit(store,'other','question')
        other=Store(replace(SNAP,parent=Parent(conversation_id=4)))
        with self.assertRaises(SideCapacity):c.admit(other,'client','question')
        self.assertEqual(other.starts,0)
        c.cancel(a.handle);c.execute(a.handle)
        b=c.admit(other,'client','question');c.cancel(b.handle);c.execute(b.handle)

    def test_deadline_and_failure_to_persist_are_not_success(self):
        clock=[0];c,p=self.coordinator(clock=lambda:clock[0]);store=Store();a=c.admit(store,'client','question');clock[0]=121
        result=c.execute(a.handle)
        self.assertTrue(result.persisted);self.assertEqual(result.exchange.status,'failed');self.assertIn('deadline',result.exchange.error)
        self.assertEqual(p.calls,[])
        c,p=self.coordinator();store=Store();a=c.admit(store,'client','question');store.fail_writes=10
        with self.assertRaises(SidePersistenceError) as caught:c.execute(a.handle)
        self.assertNotIn('dummy-secret',str(caught.exception));self.assertEqual(store.row.status,'running')
        self.assertEqual(p.calls,[])

    def test_capture_bridge_and_validation(self):
        c,p=self.coordinator();store=Store()
        capture=Capture(CaptureParent(conversation_id=3),lambda snap:c.publish_capture(store,snap),run_id=22)
        capture.save(SNAP.request,CaptureModel(**MODEL.to_dict()))
        self.assertEqual(store.snapshot_value.run_id,22);self.assertEqual(store.snapshot_value.version,1)
        self.assertEqual(store.snapshot_value.parent,store.parent)
        for client,question in [('a b','question'),('x'*129,'question'),('client',''),('client','q'*4001)]:
            with self.assertRaises(SideInvalid):c.admit(store,client,question)
        self.assertEqual(store.starts,0)

    def test_each_model_boundary_uses_fresh_resolved_provider(self):
        from unittest.mock import patch
        from artex.sidequestion import Answer,ContextInfo
        first=Provider();second=Provider();active=[first]
        first.action=lambda:active.__setitem__(0,second)
        c=SideQuestionCoordinator(lambda m:ProviderBinding(m,active[0]));store=Store()
        class Service:
            def __init__(self,provider):self.provider=provider
            def respond(self,*args,**kwargs):
                self.provider.complete({'phase':'summary'})
                self.provider.complete({'phase':'answer'})
                return Answer('done'),ContextInfo()
        with patch('artex.sidequestion.runtime.SideQuestionService',Service):
            result=c.execute(c.admit(store,'client','question').handle)
        self.assertEqual(len(first.calls),1);self.assertEqual(len(second.calls),1)
        self.assertEqual(result.exchange.status,'completed')

    def test_changed_profile_keeps_prior_summary_usage(self):
        from unittest.mock import patch
        from artex.sidequestion import Answer,AnswerError,ContextInfo
        changed=[False];provider=Provider(lambda:changed.__setitem__(0,True))
        c=SideQuestionCoordinator(lambda m:ProviderBinding(replace(m,identity='changed') if changed[0] else m,provider))
        class Service:
            def __init__(self,provider):self.provider=provider
            def respond(self,*args,**kwargs):
                _,_,usage=self.provider.complete({'phase':'summary'})
                try:self.provider.complete({'phase':'answer'})
                except Exception as exc:raise AnswerError(exc,Answer(usage=usage),ContextInfo(phase='answering')) from exc
                raise AssertionError('changed profile allowed another model call')
        store=Store()
        with patch('artex.sidequestion.runtime.SideQuestionService',Service):
            result=c.execute(c.admit(store,'client','question').handle)
        self.assertEqual(len(provider.calls),1);self.assertEqual(result.exchange.status,'failed')
        self.assertEqual(result.exchange.usage,{'input_tokens':5,'output_tokens':2})
        self.assertTrue(result.persisted)

    def test_real_store_sql_executor_bridge(self):
        # Scripted executor through the actual store; not a substitute persistence implementation.
        from contextlib import contextmanager
        import json
        row=('id',1,'conv-3',0,'client','question','','running','',json.dumps(MODEL.to_dict()),NOW,NOW,0,'{}','{}')
        class Executor:
            def __init__(self):self.calls=[]
            @contextmanager
            def transaction(self):yield self
            def fetch_all(self,q):
                self.calls.append(q)
                if q.sql.startswith('SELECT snapshot'):return [(json.dumps(SNAP.to_dict()),)]
                if q.sql.startswith('SELECT id FROM conversations'):return [(3,)]
                if q.sql.startswith('SELECT generation'):return [(0,)]
                if q.sql.startswith('SELECT s.memory'):return [('{}',)]
                if 'WHERE id=$1 AND session_key=$2' in q.sql:return [row]
                return []
            def fetch_one(self,q):
                self.calls.append(q)
                return (False,) if q.sql.startswith('SELECT EXISTS') else row
            def execute(self,q):self.calls.append(q);return 1
        executor=Executor();store=SideQuestionStore(executor,SNAP.parent);c,p=self.coordinator()
        result=c.execute(c.admit(store,'client','question').handle)
        self.assertTrue(result.persisted);self.assertEqual(result.exchange.answer,'answer')
        writes=[q for q in executor.calls if q.sql.startswith('UPDATE side_question_requests')]
        self.assertGreaterEqual(len(writes),3)
        self.assertEqual(writes[-1].params[2],'completed')
        self.assertEqual(writes[-1].params[-2:],('conv-3',0))
        self.assertTrue(all('s.generation=r.generation' in q.sql for q in writes))

if __name__=='__main__':unittest.main()
