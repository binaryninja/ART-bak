import os,tempfile,unittest
from pathlib import Path
from contextlib import nullcontext,contextmanager
from unittest.mock import patch
from artex.skills.file_listing import SkillDirectoryScope,SkillFileListingReader,SkillListingUnavailable,SkillListingLimit,SkillListingConflict

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.skill=self.root/'sample';self.skill.mkdir()
        st=self.root.stat();self.scope=SkillDirectoryScope(str(self.root),st.st_dev,st.st_ino,'revision')
        self.allowed=self.current=True;self.hook=lambda:None
    def make(self,**kwargs):
        def current(s,n):self.hook();return self.current
        return SkillFileListingReader(self.scope,authorize=lambda s,n:self.allowed,current=current,
                    lifecycle_guard=kwargs.pop('lifecycle_guard',lambda *a:nullcontext()),**kwargs)
    def test_order_nested_and_empty_directories(self):
        (self.skill/'references').mkdir();(self.skill/'empty').mkdir()
        (self.skill/'references'/'a.txt').write_text('inert')
        (self.skill/'SKILL.md').write_text('never parse or execute')
        self.assertEqual(self.make().read('sample'),{'files':['SKILL.md','empty/','references/','references/a.txt']})
    def test_empty_and_missing(self):
        self.assertEqual(self.make().read('sample'),{'files':[]})
        with self.assertRaises(FileNotFoundError):self.make().read('absent')
    def test_no_open_before_authorization(self):
        self.allowed=False
        with patch('artex.skills.file_listing.ScopedFilesystem') as fs:
            with self.assertRaises(PermissionError):self.make().read('sample')
            fs.assert_not_called()
    def test_symlink_skill_and_descendant_refused(self):
        (self.root/'alias').symlink_to(self.skill,target_is_directory=True)
        with self.assertRaises(SkillListingUnavailable):self.make().read('alias')
        (self.skill/'escape').symlink_to('/etc')
        with self.assertRaises(SkillListingUnavailable):self.make().read('sample')
    def test_hardlink_and_fifo_refused(self):
        (self.root/'outside').write_text('private')
        os.link(self.root/'outside',self.skill/'hard')
        with self.assertRaises(SkillListingUnavailable):self.make().read('sample')
        (self.skill/'hard').unlink();os.mkfifo(self.skill/'fifo')
        with self.assertRaises(SkillListingUnavailable):self.make().read('sample')
    def test_entry_depth_and_bytes_bounds(self):
        (self.skill/'a').mkdir();(self.skill/'a'/'b').mkdir()
        for limits in ({'max_entries':1},{'max_depth':1},{'max_text_bytes':1}):
            with self.subTest(limits=limits),self.assertRaises(SkillListingLimit):self.make(**limits).read('sample')
    def test_root_inode_mismatch(self):
        bad=SkillDirectoryScope(str(self.root),self.scope.device,self.scope.inode+1,'revision')
        r=SkillFileListingReader(bad,authorize=lambda *a:True,current=lambda *a:True,lifecycle_guard=lambda *a:nullcontext())
        with self.assertRaises(SkillListingConflict):r.read('sample')
    def test_guard_exit_revoke(self):
        @contextmanager
        def guard(*a):
            yield
            self.allowed=False
        with self.assertRaises(PermissionError):self.make(lifecycle_guard=guard).read('sample')
    def test_callback_scope_swap(self):
        self.hook=lambda:object.__setattr__(self.scope,'revision','changed')
        with self.assertRaises(SkillListingUnavailable):self.make().read('sample')
    def test_current_revokes_global(self):
        self.hook=lambda:setattr(self,'allowed',False)
        with self.assertRaises(PermissionError):self.make().read('sample')
    def test_invalid_names_and_files(self):
        for name in ('../sample','Sample','', 'a/b'):
            with self.assertRaises(ValueError):self.make().read(name)
        (self.skill/'bad%name').write_text('inert')
        with self.assertRaises(ValueError):self.make().read('sample')
    def test_does_not_open_file_content(self):
        (self.skill/'secret').write_text('never return me')
        from artex.tools.filesystem import ScopedFilesystem
        with patch.object(ScopedFilesystem,'_read',side_effect=AssertionError('content opened')):
            self.assertEqual(self.make().read('sample'),{'files':['secret']})
    def test_final_disclosure_revalidation(self):
        r=self.make();r.read('sample');self.allowed=False
        with self.assertRaises(PermissionError):r.validate_disclosure('sample')
    def test_directory_replacement_between_stat_and_open(self):
        nested=self.skill/'nested';nested.mkdir()
        original=os.open
        def replacing(path,flags,*args,**kwargs):
            if path=='nested':
                nested.rename(self.skill/'old')
                nested.mkdir()
                (nested/'new-secret-name').write_text('not read')
            return original(path,flags,*args,**kwargs)
        with patch('artex.skills.file_listing.os.open',replacing):
            with self.assertRaises(SkillListingConflict):self.make().read('sample')
    def test_symlink_root_ancestor(self):
        link=self.root/'linked';link.symlink_to(self.skill,target_is_directory=True)
        st=self.skill.stat();scope=SkillDirectoryScope(str(link),st.st_dev,st.st_ino,'revision')
        reader=SkillFileListingReader(scope,authorize=lambda *a:True,current=lambda *a:True,lifecycle_guard=lambda *a:nullcontext())
        with self.assertRaises(OSError):reader.read('sample')
    def test_last_current_revokes_disclosure_or_global(self):
        count=[0]
        def baseline():count[0]+=1
        self.hook=baseline;r=self.make();r.read('sample');last=count[0]
        for target in ('global','disclosure'):
            count[0]=0;allowed=[True];self.allowed=True
            def revoke():
                count[0]+=1
                if count[0]==last:
                    if target=='global':self.allowed=False
                    else:allowed[0]=False
            def disclosure():
                if not allowed[0]:raise PermissionError()
            self.hook=revoke
            with self.subTest(target=target),self.assertRaises(PermissionError):r.read('sample',disclosure=disclosure)
    def test_replaced_root_after_read_fails_disclosure(self):
        reader=self.make();reader.read('sample')
        moved=self.root.with_name(self.root.name+'-old')
        self.root.rename(moved)
        self.addCleanup(lambda:__import__('shutil').rmtree(moved))
        self.root.mkdir()
        with self.assertRaises(SkillListingConflict):reader.validate_disclosure('sample')
    def test_replaced_root_at_guard_exit_fails_read(self):
        moved=self.root.with_name(self.root.name+'-old')
        @contextmanager
        def guard(*a):
            yield
            self.root.rename(moved);self.root.mkdir()
        self.addCleanup(lambda:__import__('shutil').rmtree(moved,ignore_errors=True))
        with self.assertRaises(SkillListingConflict):self.make(lifecycle_guard=guard).read('sample')
    def test_global_discovery_cap_counts_queued_ancestor_siblings(self):
        (self.skill/'a').mkdir();(self.skill/'b').mkdir()
        (self.skill/'a'/'child').write_text('inert')
        original=os.scandir;stats=[]
        class Entry:
            def __init__(self,entry):self.entry=entry;self.name=entry.name
            def stat(self,**kwargs):stats.append(self.name);return self.entry.stat(**kwargs)
        @contextmanager
        def scan(fd):
            with original(fd) as entries:yield (Entry(e) for e in entries)
        with patch('artex.skills.file_listing.os.scandir',scan):
            with self.assertRaises(SkillListingLimit):self.make(max_entries=2).read('sample')
        self.assertCountEqual(stats,['a','b'])
    def test_final_current_root_replacement_is_detected(self):
        count=[0]
        def baseline():count[0]+=1
        self.hook=baseline;reader=self.make();reader.read('sample');final=count[0]
        count[0]=0;moved=self.root.with_name(self.root.name+'-old')
        self.addCleanup(lambda:__import__('shutil').rmtree(moved,ignore_errors=True))
        def replacing():
            count[0]+=1
            if count[0]==final:self.root.rename(moved);self.root.mkdir()
        self.hook=replacing
        with self.assertRaises(SkillListingConflict):reader.read('sample')

if __name__=='__main__':unittest.main()
