import json,os,unittest
from contextlib import nullcontext
from dataclasses import replace
from unittest.mock import patch
import test_skill_file_read as content
from artex.skills.file_read import SkillFileConflict
from artex.skills.catalog_read import SkillCatalogReader,SkillCatalogUnavailable,SkillCatalogConflict,SkillCatalogLimit

class Tests(unittest.TestCase):
    def setUp(self):
        self.c=content.Tests();self.c.setUp();self.addCleanup(self.c.doCleanups)
        self.c.path.write_text('---\nname: different-name\ndescription: Inert description\nmcps: one, two\n---\nSECRET BODY')
        self.allowed=self.current=True;self.hook=lambda:None
        self.source=self.c.reader();self.reader=self.make()
    def make(self,**kw):
        def current(s):self.hook();return self.current
        return SkillCatalogReader(self.c.f.scope,self.source,authorize_global=lambda s:self.allowed,current=current,lifecycle_guard=lambda *a:nullcontext(),**kw)
    def test_metadata_directory_identity_no_body(self):
        result=self.reader.read();row=json.loads(result.payload)['skills'][0]
        self.assertEqual(row['name'],'sample');self.assertEqual(row['description'],'Inert description')
        self.assertEqual(row['mcps'],['one','two']);self.assertNotIn('SECRET',result.payload)
        self.reader.validate_disclosure(result)
    def test_no_metadata_and_nested_listing(self):
        self.c.path.unlink();(self.c.f.skill/'refs').mkdir();(self.c.f.skill/'refs'/'a').write_text('data')
        row=json.loads(self.reader.read().payload)['skills'][0]
        self.assertNotIn('description',row);self.assertIn('refs/a',row['files'])
    def test_no_io_without_global_authority(self):
        for attribute in ('allowed','current'):
            setattr(self,attribute,False)
            with patch('artex.skills.catalog_read.ScopedFilesystem') as fs:
                with self.assertRaises(PermissionError):self.reader.read()
                fs.assert_not_called()
            setattr(self,attribute,True)
    def test_source_permission_independent(self):
        self.c.allowed=False
        with self.assertRaises(PermissionError):self.reader.read()
    def test_root_and_entry_bounds(self):
        for i in range(4):(self.c.f.root/f'ignored{i}').write_text('')
        with self.assertRaises(SkillCatalogLimit):self.make(max_entries=2).read()
        with self.assertRaises(SkillCatalogLimit):self.make(max_text_bytes=10).read()
        with self.assertRaises(SkillCatalogLimit):self.make(max_metadata_bytes=10).read()
    def test_metadata_fail_closed(self):
        for data in ('no frontmatter','---\nname: sample\ndescription: |\n text\n---\nbody'):
            self.c.path.write_text(data)
            with self.assertRaisesRegex(SkillCatalogUnavailable,'Unsupported skill metadata'):self.reader.read()
    def test_symlink_hardlink_fifo(self):
        bad=self.c.f.skill/'bad';bad.symlink_to('/etc/passwd')
        with self.assertRaises(SkillCatalogUnavailable):self.reader.read()
        bad.unlink();os.link(self.c.path,bad)
        with self.assertRaises(SkillCatalogUnavailable):self.reader.read()
        bad.unlink();os.mkfifo(bad)
        with self.assertRaises(SkillCatalogUnavailable):self.reader.read()
    def test_nested_addition_invalidates_snapshot(self):
        (self.c.f.skill/'refs').mkdir();snapshot=self.reader.read()
        (self.c.f.skill/'refs'/'new').write_text('new')
        with self.assertRaises((SkillCatalogConflict,SkillFileConflict)):self.reader.validate_disclosure(snapshot)
    def test_late_callback_tree_change(self):
        snapshot=self.reader.read()
        self.hook=lambda:(self.c.f.skill/'new').write_text('new')
        with self.assertRaises((SkillCatalogConflict,SkillFileConflict)):self.reader.validate_disclosure(snapshot)
    def test_copied_and_mutated_snapshots(self):
        snapshot=self.reader.read()
        with self.assertRaises(SkillCatalogUnavailable):self.reader.validate_disclosure(replace(snapshot))
        object.__setattr__(snapshot,'payload','{"skills":[]}')
        with self.assertRaises(SkillCatalogUnavailable):self.reader.validate_disclosure(snapshot)
    def test_foreign_bound_content_methods_refused(self):
        from artex.skills.file_read import SkillFileReader
        foreign=SkillFileReader(self.c.f.scope,authorize=lambda *a:True,current=lambda *a:True,lifecycle_guard=lambda *a:nullcontext())
        self.c.allowed=False
        for name in ('read','validate_disclosure','_snapshot_check','_receipt_check','validate','_allow','_open','_selection'):
            with self.subTest(method=name),patch.object(self.source,name,getattr(foreign,name)):
                with self.assertRaises(SkillCatalogUnavailable):self.reader.read()
    def test_root_replacement(self):
        snapshot=self.reader.read();root=self.c.f.root
        moved=root.with_name(root.name+'-moved');root.rename(moved);root.mkdir()
        self.addCleanup(lambda:os.rename(moved,root) if not root.exists() else None)
        with self.assertRaises((SkillCatalogConflict,SkillFileConflict)):self.reader.validate_disclosure(snapshot)
        root.rmdir();moved.rename(root)

if __name__=='__main__':unittest.main()
