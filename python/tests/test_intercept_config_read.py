import unittest
from contextlib import nullcontext,contextmanager
from unittest.mock import patch
from artex.server.intercept_config_read import InterceptConfigReader,InterceptConfigReadService
from artex.server.core import Request,json_response
from artex.db.tool_intercept_config import tools_digest
from artex.intercept import DEFAULT_ENABLED_TOOLS

class DB:
    def __init__(self):self.rows=[];self.calls=[];self.hook=None
    def fetch_all(self,q):
        self.calls.append(q)
        if self.hook:self.hook()
        return self.rows
class Tests(unittest.TestCase):
    def setUp(self):
        self.db=DB();self.allowed=True;self.reader=InterceptConfigReader(self.db)
        self.service=InterceptConfigReadService(self.reader,authorize=lambda r:self.allowed,current=lambda:True,lifecycle_guard=lambda:nullcontext())
        self.req=Request('GET','/api/intercept/tool-config')
    def get(self):return self.service.handle(self.req)
    def test_inert_constructor_missing_defaults_and_bound_query(self):
        self.assertEqual(self.db.calls,[]);self.assertEqual(self.get().json(),{'enabled_tools':list(DEFAULT_ENABLED_TOOLS),'version_digest':tools_digest(None)})
        self.assertEqual(self.db.calls[0].params,('intercept_enabled_tools',65536));self.assertIn('octet_length',self.db.calls[0].sql)
    def test_source_null_malformed_and_order(self):
        for stored,expected in [('null',None),('bad',[]),('{}',[]),('[1]',[]),('[]',[]),('["Unknown","Bash",null,"Bash"]',['Unknown','Bash','','Bash'])]:
            self.db.rows=[(stored,)];self.assertEqual(self.get().json(),{'enabled_tools':expected,'version_digest':tools_digest(stored)})
    def test_oversize_sentinel_and_count_fail_closed(self):
        self.db.rows=[(None,)];self.assertEqual(self.get().status,500)
        self.db.rows=[('['+','.join('"x"' for _ in range(1001))+']',)];self.assertEqual(self.get().status,500)
    def test_denied_before_sql_and_after_read(self):
        self.allowed=False;self.assertEqual(self.get().status,403);self.assertEqual(self.db.calls,[])
        self.allowed=True;self.db.hook=lambda:setattr(self,'allowed',False)
        self.assertEqual(self.get().status,403)
    def test_final_serialization_and_guard_exit_revoke(self):
        def encode(*a):self.allowed=False;return json_response(*a)
        with patch('artex.server.intercept_config_read.json_response',side_effect=encode):self.assertEqual(self.get().status,403)
        self.allowed=True
        @contextmanager
        def guard():
            yield
            self.allowed=False
        self.service=InterceptConfigReadService(self.reader,authorize=lambda r:self.allowed,current=lambda:True,lifecycle_guard=guard)
        self.assertEqual(self.get().status,403)
    def test_callback_swap_not_invoked(self):
        self.db.hook=lambda:setattr(self.service,'authorize',lambda r:self.fail('replacement invoked'))
        self.assertEqual(self.get().status,500)
    def test_no_mutation_or_query_and_redacted_db_error(self):
        self.req=Request('PUT',self.req.target);self.assertEqual(self.get().status,400);self.assertEqual(self.db.calls,[])
        self.req=Request('GET','/api/intercept/tool-config?task=1');self.assertEqual(self.get().status,400)
        self.req=Request('GET','/api/intercept/tool-config')
        def bad():raise ValueError('private database secret')
        self.db.hook=bad;result=self.get();self.assertEqual(result.status,500);self.assertNotIn(b'private',result.body)
