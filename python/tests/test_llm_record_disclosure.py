import unittest
from test_server_llm_records import RecordHTTPTests,ROW,FULL
class RecordDisclosureTests(RecordHTTPTests):
    def test_tasks_revoked_during_fetch(self):
        allowed=[True];self.service.authorize=lambda *a:allowed[0]
        original=self.store.tasks
        self.store.tasks=lambda: (allowed.__setitem__(0,False) or ['private-task'])
        response=self.call('/tasks');self.assertEqual(response.status,403);self.assertNotIn(b'private-task',response.body)
    def test_redactor_revokes_body_grant(self):
        allowed=[True];self.service.authorize=lambda *a:allowed[0];self.db.results=[FULL]
        def redact(req,row,body):allowed[0]=False;return row
        self.service.redact=redact
        response=self.call('/7');self.assertEqual(response.status,403);self.assertNotIn(b'raw secret',response.body)
