import unittest,json
from datetime import datetime
from zoneinfo import ZoneInfo
from contextlib import nullcontext,contextmanager
from unittest.mock import patch
from artex.db.notification_history import NotificationHistoryReader
from artex.server.notification_history import NotificationHistoryReadService
from artex.server.core import Request,json_response
NOW=datetime(2026,1,2,tzinfo=ZoneInfo('UTC'))
def row(snapshot=None):return (3,9,9223372036854775807,'finding_created',2,'channel','webhook','failed',3,'diagnostic',None,NOW,None,NOW,json.dumps(snapshot or {'name':'title','severity':'high'}))
class DB:
    def __init__(self):self.rows=[(0,)+(None,)*14];self.calls=[];self.hook=None
    def fetch_all(self,q):
        self.calls.append(q)
        if self.hook:self.hook()
        return self.rows
class Tests(unittest.TestCase):
    def setUp(self):
        self.db=DB();self.allowed=True;self.raw=True;self.reader=NotificationHistoryReader(self.db);self.selections=[]
        self.service=NotificationHistoryReadService(self.reader,authorize=lambda r,s:self.selections.append(s) or self.allowed,raw_authorize=lambda r,s:self.raw,current=lambda:True,lifecycle_guard=lambda:nullcontext())
    def get(self,q=''):return self.service.handle(Request('GET','/api/notify/deliveries'+('?' +q if q else '')))
    def test_empty_and_source_dto_no_credentials(self):
        self.assertEqual(self.db.calls,[]);self.assertEqual(self.get().json(),dict(deliveries=[],total=0,page=1,page_size=50))
        self.db.rows=[row()];r=self.get();self.assertEqual(r.status,200);d=r.json()['deliveries'][0]
        self.assertEqual(d['finding_id'],'9223372036854775807');self.assertEqual(d['title'],'title');self.assertNotIn('sent_at',d);self.assertNotIn('batch_id',d);self.assertNotIn('snapshot',d)
        self.assertEqual(d['created_at'],'2026-01-02T00:00:00Z');self.assertNotIn('c.config',self.db.calls[-1].sql)
    def test_snapshot_title_fallback_and_source_filters(self):
        self.db.rows=[row({'vulnclass':'class','severity':'medium'})];self.assertEqual(self.get('state=failed&channel_id=2&event_kind=finding_created').json()['deliveries'][0]['title'],'class')
        self.assertEqual(self.db.calls[-1].params[:3],(2,'failed','finding_created'))
        self.assertIn('SELECT count(*) FROM filtered',self.db.calls[-1].sql);self.assertIn('JOIN notification_channels',self.db.calls[-1].sql)
    def test_source_pagination_echo_effective_cap_and_global_selector(self):
        self.get('page=2&page_size=201&channel_id=-1');self.assertEqual(self.db.calls[-1].params,(-1,'','',50,50,1048576));self.assertEqual(self.get('page_size=201').json()['page_size'],201)
        self.get('page=-1&page_size=bad');self.assertEqual(self.db.calls[-1].params[3:5],(50,0))
        self.assertEqual(self.get('page=1000000').status,400);self.assertEqual(self.get('state=x&state=y').status,400)
    def test_missing_channel_count_can_exceed_page(self):
        self.db.rows=[(3,)+(None,)*14];self.assertEqual(self.get().json()['total'],3);self.assertEqual(self.get().json()['deliveries'],[])
    def test_raw_grants_and_post_sql_revoke(self):
        self.raw=False;self.assertEqual(self.get().status,403);self.assertEqual(self.db.calls,[])
        self.raw=True;self.db.hook=lambda:setattr(self,'raw',False);self.assertEqual(self.get().status,403)
    def test_serialization_guard_and_callback_pins(self):
        def encode(*args):self.allowed=False;return json_response(*args)
        with patch('artex.server.notification_history.json_response',side_effect=encode):self.assertEqual(self.get().status,403)
        self.allowed=True
        @contextmanager
        def guard():
            yield
            self.raw=False
        self.service=NotificationHistoryReadService(self.reader,authorize=lambda r,s:self.allowed,raw_authorize=lambda r,s:self.raw,current=lambda:True,lifecycle_guard=guard)
        self.assertEqual(self.get().status,403)
        self.setUp();self.db.hook=lambda:setattr(self.service,'raw_authorize',lambda *a:self.fail('replacement called'));self.assertEqual(self.get().status,500)
    def test_bound_sentinel_bad_types_and_no_mutation(self):
        self.db.rows=[row()];r=list(row());r[-1]=None;self.db.rows=[tuple(r)];self.assertEqual(self.get().status,500)
        r=list(row());r[1]=True;self.db.rows=[tuple(r)];self.assertEqual(self.get().status,500)
        self.assertEqual(self.service.handle(Request('POST','/api/notify/deliveries')).status,400)
