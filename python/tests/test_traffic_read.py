import tempfile,unittest
from pathlib import Path
from contextlib import nullcontext
from artex.traffic import Traffic
from artex.server.traffic_read import TrafficReadService
from artex.server.core import Request,Router

class TrafficReadTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Traffic(self.temp.name);self.addCleanup(self.store.close)
        self.store.record(url='https://example.test/a/1',exchange_id='one',timestamp=1,req_head='GET /a/1 HTTP/1.1',resp_head='HTTP/1.1 200 OK',resp_body=b'answer')
        self.service=TrafficReadService(self.store,directory=self.temp.name,scope='whole-offline-workspace',authorize=lambda *a:True,current=lambda *a:True,lifecycle_guard=lambda *a:nullcontext())
    def call(self,action='',query=''):
        key=action or 'list';return self.service.handle(key,Request('GET','/api/traffic'+('/'+action if action else '')+query))
    def test_list_source_dto_and_count(self):
        r=self.call();self.assertEqual(r.status,200);data=r.json()
        self.assertEqual((data['count'],data['total'],data['enabled'],data['proxy']),(1,1,False,''))
        self.assertEqual(data['exchanges'][0]['ts'],'1970-01-01T00:00:01Z');self.assertNotIn('path',data['exchanges'][0])
    def test_actual_filter_and_hosts(self):
        self.assertEqual(self.call(query='?host=absent').json()['total'],0)
        self.assertEqual(self.call('hosts').json(),{'hosts':[{'host':'example.test','count':1}]})
    def test_detail_raw_requires_separate_authority(self):
        self.service.authorize=lambda r,a,*rest:a!='exchange'
        self.assertEqual(self.call('exchange','?id=one').status,403)
        self.service.authorize=lambda *a:True
        self.assertIn('answer',self.call('exchange','?id=one').json()['resp'])
    def test_unknown_and_legacy_and_blob_fail_closed(self):
        self.assertEqual(self.call('exchange','?id=missing').status,404)
        self.store.db.execute('DELETE FROM exchange_bodies');self.store.db.commit()
        self.assertEqual(self.call('exchange','?id=one').status,503)
        self.assertEqual(self.call('blob','?hash='+'a'*64).status,503)
    def test_no_silent_body_filter_widening(self):
        self.assertEqual(self.call(query='?body=ab').status,503)
        self.store.fts=False;self.assertEqual(self.call(query='?body=answer').status,503)
    def test_sql_text_byte_bound_before_page(self):
        self.service.max_bytes=1024
        self.store.db.execute("UPDATE exchanges SET url=?",('x'*2048,));self.store.db.commit()
        calls=[]
        self.store.page=lambda *a:calls.append(1) or ([],0)
        self.assertEqual(self.call().status,503);self.assertEqual(calls,[])
    def test_body_bound_before_get(self):
        self.service.max_bytes=1024
        self.store.db.execute('UPDATE exchange_bodies SET resp_body=?',(b'x'*2048,));self.store.db.commit()
        calls=[]
        self.store.get=lambda *a:calls.append(1) or ('','')
        self.assertEqual(self.call('exchange','?id=one').status,503);self.assertEqual(calls,[])
    def test_callback_rebind_and_stale_final_denied(self):
        old=self.store.directory
        def current(*a):self.store.directory=Path('/tmp');return True
        self.service.current=current
        self.assertEqual(self.call().status,403);self.store.directory=old
    def test_unknown_duplicate_and_archive_selectors_rejected(self):
        for query in ('?task=1','?host=a&host=b','?size=501','?page=-1'):
            with self.subTest(query=query):self.assertEqual(self.call(query=query).status,400)
    def test_readonly_routes_no_delete(self):
        r=Router(lambda req:True);self.service.register(r)
        self.assertEqual(r.dispatch(Request('DELETE','/api/traffic')).status,405)
