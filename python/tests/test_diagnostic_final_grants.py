"""Synthetic diagnostic disclosure checks, no live logs or settings."""
import unittest
import test_pool_health as pool
import test_workspace_http as workspace
from unittest.mock import patch
from artex.tools.filesystem import ScopedFilesystem

class Tests(unittest.TestCase):
    def test_pool_status_and_retry_revoke_during_fetch(self):
        for op,rows in [('status',[[],[],[pool.PROFILE]]),('retry',[[]])]:
            with self.subTest(op=op):
                f=pool.PoolTests();f.make(rows);allowed=[True]
                f.service.authorize=lambda *a:allowed[0]
                original=f.db.fetch_all
                def read(q):
                    result=original(q);allowed[0]=False;return result
                f.db.fetch_all=read
                result=f.call(op)
                self.assertEqual(result.status,403)
                self.assertNotIn(b'chain',result.body);self.assertNotIn(b'connect',result.body)

    def test_pool_read_policy_swap_is_not_new_permission(self):
        f=pool.PoolTests();f.make([[]]);original=f.db.fetch_all
        def read(q):
            result=original(q);f.service.authorize=lambda *a:True;return result
        f.db.fetch_all=read
        self.assertEqual(f.call('retry').status,403)

    def test_workspace_download_rechecks_original_selector(self):
        f=workspace.Tests();f.setUp();self.addCleanup(f.doCleanups)
        (f.root/'x').write_text('synthetic-private')
        seen=[];authorize=f.service.authorize
        def grant(req,action,binding,path):
            seen.append(path);return authorize(req,action,binding,path)
        f.service.authorize=grant
        # Host config fixture explicitly replaces the initial policy before use.
        f.service._callbacks=(f.service.resolve,grant,f.service.current,f.service.guard)
        original=ScopedFilesystem._read
        def read(fs,*args):
            result=original(fs,*args);f.allowed=False;return result
        with patch.object(ScopedFilesystem,'_read',read):result=f.call('download','x')
        self.assertEqual(result.status,403);self.assertNotIn(b'synthetic-private',result.body)
        self.assertGreaterEqual(len(seen),2);self.assertEqual(set(seen),{'x'})
