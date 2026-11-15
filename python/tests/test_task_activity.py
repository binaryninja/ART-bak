import asyncio,json,unittest
from contextlib import nullcontext
from datetime import datetime,timezone
from artex.db.task_activity import *
from artex.db.postgres import NoRowsError
from test_conversation_activity import TransactionFake

class TaskActivityTests(unittest.TestCase):
    def make(self,values,current=lambda:True):
        self.db=TransactionFake(values)
        return TaskActivityStore(self.db,3,9,lifecycle_guard=nullcontext,current=current,scope_identity={"task_id":3,"exploration_id":9})
    def test_exact_columns_bound_metadata_usage_segment(self):
        s=self.make([(9,None),[],(12,)])
        a=Activity(worker='mainagent',kind='result',summary="x';--",metadata={'safe':[1]},input_tokens=0,main_seg=2)
        self.assertEqual(s.append_many([a]),[12]);self.assertEqual(self.db.events,['begin','commit'])
        q=self.db.calls[-1][1];self.assertEqual(len(q.params),15)
        self.assertEqual(q.params,(9,None,'mainagent','result','','',False,"x';--",'', '{"safe":[1]}',0,None,None,None,2))
        self.assertIn('$10::jsonb',q.sql);self.assertNotIn("x';--",q.sql);self.assertNotIn('updated_at',q.sql)
    def test_parent_missing_deleted_wrong_exploration(self):
        for row in (NoRowsError(),None,(10,None),(9,'deleted')):
            s=self.make([row])
            with self.assertRaises(TaskActivityMissing):s.append_many([Activity()])
            self.assertEqual(len(self.db.calls),1);self.assertEqual(self.db.events,['begin','rollback'])
    def test_archive_states(self):
        for state in ('archiving','archived','failed','unknown'):
            s=self.make([(9,None),[(state,)]])
            with self.assertRaises(TaskActivityUnavailable):s.append_many([Activity()])
            self.assertEqual(len(self.db.calls),2)
        s=self.make([(9,None),[('archive_failed',)],(1,)])
        self.assertEqual(s.append_many([Activity()]),[1])
    def test_node_ownership_checked(self):
        s=self.make([(9,None),[],[]])
        with self.assertRaises(TaskActivityMissing):s.append_many([Activity(node_id=8)])
        q=self.db.calls[-1][1];self.assertEqual(q.params,(8,9));self.assertIn('FOR KEY SHARE',q.sql)
    def test_second_failure_and_cancel_roll_back(self):
        for error in (RuntimeError('fixture'),asyncio.CancelledError()):
            s=self.make([(9,None),[],(1,),error])
            with self.assertRaises(type(error)):s.append_many([Activity(),Activity()])
            self.assertEqual(self.db.events,['begin','rollback'])
    def test_lifetime_rechecked_before_commit(self):
        values=iter([True,False]);s=self.make([(9,None),[],(1,)],lambda:next(values))
        with self.assertRaises(TaskActivityUnavailable):s.append_many([Activity()])
        self.assertEqual(self.db.events,['begin','rollback'])
    def test_prevalidation_and_lifecycle_required(self):
        for a in (Activity(metadata={'x':float('nan')}),Activity(metadata={'x':'\x00'}),Activity(main_seg=2),Activity(main_seg=-1)):
            s=self.make([])
            with self.assertRaises(TaskActivityInvalid):s.append_many([a])
            self.assertEqual(self.db.calls,[])
        with self.assertRaises(TaskActivityInvalid):TaskActivityStore(None,3,9,lifecycle_guard=None,current=None,scope_identity={})
        s=self.make([],lambda:False)
        with self.assertRaises(TaskActivityUnavailable):s.append_many([])
        self.assertEqual(self.db.events,[])
    def test_list_jsonb_and_nullable_zero(self):
        now=datetime.now(timezone.utc)
        row=(7,None,'mainagent','result','','',False,'summary',{'nested':[1]},now,0,None,None,None,0)
        s=self.make([[row]])
        items,cursor=s.list(since_id=2)
        self.assertEqual(cursor,7);self.assertEqual(items[0].metadata,{'nested':[1]});self.assertEqual(items[0].main_seg,0)
        self.assertEqual(self.db.calls[0][1].params,(3,9,None,2,300))
        self.assertNotIn('detail',self.db.calls[0][1].sql)
    def test_detail_scoped_missing_and_errors(self):
        s=self.make([[]]);self.assertEqual(s.detail(7),'');self.assertEqual(self.db.calls[0][1].params,(7,9,3))
        s=self.make([RuntimeError('fixture')])
        with self.assertRaises(RuntimeError):s.detail(7)
