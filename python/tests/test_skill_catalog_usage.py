import json,unittest
from unittest.mock import patch
import test_skill_catalog_http as http
import test_skill_catalog_read as catalog
import test_successful_skill_stats as usage
from artex.skills.catalog_read import SkillCatalogReader,SkillCatalogUnavailable
from artex.server.skill_catalog_read import SkillCatalogReadService

class Tests(unittest.TestCase):
    def setUp(self):
        self.h=http.Tests();self.h.setUp();self.addCleanup(self.h.doCleanups)
        self.u=usage.Tests();self.u.setUp();self.addCleanup(self.u.doCleanups)
        self.ledger=self.u.make([(0,usage.summary('sample',2,tasks=1,agents=['worker']),False),(1,'{"skills":{"sample":3}}',False)])
        c=self.h.s
        self.reader=SkillCatalogReader(c.c.f.scope,c.source,authorize_global=lambda s:c.allowed,current=lambda s:c.current,
            lifecycle_guard=c.reader.guard,usage_reader=self.ledger)
        self.h.service=SkillCatalogReadService(self.reader,authorize=lambda *a:self.h.request_allowed)
        self.h.bundle=self.h.bind()
    def test_actual_decoration_and_single_query(self):
        snapshot=self.reader.read();row=json.loads(snapshot.payload)['skills'][0]
        self.assertEqual((row['calls'],row['tasks'],row['usage_agents']),(5,2,['worker']))
        self.assertEqual(len(self.u.db.calls),1)
        self.reader.validate_disclosure(snapshot);self.assertEqual(len(self.u.db.calls),1)
    def test_http_decoration(self):
        result=self.h.request();self.assertEqual(result[0],200);self.assertIn('worker',str(result))
    def test_ledger_denial_not_zero_fallback(self):
        self.u.allowed=False
        self.assertEqual(self.h.request()[0],403);self.assertFalse(self.u.db.calls)
    def test_unavailable_not_zero_fallback(self):
        self.u.db.rows=[(1,'{bad',False)]
        result=self.h.request();self.assertEqual(result[0],503);self.assertNotIn('"calls":0',str(result))
    def test_postheader_ledger_revocations(self):
        for attr in ('allowed','current'):
            with self.assertRaisesRegex(RuntimeError,'disclosure revoked'):
                self.h.request(start=lambda:setattr(self.u,attr,False))
            setattr(self.u,attr,True)
    def test_final_outer_revokes_ledger(self):
        self.assertEqual(self.h.request()[0],200);final=self.h.calls;self.h.calls=0
        def revoke():
            if self.h.calls==final:self.u.allowed=False
        self.h.hook=revoke
        with self.assertRaisesRegex(RuntimeError,'disclosure revoked'):self.h.request()
    def test_ledger_current_revokes_request(self):
        count=[0];self.u.hook=lambda:count.__setitem__(0,count[0]+1)
        self.assertEqual(self.h.request()[0],200);final=count[0];count[0]=0
        def revoke():
            count[0]+=1
            if count[0]==final:self.h.request_allowed=False
        self.u.hook=revoke
        with self.assertRaisesRegex(RuntimeError,'disclosure revoked'):self.h.request()
    def test_foreign_bound_ledger_receiver(self):
        other=usage.Tests();other.setUp();self.addCleanup(other.doCleanups);foreign=other.make()
        for name in ('read','validate','validate_disclosure','_allow'):
            with patch.object(self.ledger,name,getattr(foreign,name)):
                with self.assertRaises(SkillCatalogUnavailable):self.reader.read()
    def test_binding_replaced_before_and_after_headers(self):
        old=self.reader.usage_reader;self.reader.usage_reader=None
        self.assertEqual(self.h.request()[0],503);self.reader.usage_reader=old
        with self.assertRaisesRegex(RuntimeError,'disclosure revoked'):
            self.h.request(start=lambda:setattr(self.reader,'usage_reader',None))

if __name__=='__main__':unittest.main()
