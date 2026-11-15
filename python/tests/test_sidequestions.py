from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime,timezone
import json
import unittest
from artex.db.sidequestions import (Parent,Model,Snapshot,Exchange,Memory,SideQuestionStore,
    SideInvalid,SideBusy,SideParentGone,SideSessionMissing)

NOW=datetime(2026,11,15,tzinfo=timezone.utc)
P=Parent(conversation_id=3)
S=Snapshot(P,1,2,NOW,Model(model='dummy'),{'messages':[{'role':'user','text':'context'}]})
ROW=('req',10,'conv-3',0,'client','question','','running','',json.dumps(S.model.to_dict()),NOW,NOW,0,'{}','{}')

def exchange(**kwargs):
    e=Exchange(*ROW[:9],S.model,*ROW[10:13],{}, {})
    return replace(e,**kwargs)

class Fake:
    def __init__(self,results=()):self.results=list(results);self.calls=[]
    def call(self,op,q):
        self.calls.append((op,q)); result=self.results.pop(0)
        if isinstance(result,Exception): raise result
        return result
    def fetch_all(self,q):return self.call('all',q)
    def fetch_one(self,q):return self.call('one',q)
    def execute(self,q):return self.call('execute',q)
    @contextmanager
    def transaction(self):
        self.calls.append(('begin',None))
        try:yield self
        except BaseException:
            self.calls.append(('rollback',None));raise
        else:self.calls.append(('commit',None))

class SideTests(unittest.TestCase):
    def test_parent_keys_and_invalid_shapes(self):
        self.assertEqual(P.key(),'conv-3')
        self.assertEqual(Parent(task_id=2,exploration_id=8).key(),'task-2-exp-8-main')
        self.assertEqual(Parent(task_id=2,exploration_id=8,intent_id=9).key(),'task-2-exp-8-worker-i9')
        for kwargs in ({},{'conversation_id':True},{'conversation_id':-1},{'task_id':1},
                       {'intent_id':1},{'conversation_id':1,'task_id':1,'exploration_id':2}):
            with self.subTest(kwargs=kwargs), self.assertRaises(SideInvalid):Parent(**kwargs)

    def test_snapshot_binding_order_clean_json_and_compare(self):
        db=Fake([[(3,)],1]);store=SideQuestionStore(db,P)
        s=replace(S,request={'text':'nul\x00end','literal':r'\u0000'})
        self.assertTrue(store.save_snapshot(s))
        self.assertEqual([op for op,q in db.calls],['begin','all','execute','commit'])
        self.assertIn('FOR SHARE',db.calls[1][1].sql)
        q=db.calls[2][1]
        self.assertEqual(q.params[:7],('conv-3',3,None,None,None,1,2))
        self.assertIn('(side_question_sessions.run_id,side_question_sessions.version)<(EXCLUDED.run_id,EXCLUDED.version)',q.sql)
        self.assertEqual(json.loads(q.params[7])['request'],{'text':'nulend','literal':r'\u0000'})
        self.assertNotIn('context',q.sql)
        db=Fake([[(3,)],0]);self.assertFalse(SideQuestionStore(db,P).save_snapshot(S))

    def test_worker_parent_lock_order_and_missing_guards(self):
        p=Parent(task_id=2,exploration_id=8,intent_id=9);s=replace(S,parent=p)
        db=Fake([[(2,)],[(9,)],1]);SideQuestionStore(db,p).save_snapshot(s)
        self.assertEqual(db.calls[1][1].params,(2,8));self.assertIn('archived_at IS NULL',db.calls[1][1].sql)
        self.assertIn('deleted_at IS NULL',db.calls[1][1].sql)
        self.assertEqual(db.calls[2][1].params,(9,8));self.assertIn("kind='intent' AND state<>'stopped'",db.calls[2][1].sql)
        for results in ([],[[(2,)]]):
            db=Fake(results+[[]])
            with self.assertRaises(SideParentGone):SideQuestionStore(db,p).save_snapshot(s)
            self.assertEqual(db.calls[-1][0],'rollback')
            self.assertFalse(any(op=='execute' for op,q in db.calls))

    def test_snapshot_read_scoped_and_foreign_parent_rejected(self):
        db=Fake([[(json.dumps(S.to_dict()),)],[]]);store=SideQuestionStore(db,P)
        self.assertEqual(store.snapshot(),S);self.assertIsNone(store.snapshot())
        self.assertEqual(db.calls[0][1].params,('conv-3',))
        db=Fake([[(json.dumps(replace(S,parent=Parent(conversation_id=4)).to_dict()),)]])
        with self.assertRaises(SideInvalid):SideQuestionStore(db,P).snapshot()

    def test_start_and_idempotency_order(self):
        db=Fake([[(3,)],[(0,)],[],(False,),ROW]);store=SideQuestionStore(db,P)
        e,created=store.start(S,'client','question')
        self.assertTrue(created);self.assertEqual(e.id,'req')
        self.assertIn('FOR SHARE',db.calls[1][1].sql);self.assertIn('FOR UPDATE',db.calls[2][1].sql)
        self.assertEqual(db.calls[3][1].params,('conv-3',0,'client'))
        self.assertEqual(db.calls[5][1].params[1:5],('conv-3',0,'client','question'))
        self.assertEqual(db.calls[-1][0],'commit')
        db=Fake([[(3,)],[(0,)],[ROW]])
        e,created=SideQuestionStore(db,P).start(S,'client','question')
        self.assertFalse(created);self.assertEqual(e.id,'req');self.assertEqual(len(db.calls),5)
        self.assertEqual(db.calls[-1][0],'commit')

    def test_start_conflict_busy_and_missing_roll_back(self):
        for results,error,question in [([[(3,)],[(0,)],[ROW]],SideInvalid,'changed'),
                                       ([[(3,)],[(0,)],[],(True,)],SideBusy,'question'),
                                       ([[(3,)],[]],SideSessionMissing,'question')]:
            db=Fake(results)
            with self.assertRaises(error):SideQuestionStore(db,P).start(S,'client',question)
            self.assertEqual(db.calls[-1][0],'rollback')
            self.assertFalse(any(q and 'INSERT INTO' in q.sql for op,q in db.calls))

    def test_reads_history_and_replay_bounds(self):
        row2=list(ROW);row2[1]=11
        db=Fake([[ROW],[ROW],[ROW],[row2,ROW],[row2,ROW]])
        store=SideQuestionStore(db,P)
        self.assertEqual(store.request('req').id,'req');self.assertEqual(db.calls[0][1].params,('req','conv-3'))
        store.existing('client');self.assertIn('SELECT generation',db.calls[1][1].sql)
        store.current();self.assertIn("status='running'",db.calls[2][1].sql)
        self.assertEqual([e.ordinal for e in store.history(12,21)],[11,10])
        self.assertEqual(db.calls[3][1].params,('conv-3',12,21))
        self.assertEqual([e.ordinal for e in store.replay()],[10,11])
        self.assertIn("status='completed' ORDER BY ordinal DESC LIMIT 20",db.calls[4][1].sql)
        for kwargs in ({'limit':101},{'limit':0},{'before':-1},{'limit':True}):
            with self.assertRaises(SideInvalid):store.history(**kwargs)
        self.assertEqual(len(db.calls),5)

    def test_update_cas_parent_generation_sequence_terminal_guards(self):
        db=Fake([1,0]);store=SideQuestionStore(db,P)
        e=exchange(answer='answer',status='completed',sequence=1,usage={'input_tokens':17},context={'phase':'answering'})
        self.assertTrue(store.update(e));self.assertFalse(store.update(e))
        q=db.calls[0][1]
        for clause in ("r.status='running'",'r.sequence<$5','r.session_key=$8','r.generation=$9','s.generation=r.generation'):
            self.assertIn(clause,q.sql)
        self.assertEqual(q.params[:5],('req','answer','completed','',1));self.assertEqual(q.params[-2:],('conv-3',0))
        self.assertEqual(json.loads(q.params[5]),{'input_tokens':17})

    def test_clear_atomic_generation_and_delete_scoped(self):
        db=Fake([1,3]);SideQuestionStore(db,P).clear_history()
        self.assertEqual([op for op,q in db.calls],['begin','execute','execute','commit'])
        self.assertIn("generation=generation+1,memory='{}'",db.calls[1][1].sql)
        self.assertIn('DELETE FROM side_question_requests WHERE session_key=$1',db.calls[2][1].sql)
        self.assertEqual(db.calls[1][1].params,('conv-3',));self.assertEqual(db.calls[2][1].params,('conv-3',))
        db=Fake([1,RuntimeError('inert failure')])
        with self.assertRaises(RuntimeError):SideQuestionStore(db,P).clear_history()
        self.assertEqual(db.calls[-1][0],'rollback')

    def test_memory_late_writers_and_cursor(self):
        memory=Memory('summary',8,'checkpoint','main evidence',2)
        db=Fake([[(json.dumps(memory.to_dict()),)],1,[ROW],0,[]]);store=SideQuestionStore(db,P);e=exchange(ordinal=20)
        self.assertEqual(store.memory(e),memory)
        self.assertEqual(db.calls[0][1].params,('req',0,'conv-3'))
        store.save_memory(e,memory)
        q=db.calls[1][1]
        for clause in ('s.generation=$2','r.generation=s.generation',"r.status='running'"):
            self.assertIn(clause,q.sql)
        self.assertEqual(q.params[:2],('conv-3',0))
        self.assertEqual(store.replay_page(e,8)[0].id,'req')
        self.assertEqual(db.calls[2][1].params,('conv-3',0,8,20))
        self.assertIn('ordinal>$3 AND ordinal<$4 ORDER BY ordinal LIMIT 20',db.calls[2][1].sql)
        with self.assertRaises(SideParentGone):store.save_memory(e,memory)
        with self.assertRaises(SideParentGone):store.memory(e)

    def test_foreign_dto_rejected_without_queries(self):
        db=Fake();store=SideQuestionStore(db,P);e=exchange(session_key='conv-4')
        for action in (lambda:store.save_snapshot(replace(S,parent=Parent(conversation_id=4))),
                       lambda:store.update(e),lambda:store.memory(e),lambda:store.save_memory(e,Memory()),lambda:store.replay_page(e)):
            with self.assertRaises(SideInvalid):action()
        self.assertEqual(db.calls,[])

    def test_invalid_json_and_model_never_written(self):
        db=Fake();store=SideQuestionStore(db,P)
        for s in (replace(S,request={'bad':float('nan')}),replace(S,request={'bad':object()}),
                  replace(S,captured_at=NOW.replace(tzinfo=None)),replace(S,model=Model(streaming=1))):
            with self.assertRaises(SideInvalid):store.save_snapshot(s)
        with self.assertRaises(SideInvalid):store.update(exchange(usage={'bad':float('inf')}))
        with self.assertRaises(SideInvalid):store.update(exchange(status=[]))
        self.assertEqual(db.calls,[])
        model=json.loads(ROW[9]);model['api_key']='dummy-not-a-real-key'
        row=list(ROW);row[9]=json.dumps(model)
        e=SideQuestionStore(Fake([[row]]),P).request('req')
        self.assertNotIn('api_key',e.to_dict()['model'])
        self.assertNotIn('session_key',e.to_dict());self.assertNotIn('generation',e.to_dict())

    def test_interrupt_parent_only_retains_partial_answer(self):
        db=Fake([2]);self.assertEqual(SideQuestionStore(db,P).interrupt(),2)
        q=db.calls[0][1]
        self.assertIn("status='running' AND session_key=$1",q.sql)
        self.assertIn('sequence=sequence+1',q.sql);self.assertNotIn('answer=',q.sql)
        self.assertEqual(q.params,('conv-3','Service restarted; answer interrupted'))

if __name__=='__main__':unittest.main()
