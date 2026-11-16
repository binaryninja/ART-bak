"""Direct overlay regressions, independent of the combined dispatcher."""
import unittest
import test_intercept_read_setup as transport
import test_skill_usage_read as usage
import test_missing_skill_read as missing
from artex.server.skill_usage_setup import bind_skill_usage_reads
from artex.server.missing_skill_setup import bind_missing_skill_reads

class Tests(unittest.TestCase):
    def check(self,source,binder,keyword,path):
        t=transport.Tests();t.setUp();self.addCleanup(t.doCleanups)
        s=source.Tests();s.setUp();self.addCleanup(s.doCleanups)
        now=[1000.];t.auth.clock=lambda:now[0];count=[0];last=[None]
        def grant(*args):
            count[0]+=1
            if count[0]==last[0]:now[0]=1000000.
            return True
        bundle=binder(t.original,**{keyword:s.service},authorize_global=grant)
        self.assertEqual(t.request(path,application=bundle.application)[0],200)
        last[0]=count[0];count[0]=0
        with self.assertRaisesRegex(RuntimeError,'disclosure revoked'):
            t.request(path,application=bundle.application)
    def test_usage_final_outer_clock_expiry(self):
        self.check(usage,bind_skill_usage_reads,'usage_service','/api/skills/sample/usage')
    def test_missing_final_outer_clock_expiry(self):
        self.check(missing,bind_missing_skill_reads,'missing_service','/api/skills/missing')

if __name__=='__main__':unittest.main()
