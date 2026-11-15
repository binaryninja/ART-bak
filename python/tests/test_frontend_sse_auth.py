"""Actual query-JWT authentication with the frontend EventSource URL shape."""
import io
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import urlencode
from artex.server.auth import AuthService, sign_jwt
from artex.server.logs import LogRing, LogService, parse_log
from artex.server.sidequestions import SideQuestionHTTPService
from artex.server.wsgi import WSGIApplication
from artex.db.sidequestions import Parent, Exchange, Model


class FrontendSSEAuthTests(unittest.TestCase):
    def setUp(self):
        key=b'public-fixture-not-a-user-key----'
        self.token=sign_jwt(key,now=1000)
        self.router=AuthService(None,None,key,clock=lambda:1001).router()
        self.ring=LogRing();self.ring.append(parse_log('fixture'))
        LogService(ring=self.ring).register(self.router)
        parent=Parent(conversation_id=1);now=datetime(2026,11,15,tzinfo=timezone.utc)
        exchange=Exchange('req-1',1,parent.key(),1,'client','question','answer','completed','',Model(),now,now,1)
        self.lookups=[]
        store=SimpleNamespace(parent=parent,key=parent.key(),request=lambda _:exchange)
        def resolve(key):self.lookups.append(key);return store
        SideQuestionHTTPService(request_resolver=resolve).register(self.router)
        self.app=WSGIApplication(self.router)

    def open(self,path,query):
        headers=[]
        result=self.app({'REQUEST_METHOD':'GET','PATH_INFO':path,'QUERY_STRING':query,
                         'CONTENT_LENGTH':'0','wsgi.input':io.BytesIO()},lambda status,values:headers.append((status,values)))
        return int(headers[0][0].split()[0]),result

    def test_log_eventsource_query_token_and_cursor(self):
        status,body=self.open('/api/logs/stream',urlencode({'token':self.token,'since':0}))
        self.assertEqual(status,200)
        try:
            chunk=next(iter(body));self.assertIn(b'fixture',chunk);self.assertNotIn(self.token.encode(),chunk)
        finally:body.close()
        self.assertEqual(self.ring.subscriber_count,0)

    def test_side_eventsource_query_token(self):
        status,body=self.open('/api/side-questions/req-1/events',urlencode({'token':self.token}))
        self.assertEqual(status,200)
        try:
            data=b''.join(body);self.assertIn(b'event: snapshot',data);self.assertNotIn(self.token.encode(),data)
        finally:body.close()

    def test_invalid_token_rejected_before_subscription_or_resolution(self):
        for path in ('/api/logs/stream','/api/side-questions/req-1/events'):
            status,body=self.open(path,'token=invalid');self.assertEqual(status,401);list(body)
        self.assertEqual(self.ring.subscriber_count,0);self.assertEqual(self.lookups,[])

    def test_duplicate_token_and_unknown_parameters_still_rejected(self):
        for path in ('/api/logs/stream','/api/side-questions/req-1/events'):
            for extra in ('&token='+self.token,'&unexpected=1'):
                status,body=self.open(path,urlencode({'token':self.token})+extra)
                self.assertEqual(status,400);list(body)
        self.assertEqual(self.ring.subscriber_count,0);self.assertEqual(self.lookups,[])
