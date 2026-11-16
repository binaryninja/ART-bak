import os,unittest
from dataclasses import replace
from contextlib import nullcontext,contextmanager
from unittest.mock import patch
import test_skill_file_listing as listing
from artex.skills.file_read import SkillFileReader,SkillFileSelection,SkillFileConflict,SkillFileUnavailable

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=listing.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.path=self.f.skill/'SKILL.md';self.path.write_text('inert private instructions')
        self.allowed=self.current=True;self.hook=lambda:None
        self.selection=SkillFileSelection('sample','SKILL.md')
    def reader(self,**kwargs):
        def current(s,q):self.hook();return self.current
        return SkillFileReader(self.f.scope,authorize=lambda s,q:self.allowed,current=current,
            lifecycle_guard=kwargs.pop('lifecycle_guard',lambda *a:nullcontext()),**kwargs)
    def test_bounded_snapshot_and_receipt(self):
        r=self.reader();snapshot=r.read(self.selection)
        self.assertEqual(snapshot.content,'inert private instructions');self.assertEqual(snapshot.file,'SKILL.md')
        r.validate_disclosure(self.selection,snapshot)
    def test_invalid_utf8_replacement_and_null(self):
        self.path.write_bytes(b'\xff\x00inert')
        self.assertEqual(self.reader().read(self.selection).content,'\ufffd\x00inert')
        self.path.write_bytes(b'\xe2\x82')
        self.assertEqual(self.reader().read(self.selection).content,'\ufffd\ufffd')
    def test_nested_content(self):
        (self.f.skill/'refs').mkdir();(self.f.skill/'refs'/'data').write_text('nested')
        selection=SkillFileSelection('sample','refs/data')
        self.assertEqual(self.reader().read(selection).content,'nested')
    def test_no_io_before_authority(self):
        self.allowed=False
        with patch('artex.skills.file_read.ScopedFilesystem') as fs:
            with self.assertRaises(PermissionError):self.reader().read(self.selection)
            fs.assert_not_called()
    def test_symlink_and_hardlink_refused(self):
        self.path.unlink();self.path.symlink_to('/etc/passwd')
        with self.assertRaises(SkillFileUnavailable):self.reader().read(self.selection)
        self.path.unlink();outside=self.f.root/'outside';outside.write_text('secret');os.link(outside,self.path)
        with self.assertRaises(SkillFileUnavailable):self.reader().read(self.selection)
    def test_fifo_refused_without_blocking(self):
        self.path.unlink();os.mkfifo(self.path)
        with self.assertRaises(SkillFileUnavailable):self.reader().read(self.selection)
    def test_size_and_depth_bounds(self):
        with self.assertRaises(ValueError):self.reader(max_bytes=1).read(self.selection)
        with self.assertRaises(ValueError):self.reader(max_depth=1).read(SkillFileSelection('sample','refs/data'))
    def test_path_validation(self):
        for name,path in (('UPPER','SKILL.md'),('sample','../x'),('sample','/etc/passwd'),('sample','refs/./x'),('sample','%2f')):
            with self.assertRaises(ValueError):self.reader().read(SkillFileSelection(name,path))
    def test_file_changed_after_read(self):
        r=self.reader();snapshot=r.read(self.selection);self.path.write_text('replacement')
        with self.assertRaises(SkillFileConflict):r.validate_disclosure(self.selection,snapshot)
    def test_ancestor_replaced_after_read(self):
        r=self.reader();snapshot=r.read(self.selection)
        self.f.skill.rename(self.f.root/'old');self.f.skill.mkdir();(self.f.skill/'SKILL.md').write_text('same name')
        with self.assertRaises(SkillFileConflict):r.validate_disclosure(self.selection,snapshot)
    def test_receipt_not_transferable(self):
        a=self.reader();snapshot=a.read(self.selection)
        with self.assertRaises(SkillFileUnavailable):self.reader().validate_disclosure(self.selection,snapshot)
        with self.assertRaises(SkillFileUnavailable):a.validate_disclosure(SkillFileSelection('sample','other'),snapshot)
    def test_guard_exit_modification_detected(self):
        @contextmanager
        def guard(*a):
            yield
            self.path.write_text('changed on exit')
        with self.assertRaises(SkillFileConflict):self.reader(lifecycle_guard=guard).read(self.selection)
    def test_postread_permission_and_selection_revocation(self):
        r=self.reader();snapshot=r.read(self.selection);self.allowed=False
        with self.assertRaises(PermissionError):r.validate_disclosure(self.selection,snapshot)
    def test_final_current_file_replacement(self):
        calls=[0]
        def count():calls[0]+=1
        self.hook=count;r=self.reader();r.read(self.selection);last=calls[0];calls[0]=0
        def change():
            calls[0]+=1
            if calls[0]==last:self.path.write_text('changed at last current')
        self.hook=change
        with self.assertRaises(SkillFileConflict):r.read(self.selection)
    def test_missing_file(self):
        self.path.unlink()
        with self.assertRaises(FileNotFoundError):self.reader().read(self.selection)
    def test_content_substitution_and_copied_receipt_refused(self):
        r=self.reader();snapshot=r.read(self.selection)
        with self.assertRaises(SkillFileUnavailable):r.validate_disclosure(self.selection,replace(snapshot,content='substituted'))
        with self.assertRaises(SkillFileUnavailable):r.validate_disclosure(self.selection,replace(snapshot,receipt=replace(snapshot.receipt)))
    def test_issued_receipt_field_mutation_refused(self):
        r=self.reader();snapshot=r.read(self.selection)
        object.__setattr__(snapshot.receipt,'raw_digest','0'*64)
        with self.assertRaises(SkillFileUnavailable):r.validate_disclosure(self.selection,snapshot)
    def test_midread_change_detected(self):
        original=os.read;changed=[False]
        def read(fd,size):
            value=original(fd,size)
            if value and not changed[0]:
                changed[0]=True;self.path.write_text('changed while reading')
            return value
        with patch('artex.tools.filesystem.os.read',read):
            with self.assertRaises(SkillFileConflict):self.reader().read(self.selection)
    def test_nested_symlink_refused(self):
        (self.f.skill/'refs').symlink_to(self.f.root,target_is_directory=True)
        with self.assertRaises(SkillFileUnavailable):self.reader().read(SkillFileSelection('sample','refs/outside'))

if __name__=='__main__':unittest.main()
