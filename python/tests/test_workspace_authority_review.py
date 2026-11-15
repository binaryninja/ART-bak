"""Temporary-file-only disclosure revocation regression."""
import unittest
from unittest.mock import patch
import test_workspace_http as fixture
from artex.tools.filesystem import ScopedFilesystem
class WorkspaceAuthorityReview(unittest.TestCase):
    def test_raw_read_revocation_before_response_hides_content(self):
        f=fixture.Tests();f.setUp();self.addCleanup(f.doCleanups)
        (f.root/'x').write_text('synthetic-private')
        original=ScopedFilesystem._read
        def read(fs,*args):
            result=original(fs,*args);f.allowed=False;return result
        with patch.object(ScopedFilesystem,'_read',read):
            response=f.call('read','x')
        self.assertEqual(response.status,403);self.assertNotIn(b'synthetic-private',response.body)
