import json,unittest
from contextlib import nullcontext
from unittest.mock import patch
from artex.db.missing_skill_stats import MissingSkillStatsReader
from artex.db.skill_usage_read import UsageScope
from artex.server.core import Request,json_response
from artex.server.missing_skill_read import MissingSkillReadService,parse_request
from test_missing_skill_stats import Executor,summary

class Tests(unittest.TestCase):
    def setUp(self):
        self.db=Executor([(0,summary('private-skill',2),False)])
        self.allowed=self.global_allowed=self.current=True;self.selections=[]
        self.current_calls=0;self.current_hook=lambda:None
        def current(scope):
            self.current_calls+=1;self.current_hook();return self.current
        self.reader=MissingSkillStatsReader(self.db,UsageScope('workspace','revision'),authorize_global=lambda s,q:self.global_allowed,current=current,lifecycle_guard=nullcontext)
        self.service=MissingSkillReadService(self.reader,authorize=lambda r,s,q:self.selections.append(q) or self.allowed)
    def request(self,target='/api/skills/missing',**kwargs):return Request('GET',target,{},**kwargs)
    def test_defaults_and_clamps(self):
        for query,expected in (('',20),('?limit=1',1),('?limit=200',200),('?limit=201',20),('?limit=0',20),('?limit=no',20),('?limit=-1',20)):
            self.assertEqual(parse_request(self.request('/api/skills/missing'+query)).limit,expected)
    def test_success_same_selection(self):
        result=self.service.handle(self.request());self.assertEqual(result.status,200)
        self.assertEqual(result.json()['missing'][0]['skill'],'private-skill')
        self.assertEqual(len({id(q) for q in self.selections}),1)
    def test_invalid_targets_and_body(self):
        for target in ('/api/skills/missing?x=1','/api/skills/missing?limit=1&limit=2','/api/skills/missing#frag','https://example.com/api/skills/missing','/api/skills/missing?limit=%FF'):
            self.assertEqual(self.service.handle(self.request(target)).status,400)
        self.assertEqual(self.service.handle(self.request(body=b'{}')).status,400)
        self.assertFalse(self.db.calls)
    def test_independent_grants(self):
        for name in ('allowed','global_allowed','current'):
            setattr(self,name,False)
            self.assertEqual(self.service.handle(self.request()).status,403)
            setattr(self,name,True)
        self.assertFalse(self.db.calls)
    def test_postfetch_and_serialization_revocation(self):
        self.db.hook=lambda:setattr(self,'allowed',False)
        result=self.service.handle(self.request());self.assertNotEqual(result.status,200);self.assertNotIn(b'private-skill',result.body)
        self.allowed=True;self.db.hook=lambda:None
        def revoked(status,value):
            response=json_response(status,value)
            if status==200:self.global_allowed=False
            return response
        with patch('artex.server.missing_skill_read.json_response',revoked):
            result=self.service.handle(self.request())
        self.assertEqual(result.status,403);self.assertNotIn(b'private-skill',result.body)
    def test_request_mutation(self):
        req=self.request();self.db.hook=lambda:req.headers.update({'x':'changed'})
        self.assertNotEqual(self.service.handle(req).status,200)
    def test_backend_errors_hide_details(self):
        def fail():raise RuntimeError('private-backend-detail')
        self.db.hook=fail
        result=self.service.handle(self.request());self.assertEqual(result.status,500)
        self.assertNotIn(b'private-backend-detail',result.body)

if __name__=='__main__':unittest.main()
