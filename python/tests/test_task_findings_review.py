"""Independent narrow disclosure-boundary tests; synthetic SQL only."""
import unittest
from unittest.mock import patch
import test_task_findings as fixture
import artex.server.task_findings as http

class FindingReview(unittest.TestCase):
    def test_serialization_revokes_request(self):
        f=fixture.Tests();f.make();original=http.json_response
        def serialize(*args):
            response=original(*args);f.live=False;return response
        with patch.object(http,'json_response',serialize):result=f.get()
        self.assertEqual(result.status,403);self.assertNotIn(b'synthetic',result.body)
    def test_serialization_revokes_asset_read(self):
        live=[True];f=fixture.Tests();f.make(assets=lambda *a:live[0]);original=http.json_response
        def serialize(*args):
            response=original(*args);live[0]=False;return response
        with patch.object(http,'json_response',serialize):result=f.get()
        self.assertEqual(result.status,403);self.assertNotIn(b'example.invalid',result.body)
    def test_direct_source_grant_revoked_after_asset_query(self):
        row=list(fixture.ROW);row[3]=6
        f=fixture.Tests();f.make([fixture.context.PARENT,fixture.context.SOURCES,[tuple(row)],[fixture.ASSET]])
        prior=f.coverage.authorize;live=[True]
        def authorize(scope,task,exp):return prior(scope,task,exp) and (task!=6 or live[0])
        f.coverage.authorize=authorize
        f.store=fixture.TaskFindingsStore(f.coverage,asset_authorize=lambda *a:True)
        f.service=fixture.TaskFindingsService(f.store,authorize=lambda *a:True)
        original=f.f.db.fetch_all
        def fetch(query):
            result=original(query)
            if 'FROM assets WHERE' in query.sql:live[0]=False
            return result
        f.f.db.fetch_all=fetch
        result=f.get();self.assertEqual(result.status,403);self.assertNotIn(b'synthetic',result.body)
