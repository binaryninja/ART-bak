"""Independent judge read composition and AST whitelist; inert callbacks only."""
import unittest
from artex.server.intercept_read_setup import bind_metadata_reads
from artex.server.judge_config_read import JudgeConfigReadService
from artex.server.auth import AuthService
from artex.server.wsgi import WSGIApplication
from test_application import TestCodec
import test_metadata_judge_config_setup as fixture
import test_route_inventory_detail as scanner

class JudgeCompositionReview(unittest.TestCase):
    def fixture(self):
        f=fixture.Tests();f.setUp();self.addCleanup(f.doCleanups);return f
    def test_last_original_jwt_revokes_raw_prompt_grant(self):
        f=self.fixture();state={'started':False,'calls':0}
        def clock():
            if state['started']:
                state['calls']+=1
                if state['calls']==2:f.j.raw=False
            return 1000.
        original=WSGIApplication(AuthService(object(),TestCodec(),f.t.f.key,clock=clock).router())
        f.t.bundle=bind_metadata_reads(original,judge_config_service=f.j.service,authorize_global=lambda *a:True)
        with self.assertRaises(RuntimeError):f.t.request('/api/intercept/judge',start=lambda:state.update(started=True))
        self.assertEqual(len(f.j.queries),1)
    def test_last_raw_callback_revokes_current_without_body(self):
        f=self.fixture();state={'started':False,'current':True}
        def raw(request):
            if state['started']:state['current']=False
            return True
        service=JudgeConfigReadService(f.j.reader,authorize=lambda *a:True,raw_authorize=raw,current=lambda:state['current'],lifecycle_guard=f.j.service.guard)
        f.t.bundle=bind_metadata_reads(f.t.f.original,judge_config_service=service,authorize_global=lambda *a:True)
        with self.assertRaises(RuntimeError):f.t.request('/api/intercept/judge',start=lambda:state.update(started=True))
    def test_head_mixed_prefix_conflict_is_atomic_no_grants(self):
        f=self.fixture();calls=[]
        f.t.f.original.router.add('HEAD','/a{rest}/intercept/{kind}',lambda *a:None)
        before=tuple(f.t.f.original.router._routes)
        with self.assertRaises(ValueError):bind_metadata_reads(f.t.f.original,judge_config_service=f.j.service,authorize_global=lambda *a:calls.append(a) or True)
        self.assertEqual(tuple(f.t.f.original.router._routes),before);self.assertFalse(calls);self.assertFalse(f.j.queries)
    def test_scanner_entry_does_not_admit_other_call_shapes(self):
        cases=["if 'judge_config' in services:staged.add('GET','/api/intercept/judge',handler,public=True)","if 'judge_config' in services:staged.add('GET','/api/intercept/judge',handler,False)","if 'judge_config' not in services:staged.add('GET','/api/intercept/judge',handler)"]
        for text in cases:
            with self.subTest(text=text),self.assertRaises(scanner.fixture.audit.AuditError):scanner.Tests().scan('        '+text)

if __name__=='__main__':unittest.main()
