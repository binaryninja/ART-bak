"""Inert scoped evidence metadata, no files/proxy/traffic capture."""
import unittest
from contextlib import contextmanager,nullcontext
from datetime import datetime,timezone
from unittest.mock import patch
from artex.db.finding_traffic_read import *
from artex.server.finding_traffic_read import FindingTrafficReadService
from artex.server.core import Request
NOW=datetime(2026,1,1,tzinfo=timezone.utc)
def binding():return (3,11,'a'*64,'proof','note',0,NOW,'a'*64,'traffic1',1700000000,'https://fixture.invalid','GET',200,'text/plain','b'*64,'c'*64,0,10,NOW,512)
class DB:
    def __init__(self):self.calls=[];self.rows=[binding()];self.parent=[(11,2,1)];self.hook=lambda q:None
    @contextmanager
    def transaction(self):
        self.calls.append('begin');yield self;self.calls.append('commit')
    def fetch_all(self,q):
        self.calls.append(q)
        result=[(None,)] if 'pg_advisory' in q.sql else self.parent if q.sql.startswith('SELECT f.id') else self.rows
        self.hook(q);return result
class Tests(unittest.TestCase):
    def setUp(self):
        self.db=DB();self.allowed=True;self.request_allowed=True;self.current=True
        self.scope=FindingTrafficScope(5,9,11,'r1')
        self.reader=FindingTrafficReader(self.db,self.scope,authorize=lambda s:self.allowed,current=lambda s:self.current,lifecycle_guard=lambda s:nullcontext())
        self.service=FindingTrafficReadService(self.reader,authorize=lambda r,s:self.request_allowed)
    def request(self,target='/api/exploration/findings/11/traffic'):
        return self.service.handle(Request('GET',target))
    def test_bound_task_standalone_finding_list_and_explicit_projection(self):
        response=self.request();self.assertEqual(response.status,200);value=response.json()
        self.assertEqual((value['finding_id'],value['version'],value['report_version']),('11',2,1))
        self.assertEqual(value['bindings'][0]['id'],'3');self.assertNotIn('req_head',value['bindings'][0]['snapshot'])
        queries=[q for q in self.db.calls if isinstance(q,Query)]
        self.assertEqual(queries[0].params,(7337741004,));self.assertEqual(queries[1].params,(11,5,9))
        self.assertIn("node.kind='finding'",queries[1].sql);self.assertIn('FOR SHARE OF f,t,node',queries[1].sql)
        for q in queries:
            for secret in ('req_head','resp_head','to_jsonb','req_body','resp_body'):self.assertNotIn(secret,q.sql)
        self.assertEqual(queries[2].params,(11,2097152,1001));self.assertIn('ORDER BY position,id',queries[2].sql)
    def test_empty_normal_and_scoped_missing(self):
        self.db.rows=[];self.assertEqual(self.request().json()['bindings'],[])
        self.db.parent=[];self.assertEqual(self.request().status,404)
    def test_wrong_or_ambiguous_selector_no_sql(self):
        for target in ('/api/exploration/findings/9/traffic','/api/exploration/findings/11/traffic?context_task=6','/api/exploration/findings/11/traffic?context_task=5&context_task=5','/api/exploration/findings/11/traffic?download=1'):
            self.assertEqual(self.request(target).status,400)
        self.assertEqual(self.db.calls,[])
    def test_revocation_after_advisory_or_fetch_prevents_disclosure(self):
        self.db.hook=lambda q:setattr(self,'allowed',False)
        self.assertEqual(self.request().status,403)
        self.assertEqual(len([q for q in self.db.calls if isinstance(q,Query)]),1)
    def test_hash_reference_bounds_and_type_drift_refuse(self):
        for index,value in ((2,'A'*64),(7,'d'*64),(14,'bad'),(16,True),(19,2097153),(5,True)):
            row=list(binding());row[index]=value;self.db.rows=[tuple(row)]
            self.assertEqual(self.request().status,503)
        self.db.rows=[binding(),binding()];self.assertEqual(self.request().status,503)
    def test_parent_identity_and_original_scope_types(self):
        self.db.parent=[(True,2,1)];self.assertEqual(self.request().status,503)
        self.db.parent=[(11,2,1)];object.__setattr__(self.scope,'exploration_id',9.0)
        self.assertEqual(self.request().status,403)
    def test_serialization_revocation_blocks_body(self):
        from artex.server import finding_traffic_read as module
        original=module.json_response
        def render(*args):
            result=original(*args);self.request_allowed=False;return result
        with patch.object(module,'json_response',render):response=self.request()
        self.assertEqual(response.status,403);self.assertNotIn(b'traffic1',response.body)
    def test_final_current_revokes_metadata_grant_before_query(self):
        count=[]
        def current(s):count.append(1);self.allowed=False;return True
        reader=FindingTrafficReader(self.db,self.scope,authorize=lambda s:self.allowed,current=current,lifecycle_guard=lambda s:nullcontext())
        service=FindingTrafficReadService(reader,authorize=lambda *a:True)
        self.assertEqual(service.handle(Request('GET','/api/exploration/findings/11/traffic')).status,403)
        self.assertEqual(self.db.calls,[])
