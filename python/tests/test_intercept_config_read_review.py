"""Independent global metadata read review; no registration or mutation."""
import unittest
from contextlib import contextmanager,nullcontext
from unittest.mock import patch
from artex.server.intercept_config_read import InterceptConfigReader,InterceptConfigReadService
from artex.server.core import Request,json_response
import test_intercept_config_read as fixture


class Review(unittest.TestCase):
    def setUp(self):
        self.db=fixture.DB();self.live=True;self.granted=True
        self.reader=InterceptConfigReader(self.db)
        self.service=self.make(lambda:nullcontext())
        self.request=Request('GET','/api/intercept/tool-config')
    def make(self,guard):
        return InterceptConfigReadService(self.reader,authorize=lambda req:self.granted,
            current=lambda:self.live,lifecycle_guard=guard)
    def test_current_revoked_after_database_read_discloses_nothing(self):
        self.db.rows=[('["private-tool"]',)]
        self.db.hook=lambda:setattr(self,'live',False)
        result=self.service.handle(self.request)
        self.assertEqual(result.status,403);self.assertNotIn(b'private-tool',result.body)
    def test_guard_enter_revocation_prevents_query(self):
        @contextmanager
        def guard():
            self.granted=False
            yield
        self.service=self.make(guard)
        self.assertEqual(self.service.handle(self.request).status,403)
        self.assertEqual(self.db.calls,[])
    def test_guard_exit_swapped_callback_is_never_invoked(self):
        @contextmanager
        def guard():
            yield
            self.service.current=lambda:self.fail('replacement callback invoked')
        self.service=self.make(guard)
        self.assertEqual(self.service.handle(self.request).status,500)
    def test_final_encoder_revoked_current_discloses_nothing(self):
        self.db.rows=[('["private-tool"]',)]
        def encode(*args):
            self.live=False
            return json_response(*args)
        with patch('artex.server.intercept_config_read.json_response',side_effect=encode):
            result=self.service.handle(self.request)
        self.assertEqual(result.status,403);self.assertNotIn(b'private-tool',result.body)
    def test_reader_executor_swap_after_fetch_fails_closed(self):
        self.db.hook=lambda:setattr(self.reader,'executor',fixture.DB())
        self.assertEqual(self.service.handle(self.request).status,500)
    def test_only_bounded_read_query_no_mutation(self):
        self.db.rows=[('["x","x",null]',)]
        result=self.service.handle(self.request)
        self.assertEqual(result.json(),{'enabled_tools':['x','x','']})
        self.assertEqual(len(self.db.calls),1)
        self.assertTrue(self.db.calls[0].sql.startswith('SELECT '))
        self.assertEqual(self.db.calls[0].params,('intercept_enabled_tools',65536))
        self.assertFalse(hasattr(self.service,'register'))
    def test_bad_row_and_nonfinite_json_never_disclose(self):
        for rows in [[('[]',),('[]',)],[(1,)],[('NaN',)],[('[Infinity]',)]]:
            self.db.rows=rows
            result=self.service.handle(self.request)
            self.assertIn(result.status,(200,500))
            if result.status==200:self.assertEqual(result.json(),{'enabled_tools':[]})
    def test_go_string_unmarshal_replaces_unpaired_utf16_surrogates(self):
        self.db.rows=[('["\\ud800","\\udc00","\\ud800\\udc00"]',)]
        result=self.service.handle(self.request)
        self.assertEqual(result.json(),{'enabled_tools':['\ufffd','\ufffd','\U00010000']})
