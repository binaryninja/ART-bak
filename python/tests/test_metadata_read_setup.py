"""Typed optional metering/history disclosure; real JWT, inert bounded SQL."""
import io,json,unittest
from contextlib import nullcontext
from dataclasses import replace
from urllib.parse import urlsplit
from unittest.mock import patch
from artex.server.auth import sign_jwt,AuthService
from artex.server.wsgi import WSGIApplication
from artex.server.core import Request
from artex.server.intercept_read_setup import bind_metadata_reads,bind_intercept_reads,InterceptReadBundle
from artex.server.judge_usage_read import JudgeUsageReadService
from artex.server.notification_history import NotificationHistoryReadService
from artex.db.judge_usage_read import JudgeUsageReader
from artex.db.notification_history import NotificationHistoryReader,DeliverySelection
import test_intercept_read_setup as fixture
from test_notification_history import row
from test_application import TestCodec

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        old=self.f.db.fetch_all
        def fetch(q):
            if 'llm_usage' in q.sql:
                self.f.db.calls.append(q);return [(3,4,5,0,0,None,None,None,None)]
            if 'notification_deliveries' in q.sql:
                self.f.db.calls.append(q);return [row()]
            return old(q)
        self.f.db.fetch_all=fetch
        self.allowed=True;self.raw=True;self.current=True;self.selections=[];self.days=[]
        self.judge=JudgeUsageReadService(JudgeUsageReader(self.f.db),authorize=lambda r,d:self.days.append(d) or self.allowed,
            current=lambda:self.current,lifecycle_guard=lambda:nullcontext())
        self.history=NotificationHistoryReadService(NotificationHistoryReader(self.f.db),authorize=lambda r,s:self.selections.append(s) or self.allowed,
            raw_authorize=lambda r,s:self.raw,current=lambda:self.current,lifecycle_guard=lambda:nullcontext())
        self.bundle=self.bind()
    def bind(self,**kw):
        args=dict(judge_usage_service=self.judge,notification_history_service=self.history,authorize_global=lambda r,k:self.allowed)
        args.update(kw)
        return bind_metadata_reads(self.f.original,**args)
    def request(self,target,*,start=None,token=True,method='GET'):
        url=urlsplit(target);status=[]
        env={'REQUEST_METHOD':method,'PATH_INFO':url.path,'QUERY_STRING':url.query,'CONTENT_LENGTH':'0','wsgi.input':io.BytesIO()}
        if token:env['HTTP_AUTHORIZATION']='Bearer '+sign_jwt(self.f.key,now=1000.)
        def response(s,h):
            status.append(s)
            if start:start()
        body=b''.join(self.bundle.application(env,response))
        return int(status[0].split()[0]),body
    def test_only_supplied_routes_inert_original_unmodified(self):
        self.assertEqual(self.f.db.calls,[]);self.assertEqual(self.days,[])
        self.assertEqual(self.request('/api/intercept/tool-config')[0],404)
        self.assertEqual(self.request('/api/intercept/judge/usage')[0],200)
        self.assertEqual(self.request('/api/notify/deliveries')[0],200)
        self.assertEqual({p for m,p,*rest in self.bundle.application._router._routes},
                         {'/api/intercept/judge/usage','/api/notify/deliveries'})
    def test_jwt_no_grant_or_sql_and_methods_remain_readonly(self):
        self.assertEqual(self.request('/api/notify/deliveries',token=False)[0],401)
        self.assertEqual(self.selections,[]);self.assertEqual(self.f.db.calls,[])
        self.assertEqual(self.request('/api/notify/deliveries',method='POST')[0],405)
        self.assertEqual(self.request('/api/notify/deliveries',method='HEAD'),(400,b''))
    def test_normalized_days_reused_at_every_grant(self):
        self.assertEqual(self.request('/api/intercept/judge/usage?days=366')[0],200)
        self.assertTrue(self.days);self.assertEqual(set(self.days),{366})
        self.assertTrue(all(value is self.days[0] for value in self.days))
        self.days.clear()
        self.assertEqual(self.request('/api/intercept/judge/usage?days=bad')[0],200)
        self.assertEqual(set(self.days),{30})
    def test_history_exact_selection_reused_and_effective_limit_preserved(self):
        status,body=self.request('/api/notify/deliveries?channel_id=2&state=failed&event_kind=finding_created&page=2&page_size=201')
        self.assertEqual(status,200);self.assertEqual(json.loads(body)['page_size'],201)
        selected=self.selections[0]
        self.assertEqual(selected,DeliverySelection(2,'failed','finding_created',2,201))
        self.assertTrue(all(s is selected for s in self.selections))
        self.assertEqual(self.f.db.calls[-1].params[3:5],(50,50))
    def test_raw_denial_never_reads_or_discloses(self):
        self.raw=False
        status,body=self.request('/api/notify/deliveries')
        self.assertEqual(status,403);self.assertNotIn(b'diagnostic',body);self.assertEqual(self.f.db.calls,[])
    def test_last_raw_callback_revokes_read_blocks_post_start_body(self):
        started=[False]
        def raw(r,s):
            if started[0]:self.allowed=False
            return True
        self.history=NotificationHistoryReadService(self.history.reader,authorize=lambda r,s:self.allowed,raw_authorize=raw,
            current=lambda:self.current,lifecycle_guard=lambda:nullcontext())
        self.bundle=self.bind()
        with self.assertRaises(RuntimeError):self.request('/api/notify/deliveries',start=lambda:started.__setitem__(0,True))
    def test_last_jwt_callback_revokes_raw_blocks_post_start_body(self):
        state={'started':False,'calls':0}
        def clock():
            if state['started']:
                state['calls']+=1
                if state['calls']==2:self.raw=False
            return 1000.
        self.f.auth=AuthService(object(),TestCodec(),self.f.key,clock=clock)
        self.f.original=WSGIApplication(self.f.auth.router());self.bundle=self.bind()
        with self.assertRaises(RuntimeError):self.request('/api/notify/deliveries',start=lambda:state.update(started=True))
    def test_parser_replacement_refused_without_invoking_replacement(self):
        with patch('artex.server.notification_history.parse_request',side_effect=AssertionError('replacement invoked')):
            status,body=self.request('/api/notify/deliveries')
        self.assertEqual(status,503);self.assertEqual(self.f.db.calls,[])
    def test_invalid_selectors_rejected_before_sql_and_injected_selection_no_bypass(self):
        for target in ('/api/intercept/judge/usage?days=1&days=2','/api/notify/deliveries?state=x&state=y'):
            self.assertEqual(self.request(target)[0],400)
        response=self.history.handle(Request('GET','/api/notify/deliveries?channel_id=2'),selection=DeliverySelection(3))
        self.assertEqual(response.status,400)
        self.assertEqual(self.judge.handle(Request('GET','/api/intercept/judge/usage?days=2'),selection=True).status,400)
        self.assertEqual(self.f.db.calls,[])
    def test_atomic_conflict_only_for_supplied_routes(self):
        before=tuple(self.f.original.router._routes)
        self.f.original.router.add('GET','/api/notify/{name}',lambda r,p:None)
        routes=tuple(self.f.original.router._routes)
        with self.assertRaises(ValueError):self.bind()
        self.assertEqual(tuple(self.f.original.router._routes),routes);self.assertEqual(self.f.db.calls,[])
        self.assertEqual(len(routes),len(before)+1)
    def test_legacy_signature_return_and_new_empty_or_foreign_types(self):
        old=bind_intercept_reads(self.f.original,config_service=self.f.config,rules_service=self.f.rules,authorize_global=lambda *a:True)
        self.assertIs(type(old),InterceptReadBundle)
        with self.assertRaises(ValueError):bind_metadata_reads(self.f.original,authorize_global=lambda *a:True)
        with self.assertRaises(ValueError):self.bind(judge_usage_service=object())
        other=JudgeUsageReadService(JudgeUsageReader(fixture.DB()),authorize=lambda *a:True,current=lambda:True,lifecycle_guard=lambda:nullcontext())
        with self.assertRaises(ValueError):self.bind(judge_usage_service=other)
    def test_supplied_boolean_history_fields_rejected_before_grants(self):
        for selection,query in ((DeliverySelection(channel_id=True),'channel_id=1'),
                                (DeliverySelection(page=True),'page=1'),
                                (DeliverySelection(page_size=True),'page_size=1')):
            self.selections.clear();self.f.db.calls.clear()
            request=Request('GET','/api/notify/deliveries?'+query)
            result=self.history.handle(request,selection=selection)
            self.assertEqual(result.status,400)
            self.assertEqual(self.selections,[]);self.assertEqual(self.f.db.calls,[])
