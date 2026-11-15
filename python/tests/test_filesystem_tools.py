"""Only temporary fixtures; no user files, shell, or network."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from artex.tools.filesystem import ScopedFilesystem, FilesystemError, FilesystemConflict
from artex.agent.runtime import BoundedAgent, Grant, RunStopped


class Context:
    def check(self):
        pass


class FilesystemTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.fs = ScopedFilesystem(str(self.root))
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.fs.close)

    def call(self, name, **args):
        return next(t for t in self.fs.tools() if t.name == name).callback(Context(), args)

    def test_names_schemas_and_utf8_roundtrip(self):
        self.assertEqual([t.name for t in self.fs.tools()], ['Read', 'Write', 'Edit', 'LS'])
        self.assertEqual(self.call('Write', file_path='file', content='hello\nworld'), 'Created file (11 bytes)')
        self.assertEqual(self.call('Read', file_path='file', offset=2, limit=1), '     2\tworld\n')
        self.call('Edit', file_path='file', old_string='hello', new_string='héllo')
        self.assertEqual((self.root/'file').read_text(), 'héllo\nworld')
        self.assertEqual(os.stat(self.root/'file').st_mode & 0o777, 0o600)

    def test_existing_modes_preserved_privilege_bits_removed(self):
        for tool in ('Write','Edit'):
            for mode in (0o755, 0o640, 0o4755, 0o2750, 0o1755):
                with self.subTest(tool=tool,mode=oct(mode)):
                    target=self.root/'f';target.write_text('old');target.chmod(mode)
                    if tool=='Write':self.call(tool,file_path='f',content='new')
                    else:self.call(tool,file_path='f',old_string='old',new_string='new')
                    self.assertEqual(target.stat().st_mode & 0o7777,mode & 0o777)
                    self.assertEqual(target.read_text(),'new')

    def test_xattr_and_uninspectable_metadata_rejected_without_write(self):
        target=self.root/'f';target.write_text('old')
        for value in (['user.fixture'], ['system.posix_acl_access']):
            with patch('artex.tools.filesystem.os.listxattr',return_value=value):
                with self.assertRaises(FilesystemError):self.call('Write',file_path='f',content='new')
            self.assertEqual(target.read_text(),'old')
            self.assertEqual(list(self.root.iterdir()),[target])
        with patch('artex.tools.filesystem.os.listxattr',side_effect=OSError('unsupported')):
            with self.assertRaises(FilesystemError):self.call('Edit',file_path='f',old_string='old',new_string='new')
        self.assertEqual(target.read_text(),'old')

    def test_ownership_mismatch_rejected_without_chown(self):
        from types import SimpleNamespace
        target=self.root/'f';target.write_text('old')
        original=os.fstat;calls=[]
        def mismatch(fd):
            value=original(fd);calls.append(fd)
            if len(calls)==3:  # freshly created replacement, after original read
                return SimpleNamespace(st_uid=value.st_uid,st_gid=value.st_gid+1)
            return value
        with patch('artex.tools.filesystem.os.fstat',side_effect=mismatch):
            with self.assertRaisesRegex(FilesystemError,'Ownership'):
                self.call('Write',file_path='f',content='new')
        self.assertEqual(target.read_text(),'old')
        self.assertEqual(list(self.root.iterdir()),[target])

    def test_existing_xattr_rejected(self):
        target=self.root/'f';target.write_text('old')
        try:os.setxattr(target,'user.artex_fixture',b'fixture')
        except OSError:self.skipTest('Temporary filesystem lacks user xattrs')
        with self.assertRaises(FilesystemError):self.call('Write',file_path='f',content='new')
        self.assertEqual(target.read_text(),'old')
        self.assertEqual(os.getxattr(target,'user.artex_fixture'),b'fixture')

    def test_ambiguity_and_all(self):
        (self.root/'f').write_text('a a')
        with self.assertRaises(FilesystemError):
            self.call('Edit', file_path='f', old_string='a', new_string='b')
        self.call('Edit', file_path='f', old_string='a', new_string='b', replace_all=True)
        self.assertEqual((self.root/'f').read_text(), 'b b')

    def test_traversal_and_absolute_rejected(self):
        for path in ('../escape', '/etc/passwd', 'x/../f', 'a//b', 'a\\b', 'f\nforge'):
            with self.subTest(path=path), self.assertRaises(FilesystemError):
                self.call('Write', file_path=path, content='no')

    def test_symlinks_and_hardlinks_and_fifo(self):
        (self.root/'f').write_text('fixture')
        (self.root/'link').symlink_to(self.root/'f')
        (self.root/'dirlink').symlink_to(self.root, target_is_directory=True)
        for path in ('link', 'dirlink/f'):
            with self.assertRaises(OSError): self.call('Read', file_path=path)
            with self.assertRaises(OSError): self.call('Write', file_path=path, content='no')
        os.link(self.root/'f', self.root/'hard')
        with self.assertRaises(FilesystemError): self.call('Read', file_path='hard')
        os.mkfifo(self.root/'pipe')
        with self.assertRaises(FilesystemError): self.call('Read', file_path='pipe')
        self.assertEqual((self.root/'f').read_text(), 'fixture')

    def test_existing_explicit_root_and_closed(self):
        with self.assertRaises(FilesystemError): ScopedFilesystem('.')
        with self.assertRaises(FileNotFoundError): ScopedFilesystem(str(self.root/'missing'))
        (self.root/'symlink').symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError): ScopedFilesystem(str(self.root/'symlink'))
        self.fs.close()
        with self.assertRaises(FilesystemError): self.call('LS')

    def test_parent_creation_not_implicit(self):
        with self.assertRaises(FileNotFoundError): self.call('Write', file_path='missing/file', content='no')
        self.assertFalse((self.root/'missing').exists())

    def test_list_directory_first_nonrecursive(self):
        (self.root/'dir').mkdir(); (self.root/'file').write_text('hi')
        self.assertEqual(self.call('LS'), 'dir/\nfile\t2 bytes\n')
        self.assertEqual(self.call('LS', path='dir'), '(empty directory)')

    def test_byte_entry_output_and_binary_limits(self):
        with ScopedFilesystem(str(self.root), max_bytes=4, max_entries=1, max_output_bytes=30) as fs:
            with self.assertRaises(FilesystemError): fs._call('Write', Context(), {'file_path':'f','content':'ééé'})
            (self.root/'f').write_bytes(b'12345')
            with self.assertRaises(FilesystemError): fs._call('Read', Context(), {'file_path':'f'})
            (self.root/'g').write_bytes(b'\0')
            with self.assertRaises(FilesystemError): fs._call('Read', Context(), {'file_path':'g'})
            with self.assertRaises(FilesystemError): fs._call('LS', Context(), {})
        (self.root/'g').write_bytes(b'\xff')
        with self.assertRaises(FilesystemError): self.call('Read', file_path='g')

    def test_invalid_types_and_fields(self):
        for args in ({'file_path':'f','offset':True}, {'file_path':'f','limit':-1}, {'file_path':'f','extra':1}):
            with self.assertRaises(FilesystemError): self.call('Read', **args)
        with self.assertRaises(FilesystemError): self.call('Edit', file_path='f', old_string='', new_string='a')

    def test_conflict_leaves_original_and_cleans_temp(self):
        (self.root/'f').write_text('original')
        original=self.fs._version
        calls=[]
        def changed(fd,name):
            calls.append(name)
            value=original(fd,name)
            return None if len(calls)==2 else value
        with patch.object(self.fs,'_version',side_effect=changed):
            with self.assertRaises(FilesystemConflict): self.call('Write', file_path='f', content='new')
        self.assertEqual((self.root/'f').read_text(),'original')
        self.assertEqual(list(self.root.iterdir()),[self.root/'f'])

    def test_cancel_before_commit_cleans_temp(self):
        class Cancel(Context):
            def __init__(self): self.count=0
            def check(self):
                self.count+=1
                if self.count>=2: raise RunStopped('cancelled')
        with self.assertRaises(RunStopped): self.fs._call('Write',Cancel(),{'file_path':'f','content':'new'})
        self.assertEqual(list(self.root.iterdir()),[])

    def test_bounded_agent_admitted_write_read(self):
        class Provider:
            def __init__(self): self.calls=0
            def complete(self,ctx,request):
                self.calls+=1
                if self.calls==1:
                    return ({'role':'assistant','content':[{'type':'tool_use','id':'write1','name':'Write','input':{'file_path':'f','content':'fixture'}}]}, 'tool_use', {})
                return ({'role':'assistant','content':[{'type':'text','text':'done'}]},'end_turn',{})
        result=BoundedAgent(Provider(),tools=self.fs.tools(),gate=lambda c,a:Grant(True,True,True)).run({'messages':[{'role':'user','content':[{'type':'text','text':'fixture'}]}]})
        self.assertEqual(result.reason,'completed')
        self.assertEqual((self.root/'f').read_text(),'fixture')
        self.assertEqual(result.tool_calls,1)

    def test_bounded_agent_gate_denial_never_writes(self):
        class Provider:
            def complete(self,ctx,request):
                return ({'role':'assistant','content':[{'type':'tool_use','id':'write1','name':'Write','input':{'file_path':'f','content':'new'}}]}, 'tool_use', {})
        result=BoundedAgent(Provider(),tools=self.fs.tools(),gate=lambda c,a:Grant(True,True,a.kind=='model')).run({'messages':[{'role':'user','content':[{'type':'text','text':'fixture'}]}]})
        self.assertFalse((self.root/'f').exists())
        self.assertEqual(result.reason,'permission_denied')


if __name__ == '__main__': unittest.main()
