"""Independent resolved metadata only; never invoke the stored judge prompt."""
import unittest
from artex.db.judge_config_read import KEYS,integer
import test_judge_config_read as fixture

class JudgeReview(unittest.TestCase):
    def fixture(self):
        f=fixture.Tests();f.setUp();self.addCleanup(f.doCleanups);return f
    def test_int64_boundaries_long_zeroes_and_go_whitespace(self):
        for value,want in (('-9223372036854775808',-(2**63)),('9223372036854775807',2**63-1),('9223372036854775808',17),('-9223372036854775809',17),('+'+'0'*10000+'5',5),('\x1c5',17),('\u0085-9\u3000',-9),('１２',17)):
            with self.subTest(value=value[:40]):self.assertEqual(integer(value,17),want)
    def test_multibyte_prompt_over_bound_is_not_disclosed(self):
        f=self.fixture();f.rows=[(KEYS[2],'é'*(f.reader.max_bytes//2+1))]
        response=f.service.handle(f.request)
        self.assertEqual(response.status,500);self.assertNotIn(b'\\u00e9',response.body)
        self.assertIn('SUM(octet_length(value)) OVER ()',f.queries[0].sql)
        self.assertNotIn('llm_profiles',f.queries[0].sql)
    def test_original_raw_callback_swap_after_fetch_is_denied(self):
        f=self.fixture();f.rows=[(KEYS[2],'private prompt')]
        f.after=lambda:setattr(f.service,'raw_authorize',lambda *a:True)
        response=f.service.handle(f.request)
        self.assertEqual(response.status,500);self.assertNotIn(b'private prompt',response.body)
    def test_final_guard_exit_raw_revocation_withholds_custom_prompt(self):
        f=self.fixture();f.rows=[(KEYS[2],'private prompt')]
        f.after_guard=lambda:setattr(f,'raw',False)
        response=f.service.handle(f.request)
        self.assertEqual(response.status,403);self.assertNotIn(b'private prompt',response.body)
    def test_raw_grant_cannot_mask_revoked_global_read(self):
        from artex.server.judge_config_read import JudgeConfigReadService
        f=self.fixture();phase={'after_fetch':False}
        f.after=lambda:phase.update(after_fetch=True)
        def raw(request):
            if phase['after_fetch']:f.allowed=False
            return True
        f.service=JudgeConfigReadService(f.reader,authorize=lambda r:f.allowed,raw_authorize=raw,current=lambda:True,lifecycle_guard=f.service.guard)
        response=f.service.handle(f.request)
        self.assertEqual(response.status,403)

if __name__=='__main__':unittest.main()
