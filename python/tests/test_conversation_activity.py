from datetime import datetime,timezone
import unittest
from artex.db.conversation_activity import Activity,ActivityInvalid,ConversationActivityStore

NOW=datetime(2026,11,15,12,30,tzinfo=timezone.utc)
ROW=(11,'worker','result','fixture','call1',False,'summary',NOW,0,2,None,3)

class Fake:
    def __init__(self,results=()):self.results=list(results);self.calls=[]
    def call(self,mode,q):
        self.calls.append((mode,q));value=self.results.pop(0)
        if isinstance(value,Exception):raise value
        return value
    def fetch_one(self,q):return self.call('one',q)
    def fetch_all(self,q):return self.call('all',q)

class ActivityTests(unittest.TestCase):
    def test_append_exact_columns_bound_values_and_nullable_usage(self):
        db=Fake([(17,)]);store=ConversationActivityStore(db,3)
        a=Activity(worker='worker',kind='result',tool='fixture',tool_use_id='call1',is_error=True,summary="x';DROP",detail='full detail',input_tokens=0,output_tokens=2)
        self.assertEqual(store.append(a),17)
        q=db.calls[0][1]
        self.assertEqual(q.params,(3,'worker','result','fixture','call1',True,"x';DROP",'full detail',0,2,None,None))
        self.assertNotIn("x';DROP",q.sql);self.assertIn('RETURNING id',q.sql)
        self.assertNotIn('created_at',q.sql) # database assigns actual timestamp
        self.assertIn("NULLIF($8,'')",q.sql)

    def test_parent_foreign_key_failure_propagates_no_fallback(self):
        error=RuntimeError('fixture foreign key violation');db=Fake([error])
        with self.assertRaises(RuntimeError) as caught:ConversationActivityStore(db,99).append(Activity())
        self.assertIs(caught.exception,error);self.assertEqual(len(db.calls),1)

    def test_text_cleanup_preserves_valid_utf8_and_groups_invalid_bytes(self):
        db=Fake([(1,)])
        ConversationActivityStore(db,3).append(Activity(worker=b'a\0\xff\xfez',summary='snowman \u2603\0',detail='x\ud800\ud801y'))
        params=db.calls[0][1].params
        self.assertEqual(params[1],'a\ufffdz');self.assertEqual(params[6],'snowman \u2603');self.assertEqual(params[7],'x\ufffdy')

    def test_list_ascending_tail_and_cursor_nullable_zero_usage(self):
        row2=list(ROW);row2[0]=12
        db=Fake([[ROW,tuple(row2)],[]]);store=ConversationActivityStore(db,3)
        rows,cursor=store.list(8,1000)
        self.assertEqual([r.id for r in rows],[11,12]);self.assertEqual(cursor,12)
        q=db.calls[0][1];self.assertEqual(q.params,(3,8,1000));self.assertIn('id>$2 ORDER BY id LIMIT $3',q.sql)
        self.assertNotIn('detail',q.sql)
        data=rows[0].to_dict();self.assertEqual(data['input_tokens'],0);self.assertNotIn('cache_read_tokens',data)
        self.assertEqual(data['created_at'],NOW.isoformat());self.assertNotIn('node_id',data);self.assertNotIn('detail',data)
        self.assertEqual(store.list(12),( [],12))

    def test_reverse_page_extra_row_and_ascending_display(self):
        def row(id):return (id,*ROW[1:])
        db=Fake([[row(14),row(13),row(12)],[row(11)],[]]);store=ConversationActivityStore(db,3)
        rows,more=store.page(20,2)
        self.assertEqual([r.id for r in rows],[13,14]);self.assertTrue(more)
        self.assertEqual(db.calls[0][1].params,(3,20,3));self.assertIn('ORDER BY id DESC',db.calls[0][1].sql)
        self.assertNotIn('detail',db.calls[0][1].sql)
        rows,more=store.page(13,2);self.assertEqual([r.id for r in rows],[11]);self.assertFalse(more)
        self.assertEqual(store.page(),([],False))

    def test_nonpositive_defaults_negative_cursor_and_bounds(self):
        db=Fake([[],[]]);store=ConversationActivityStore(db,3)
        self.assertEqual(store.list(-4,0),([], -4));store.page(-2,-1)
        self.assertEqual(db.calls[0][1].params,(3,-4,500));self.assertEqual(db.calls[1][1].params,(3,-2,201))
        for action in (lambda:store.list(limit=10001),lambda:store.page(limit=True),lambda:store.list(since_id='1'),lambda:store.detail(0)):
            with self.assertRaises(ActivityInvalid):action()
        self.assertEqual(len(db.calls),2)

    def test_detail_missing_null_and_cross_parent_binding(self):
        db=Fake([[('full',)],[(None,)],[]]);store=ConversationActivityStore(db,3)
        self.assertEqual(store.detail(11),'full');self.assertEqual(store.detail(12),'');self.assertEqual(store.detail(99),'')
        self.assertEqual(db.calls[0][1].params,(11,3));self.assertIn('id=$1 AND conversation_id=$2',db.calls[0][1].sql)

    def test_shared_dto_fields_omit_detail_and_preserve_optional_zero(self):
        metadata={'source':'fixture'}
        a=Activity(node_id=0,metadata=metadata,main_seg=0,source_task_id=9,inherited=True,detail='never exposed')
        dto=a.to_dict()
        self.assertEqual(dto['node_id'],0);self.assertEqual(dto['main_seg'],0);self.assertTrue(dto['inherited'])
        self.assertNotIn('detail',dto);self.assertNotIn('worker',dto)
        dto['metadata']['source']='edited';self.assertEqual(metadata,{'source':'fixture'})
        db=Fake([(1,)]);ConversationActivityStore(db,3).append(a)
        self.assertNotIn('node_id',db.calls[0][1].sql);self.assertNotIn('metadata',db.calls[0][1].sql)

    def test_validation_precedes_write(self):
        for kwargs in ({'input_tokens':True},{'output_tokens':2**31},{'is_error':1},{'summary':None}):
            db=Fake()
            with self.assertRaises(ActivityInvalid):ConversationActivityStore(db,3).append(Activity(**kwargs))
            self.assertEqual(db.calls,[])
        for id in (0,-1,True,'3',2**63):
            with self.assertRaises(ActivityInvalid):ConversationActivityStore(Fake(),id)

if __name__=='__main__':unittest.main()

class TransactionFake(Fake):
    def __init__(self,results):super().__init__(results);self.events=[]
    def call(self,mode,q):
        self.calls.append((mode,q));value=self.results.pop(0)
        if isinstance(value,BaseException):raise value
        return value
    def execute(self,q):return self.call('execute',q)
    def transaction(self):return self
    def __enter__(self):self.events.append('begin');return self
    def __exit__(self,kind,value,tb):self.events.append('rollback' if kind else 'commit')

class ActivityAtomicTests(unittest.TestCase):
    def test_batch_locks_parent_then_appends_then_touches_in_one_commit(self):
        db=TransactionFake([(3,),(11,),(12,),1])
        ids=ConversationActivityStore(db,3).append_many_and_touch([Activity(kind='human'),Activity(kind='result')],title_if_default='First message')
        self.assertEqual(ids,[11,12]);self.assertEqual(db.events,['begin','commit'])
        self.assertIn('FOR UPDATE',db.calls[0][1].sql)
        q=db.calls[-1][1];self.assertEqual(q.params,(3,'First message'))
        self.assertIn("title='New conversation'",q.sql);self.assertIn('updated_at=now()',q.sql)

    def test_missing_parent_does_not_append(self):
        from artex.db.conversation_activity import ActivityMissing
        db=TransactionFake([None])
        with self.assertRaises(ActivityMissing):ConversationActivityStore(db,3).append_many_and_touch([Activity()])
        self.assertEqual(len(db.calls),1);self.assertEqual(db.events,['begin','rollback'])

    def test_late_append_and_touch_failure_roll_back_whole_batch(self):
        for results in ([(3,),(11,),RuntimeError('fixture')],[(3,),(11,),(12,),RuntimeError('fixture')]):
            db=TransactionFake(results)
            with self.assertRaises(RuntimeError):ConversationActivityStore(db,3).append_many_and_touch([Activity(),Activity()])
            self.assertEqual(db.events,['begin','rollback'])

    def test_cancellation_rolls_back_without_swallowing(self):
        import asyncio
        error=asyncio.CancelledError();db=TransactionFake([(3,),(11,),error])
        with self.assertRaises(asyncio.CancelledError) as caught:
            ConversationActivityStore(db,3).append_many_and_touch([Activity(),Activity()])
        self.assertIs(caught.exception,error);self.assertEqual(db.events,['begin','rollback'])

    def test_prevalidation_no_partial_batch_or_missing_transaction_fallback(self):
        from artex.db.conversation_activity import ActivityUnavailable
        db=TransactionFake([])
        with self.assertRaises(ActivityInvalid):ConversationActivityStore(db,3).append_many_and_touch([Activity(),Activity(is_error=1)])
        self.assertEqual(db.calls,[]);self.assertEqual(db.events,[])
        with self.assertRaises(ActivityUnavailable):ConversationActivityStore(Fake(),3).append_many_and_touch([])

    def test_empty_batch_still_touches_no_title_change(self):
        db=TransactionFake([(3,),1]);self.assertEqual(ConversationActivityStore(db,3).append_many_and_touch([]),[])
        self.assertEqual(db.calls[-1][1].params,(3,None));self.assertEqual(db.events,['begin','commit'])

class ActivityDriverMissingTests(unittest.TestCase):
    def test_missing_locked_parent_no_rows_rolls_back_no_append(self):
        from artex.db.postgres import NoRowsError
        from artex.db.conversation_activity import ActivityMissing
        db=TransactionFake([NoRowsError()])
        with self.assertRaises(ActivityMissing):ConversationActivityStore(db,3).append_many_and_touch([Activity()])
        self.assertEqual(db.events,['begin','rollback']);self.assertEqual(len(db.calls),1)

    def test_detail_missing_uses_fetch_all_not_single_row_contract(self):
        db=Fake([[]]);self.assertEqual(ConversationActivityStore(db,3).detail(5),'')
        self.assertEqual(db.calls[0][0],'all')
