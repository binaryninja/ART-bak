"""Independent filesystem review; temporary fixtures only."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from artex.tools.filesystem import ScopedFilesystem,FilesystemError,FilesystemConflict
from artex.agent.runtime import BoundedAgent,Grant,RunStopped
from test_filesystem_tools import Context

class FilesystemReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.fs=ScopedFilesystem(str(self.root));self.addCleanup(self.fs.close)
    def call(self,name,**args):return self.fs._call(name,Context(),args)

    def test_hardlinked_destination_write_and_edit_rejected(self):
        (self.root/'f').write_text('old');os.link(self.root/'f',self.root/'alias')
        for name,args in (('Write',{'content':'new'}),('Edit',{'old_string':'old','new_string':'new'})):
            with self.assertRaises(FilesystemError):self.call(name,file_path='alias',**args)
        self.assertEqual((self.root/'f').read_text(),'old')

    def test_conflicting_destination_created_before_commit_is_not_overwritten(self):
        version=self.fs._version;count=[]
        def mutate(fd,name):
            count.append(1)
            if len(count)==2:(self.root/'new').write_text('other writer')
            return version(fd,name)
        with patch.object(self.fs,'_version',side_effect=mutate):
            with self.assertRaises(FilesystemConflict):self.call('Write',file_path='new',content='mine')
        self.assertEqual((self.root/'new').read_text(),'other writer')
        self.assertFalse(list(self.root.glob('.artex-write-*')))

    def test_existing_executable_mode_is_preserved(self):
        (self.root/'f').write_text('old');(self.root/'f').chmod(0o755)
        self.call('Edit',file_path='f',old_string='old',new_string='new')
        self.assertEqual((self.root/'f').stat().st_mode&0o777,0o755)

    def test_metadata_inspection_failure_rejects_without_replacement(self):
        (self.root/'f').write_text('old')
        with patch('artex.tools.filesystem.os.listxattr',side_effect=OSError('unsupported')):
            with self.assertRaises(FilesystemError):self.call('Write',file_path='f',content='new')
        self.assertEqual((self.root/'f').read_text(),'old')
        self.assertFalse(list(self.root.glob('.artex-write-*')))

    def test_existing_xattr_and_inherited_acl_rejected(self):
        (self.root/'f').write_text('old')
        for attrs in (([],['user.fixture']),(['system.posix_acl_access'],)):
            with patch('artex.tools.filesystem.os.listxattr',side_effect=list(attrs)):
                with self.assertRaises(FilesystemError):self.call('Write',file_path='f',content='new')
            self.assertEqual((self.root/'f').read_text(),'old')
            self.assertFalse(list(self.root.glob('.artex-write-*')))

    def test_output_limit_rejects_before_write(self):
        with ScopedFilesystem(str(self.root),max_output_bytes=1) as small:
            with self.assertRaises(FilesystemError):small._call('Write',Context(),{'file_path':'f','content':'new'})
        self.assertFalse((self.root/'f').exists())

    def test_cancel_at_last_precommit_check_leaves_target_and_no_temp(self):
        (self.root/'f').write_text('old')
        class Cancel:
            def __init__(self):self.n=0
            def check(self):
                self.n+=1
                if self.n==3:raise RunStopped('cancelled')
        with self.assertRaises(RunStopped):self.fs._call('Write',Cancel(),{'file_path':'f','content':'new'})
        self.assertEqual((self.root/'f').read_text(),'old')
        self.assertFalse(list(self.root.glob('.artex-write-*')))

    def test_missing_gate_prevents_all_callbacks(self):
        class Provider:
            def complete(self,*args):raise AssertionError('must not call model')
        result=BoundedAgent(Provider(),tools=self.fs.tools()).run({'messages':[]})
        self.assertNotEqual(result.reason,'completed')
        self.assertFalse(list(self.root.iterdir()))

if __name__=='__main__':unittest.main()
