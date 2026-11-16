"""Synthetic SQL task activity, no user transcripts or running tasks."""
import unittest,json
from contextlib import contextmanager,nullcontext
from artex.db.task_activity_read import *
from artex.server.task_activity import TaskActivityService
from artex.server.core import Request,Router
from artex.server.streaming import ClosingIterator

class DB:
    def __init__(self):self.queries=[];self.rows=[self.row(i) for i in range(1,6)];self.after=lambda:None
    @staticmethod
    def row(i):return {'seq':i,'worker':'planner','kind':'result','ts':'2026-01-01T00:00:00Z','is_error':False,'summary':f'private-{i}','input_tokens':0}
    @contextmanager
    def transaction(self):yield self
    def fetch_all(self,q):
        self.queries.append(q)
        if q.sql.startswith('SELECT exploration_id'):return [(9,)]
        if q.sql.startswith('SELECT id FROM exploration_nodes'):return [(q.params[0],)] if q.params[0]==7 else []
        if q.sql.startswith('SELECT CASE'):
            self.after();return [('private-detail',)]
        if 'WITH ' in q.sql:
            exp,node,cursor,limit,seg,maximum=q.params
            rows=[x for x in self.rows if (x['seq']<cursor or cursor==0)] if 'ORDER BY a.id DESC' in q.sql else [x for x in self.rows if x['seq']>cursor]
            if 'ORDER BY a.id DESC' in q.sql:rows=list(reversed(rows))
            rows=rows[:limit];result=[(max([0]+[x['seq'] for x in self.rows]),x['seq'],json.dumps(x)) for x in rows] or [(5,None,None)]
            self.after();return result
        raise AssertionError(q.sql)
class Tests(unittest.TestCase):
    def setUp(self):
        self.db=DB();self.allowed=True;self.live=True;self.http_allowed=True
        self.store=TaskActivityReadStore(self.db,ActivityReadScope(5,9,'r1'),current=lambda s:self.live,lifecycle_guard=lambda s:nullcontext(),authorize=lambda s,q:True,raw_authorize=lambda s,q:self.allowed,max_rows=2)
        self.service=TaskActivityService(self.store,authorize=lambda *a:self.http_allowed,stream_current=lambda *a:self.live,max_batches=5)
    def request(self,action,url,params=None,headers=None):return self.service.handle(action,Request('GET',url,headers or {}),params or {})
    def test_history_task_snapshot_not_page_cursor(self):
        result=self.store.read(ActivitySelection('history',before=4,limit=2,session='plan'))
        self.assertEqual([x['seq'] for x in result['items']],[2,3]);self.assertEqual(result['snapshot_cursor'],5)
        self.assertEqual(result['earliest_cursor'],2);self.assertTrue(result['has_more'])
        sql=self.db.queries[-1].sql
        self.assertIn('SUM(octet_length(payload)) OVER()',sql);self.assertIn('CASE WHEN sized.bytes',sql)
    def test_list_detail_shapes(self):
        r=self.request('list','/api/exploration/activity?task=5&since=2').json()
        self.assertEqual(r['cursor'],4);self.assertEqual(r['items'][0]['input_tokens'],0)
        r=self.request('detail','/api/exploration/activity/3?task=5',{'seq':'3'}).json()
        self.assertEqual(r,{'detail':'private-detail'})
    def test_main_uses_actual_sessions_table(self):
        self.store.read(ActivitySelection('history',limit=2,session='main'))
        self.assertIn('MAX(seq)',self.db.queries[-1].sql);self.assertIn('FROM main_sessions',self.db.queries[-1].sql)
    def test_wrong_task_and_nonlocal_intent_failclosed(self):
        self.assertEqual(self.request('list','/api/exploration/activity?task=6').status,403)
        self.assertEqual(self.db.queries,[])
        self.assertEqual(self.request('history','/api/exploration/activity/history?task=5&session=intent:99&limit=2').status,404)
    def test_postfetch_revocation_and_callback_swap(self):
        self.db.after=lambda:setattr(self,'allowed',False)
        self.assertEqual(self.request('list','/api/exploration/activity?task=5').status,403)
        self.allowed=True;self.db.after=lambda:setattr(self.store,'raw_authorize',lambda *a:True)
        self.assertEqual(self.request('list','/api/exploration/activity?task=5').status,503)
    def test_http_grant_revocation_after_fetch(self):
        self.db.after=lambda:setattr(self,'http_allowed',False)
        self.assertEqual(self.request('detail','/api/exploration/activity/3?task=5',{'seq':'3'}).status,403)
    def test_sse_replays_multiple_pages_and_last_event_id_wins(self):
        response=self.request('stream','/api/exploration/activity/stream?task=5&since=0',headers={'Last-Event-ID':'2'})
        chunks=list(ClosingIterator(response))
        self.assertEqual([int(c.split(b'\n')[0][4:]) for c in chunks],[3,4,5])
        self.assertTrue(all(b'data: ' in c for c in chunks));self.assertTrue(response.body.closed)
    def test_stream_resume_denial_drops_prefetched_private_rows(self):
        response=self.request('stream','/api/exploration/activity/stream?task=5')
        stream=ClosingIterator(response);self.assertIn(b'private-1',next(stream));self.allowed=False
        self.assertEqual(list(stream),[]);self.assertTrue(stream.failed);self.assertEqual(response.body.pending,[])
    def test_stream_close_idempotent_no_reads(self):
        response=self.request('stream','/api/exploration/activity/stream?task=5');stream=ClosingIterator(response)
        stream.close();stream.close();self.assertEqual(self.db.queries,[]);self.assertEqual(list(stream),[])
    def test_stream_finite_bound_and_new_poll_rows(self):
        self.service=TaskActivityService(self.store,authorize=lambda *a:True,stream_current=lambda *a:True,max_batches=1)
        chunks=list(ClosingIterator(self.request('stream','/api/exploration/activity/stream?task=5')))
        self.assertEqual(len(chunks),2)
    def test_routes_auth_before_read(self):
        router=Router(lambda r:False);self.service.register(router)
        self.assertEqual(router.dispatch(Request('GET','/api/exploration/activity?task=5')).status,401)
        self.assertEqual(self.db.queries,[])
    def test_nonreentrant_guard_and_invalid_cursor(self):
        held=False
        @contextmanager
        def guard(scope):
            nonlocal held
            if held:raise AssertionError('nested')
            held=True
            try:yield
            finally:held=False
        self.store=TaskActivityReadStore(self.db,ActivityReadScope(5,9,'r1'),current=lambda s:True,lifecycle_guard=guard,authorize=lambda *a:True,raw_authorize=lambda *a:True,max_rows=2)
        self.service=TaskActivityService(self.store,authorize=lambda *a:True,stream_current=lambda *a:True)
        self.assertEqual(self.request('list','/api/exploration/activity?task=5').status,200)
        self.assertEqual(self.request('stream','/api/exploration/activity/stream?task=5',headers={'Last-Event-ID':'bad'}).status,400)

    def test_metadata_is_not_recursively_stripped_and_private_receipt_hidden_in_sql(self):
        from artex.db.task_activity_read import _PAYLOAD
        stripped,appended=_PAYLOAD.split(' ||',1)
        self.assertNotIn('a.metadata',stripped)
        self.assertNotIn('jsonb_strip_nulls',appended)
        self.assertIn("jsonb_typeof(a.metadata)='object'",appended)
        self.assertIn("a.metadata - '_artex_projection'",appended)
        self.assertIn('ELSE a.metadata END',appended)

    def test_stream_later_batch_selector_revalidated_for_buffered_rows(self):
        allowed=[True]
        self.store.raw_authorize=lambda scope,selection:selection.since==0 or allowed[0]
        response=self.request('stream','/api/exploration/activity/stream?task=5')
        stream=ClosingIterator(response)
        self.assertIn(b'private-1',next(stream));self.assertIn(b'private-2',next(stream))
        self.assertIn(b'private-3',next(stream))
        allowed[0]=False
        self.assertEqual(list(stream),[]);self.assertTrue(stream.failed)
        self.assertEqual(response.body.pending,[])
    def test_stream_later_batch_request_grant_checked_before_read(self):
        queries=[]
        self.service=TaskActivityService(self.store,authorize=lambda request,scope,selection:selection.since==0,stream_current=lambda *a:True)
        response=self.request('stream','/api/exploration/activity/stream?task=5')
        stream=ClosingIterator(response)
        self.assertIn(b'private-1',next(stream));self.assertIn(b'private-2',next(stream))
        count=len(self.db.queries)
        self.assertEqual(list(stream),[]);self.assertTrue(stream.failed)
        self.assertEqual(len(self.db.queries),count)
