import unittest,json,re
from pathlib import Path
from contextlib import contextmanager
from unittest.mock import patch
from artex.db.judge_config_read import JudgeConfigReader,DEFAULT_JUDGE_PROMPT,KEYS
from artex.server.judge_config_read import JudgeConfigReadService
from artex.server.core import Request,json_response

class Tests(unittest.TestCase):
    def setUp(self):
        self.rows=[];self.queries=[];self.allowed=True;self.raw=True;self.after=None;self.after_guard=None
        class DB:
            def fetch_all(db,q):
                self.queries.append(q)
                if self.after:self.after()
                return self.rows
        self.reader=JudgeConfigReader(DB())
        @contextmanager
        def guard():
            yield
            if self.after_guard:self.after_guard()
        self.service=JudgeConfigReadService(self.reader,authorize=lambda r:self.allowed,raw_authorize=lambda r:self.raw,current=lambda:True,lifecycle_guard=guard)
        self.request=Request('GET','/api/intercept/judge')
    def test_source_default_prompt_exact_data(self):
        source=(Path(__file__).resolve().parents[2]/'intercept/prompt.go').read_text()
        def extract(name):
            block=source.split('const '+name+' = ',1)[1].split('\n\n',1)[0]
            return ''.join(json.loads(x) for x in re.findall(r'"(?:[^"\\]|\\.)*"',block))
        self.assertEqual(DEFAULT_JUDGE_PROMPT,extract('DefaultJudgePrompt')+extract('JudgeOutputContract'))
        self.assertNotIn('# Review input boundary',DEFAULT_JUDGE_PROMPT)
    def test_missing_defaults_no_credentials_and_no_runtime(self):
        r=self.service.handle(self.request);self.assertEqual(r.status,200)
        self.assertEqual(r.json(),dict(enabled=False,profile_id=0,prompt=DEFAULT_JUDGE_PROMPT,timeout_seconds=15,fail_action='allow',ask_timeout_seconds=300,ask_timeout_action='deny'))
        self.assertEqual(self.queries[0].params[0],list(KEYS));self.assertIn('CASE WHEN bytes<=',self.queries[0].sql)
    def test_resolved_source_values(self):
        self.rows=list(zip(KEYS,('1',' -3 ',' custom prompt \n','+25',' ask ','0','allow')))
        data=self.service.handle(self.request).json()
        self.assertEqual(data['prompt'],' custom prompt \n');self.assertTrue(data['enabled']);self.assertEqual(data['profile_id'],-3)
        self.assertEqual(data['timeout_seconds'],25);self.assertEqual(data['ask_timeout_seconds'],300);self.assertEqual(data['fail_action'],'ask')
    def test_invalid_bool_numbers_choice_whitespace_fallback(self):
        self.rows=list(zip(KEYS,(' TRUE ','9223372036854775808','\u2000\t','-1','ALLOW','\x1c2','ask')))
        data=self.service.handle(self.request).json()
        self.assertFalse(data['enabled']);self.assertEqual(data['profile_id'],0);self.assertEqual(data['timeout_seconds'],15)
        self.assertEqual(data['ask_timeout_seconds'],300);self.assertEqual(data['fail_action'],'allow');self.assertEqual(data['ask_timeout_action'],'deny');self.assertEqual(data['prompt'],DEFAULT_JUDGE_PROMPT)
    def test_overflow_duplicate_unknown_and_sql_error_fail_closed(self):
        for rows in ([(KEYS[2],None)],[(KEYS[0],'1')]*2,[('auth.password_hash','secret')]):
            self.rows=rows;self.assertEqual(self.service.handle(self.request).status,500)
        def fail():raise RuntimeError('secret')
        self.after=fail;r=self.service.handle(self.request);self.assertEqual(r.status,500);self.assertNotIn(b'secret',r.body)
    def test_grants_before_and_after_guard_and_serialization(self):
        self.raw=False;self.assertEqual(self.service.handle(self.request).status,403);self.assertFalse(self.queries)
        self.raw=True;self.after_guard=lambda:setattr(self,'allowed',False)
        self.assertEqual(self.service.handle(self.request).status,403)
        self.after_guard=None;self.allowed=True
        def encode(*args):
            result=json_response(*args);self.raw=False;return result
        with patch('artex.server.judge_config_read.json_response',encode):self.assertEqual(self.service.handle(self.request).status,403)
    def test_unknown_query_and_binding_swap(self):
        self.assertEqual(self.service.handle(Request('GET','/api/intercept/judge?x=1')).status,400)
        self.after=lambda:setattr(self.service,'raw_authorize',lambda r:True)
        self.assertEqual(self.service.handle(self.request).status,500)
