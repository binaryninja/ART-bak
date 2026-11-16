"""Independent raw detail validation and final disclosure boundaries."""
import unittest,json
from unittest.mock import patch
import test_intercept_detail_read as fixture
from artex.server.core import json_response

class DetailReview(unittest.TestCase):
    def fixture(self):
        f=fixture.Tests();f.setUp();self.addCleanup(f.doCleanups);return f
    def test_unpaired_surrogate_nested_values_and_keys_fail_closed(self):
        for raw in ('{"model_input":{"text":"\\ud800"}}','{"model_input":{"\\ud800":1}}','{"user_message":"\\udfff"}'):
            with self.subTest(raw=raw):
                f=self.fixture();f.rows=[(*f.row[:-1],raw)]
                response=f.service.handle(f.request)
                self.assertEqual(response.status,500)
    def test_go_case_insensitive_known_struct_field(self):
        f=self.fixture();f.rows=[(*f.row[:-1],'{"Correlation":"exact","CONTEXT":[{"Kind":"user","Text":"message"}]}')]
        response=f.service.handle(f.request)
        self.assertEqual(response.status,200)
        self.assertEqual(response.json()['audit']['correlation'],'exact')
        self.assertEqual(response.json()['audit']['context'],[{'kind':'user','text':'message'}])
    def test_case_alias_does_not_hide_invalid_earlier_known_field(self):
        f=self.fixture();f.rows=[(*f.row[:-1],'{"Correlation":5,"correlation":"exact"}')]
        self.assertEqual(f.service.handle(f.request).status,500)

    def test_final_serialization_raw_revoke_withholds_record(self):
        f=self.fixture()
        def encode(*args):
            response=json_response(*args);f.raw=False;return response
        with patch('artex.server.intercept_detail_read.json_response',encode):response=f.service.handle(f.request)
        self.assertEqual(response.status,403);self.assertNotIn(b'raw reason',response.body)
    def test_guard_exit_callback_replacement_withholds_record(self):
        f=self.fixture();f.after_guard=lambda:setattr(f.service,'raw_authorize',lambda *a:True)
        response=f.service.handle(f.request)
        self.assertEqual(response.status,403);self.assertNotIn(b'raw reason',response.body)
    def test_known_nested_type_and_profile_bool_rejected(self):
        for audit in ({'context':[{'text':True}]},{'profile_id':True},{'captured_at':'2026-02-30T00:00:00Z'}):
            f=self.fixture();f.rows=[(*f.row[:-1],json.dumps(audit))]
            self.assertEqual(f.service.handle(f.request).status,500)

if __name__=='__main__':unittest.main()
