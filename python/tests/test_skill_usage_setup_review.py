"""Independent optional usage overlay disclosure checks; no live server."""
import unittest
from contextlib import nullcontext
import test_skill_usage_setup as base
from artex.db.skill_usage_read import SkillUsageReader,UsageScope
from artex.server.skill_usage_read import SkillUsageReadService
from artex.server.skill_usage_setup import bind_skill_usage_reads
class Review(unittest.TestCase):
    def fixture(self):
        f=base.Tests();f.setUp();self.addCleanup(f.doCleanups);return f
    def test_last_source_current_revokes_outer_after_headers(self):
        f=self.fixture();started=[]
        def current(scope):
            if started:f.allowed=False
            return True
        reader=SkillUsageReader(f.u.db,UsageScope('w','r'),authorize_global=lambda *a:True,current=current,lifecycle_guard=lambda:nullcontext())
        service=SkillUsageReadService(reader,authorize=lambda *a:True)
        f.bundle=bind_skill_usage_reads(f.t.original,usage_service=service,authorize_global=lambda *a:f.allowed)
        with self.assertRaises(RuntimeError):f.request(start=lambda:started.append(True))
        self.assertEqual(len(f.u.db.calls),1)
    def test_original_selection_mutation_after_headers_refused(self):
        f=self.fixture()
        with self.assertRaises(RuntimeError):f.request(start=lambda:object.__setattr__(f.grants[0],'name','different'))
        self.assertEqual(len(f.u.db.calls),1)
    def test_reader_executor_swap_after_headers_refused(self):
        f=self.fixture()
        with self.assertRaises(RuntimeError):f.request(start=lambda:setattr(f.u.reader,'executor',object()))
    def test_original_request_mutation_after_headers_refused(self):
        f=self.fixture();requests=[]
        f.bundle=bind_skill_usage_reads(f.t.original,usage_service=f.u.service,authorize_global=lambda req,*a:requests.append(req) or True)
        with self.assertRaises(RuntimeError):f.request(start=lambda:object.__setattr__(requests[0],'target','/api/skills/other/usage'))
    def test_replaced_source_method_refused_before_fetch(self):
        f=self.fixture();f.u.reader.validate_disclosure=lambda *a,**kw:None
        self.assertNotEqual(f.request()[0],200);self.assertFalse(f.u.db.calls)
    def test_head_does_not_invent_success_or_extra_read(self):
        f=self.fixture();status,body=f.request(method='HEAD')[:2]
        self.assertEqual(status,400);self.assertFalse(f.u.db.calls)
if __name__=='__main__':unittest.main()
