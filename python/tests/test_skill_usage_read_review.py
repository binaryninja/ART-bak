"""Independent global usage disclosure contract; inert rows only."""
import unittest
from datetime import datetime,timezone,timedelta
from contextlib import nullcontext
from unittest.mock import patch
import test_skill_usage_read as base
from artex.db.skill_usage_read import SkillUsageReader,UsageScope
from artex.server.skill_usage_read import SkillUsageReadService
from artex.server.core import Request,json_response
class Review(unittest.TestCase):
    def fixture(self):
        f=base.Tests();f.setUp();self.addCleanup(f.doCleanups);return f
    def test_final_current_cannot_revoke_request_after_its_last_grant(self):
        f=self.fixture();rendered=[];checks=[]
        def current(scope):
            if rendered:
                checks.append(1)
                if len(checks)==4:f.allowed=False
            return True
        reader=SkillUsageReader(f.db,UsageScope('w','r'),authorize_global=lambda *a:True,current=current,lifecycle_guard=lambda:nullcontext())
        service=SkillUsageReadService(reader,authorize=lambda *a:f.allowed)
        def render(*args):
            response=json_response(*args);rendered.append(1);return response
        with patch('artex.server.skill_usage_read.json_response',render):
            response=service.handle(Request('GET','/api/skills/sample/usage'))
        self.assertEqual(response.status,403)
    def test_request_mutation_by_grant_refused_before_sql(self):
        f=self.fixture();request=Request('GET','/api/skills/sample/usage')
        def grant(req,*args):object.__setattr__(req,'target','/api/skills/other/usage');return True
        response=SkillUsageReadService(f.reader,authorize=grant).handle(request)
        self.assertNotEqual(response.status,200);self.assertEqual(f.db.calls,[])
    def test_ascending_rows_and_false_equivalent_overflow_refused(self):
        f=self.fixture();t=datetime(2026,1,1,tzinfo=timezone.utc)
        row=(t,'worker',0,'',0,False)
        for rows in ([row,(t+timedelta(seconds=1),*row[1:])],[(*row[:-1],0)]):
            f.db.rows=rows;self.assertEqual(f.get().status,500)
    def test_text_byte_bound_counts_utf8_not_characters(self):
        f=self.fixture();f.db.rows=[(datetime(2026,1,1,tzinfo=timezone.utc),'é'*32769,0,'',0,False)]
        self.assertEqual(f.get().status,500)
    def test_exact_encoded_selector_and_source_dimensions_only(self):
        f=self.fixture();f.db.rows=[(datetime(2026,1,1,tzinfo=timezone.utc),'',0,'chat',1,False)]
        response=f.get('limit=%2B2',name='%C3%A9')
        self.assertEqual(response.status,200);self.assertEqual(f.db.calls[0].params,('é',2,65536))
        self.assertEqual(set(response.json()['calls'][0]),{'ts','agent_key','task_id','session_id','args_len'})
        self.assertNotIn('args,',f.db.calls[0].sql)
if __name__=='__main__':unittest.main()
