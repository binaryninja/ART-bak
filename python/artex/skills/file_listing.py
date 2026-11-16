"""Bounded, explicit-root skill file-name listing; never opens file contents.

Caller guard must exclude uncooperative renames, mounts and mutations. Descriptor
checks detect observed changes, not a filesystem-wide transactional snapshot.
"""
from dataclasses import dataclass
import os
import stat
from . import valid_skill_name, skill_rel_path
from ..tools.filesystem import ScopedFilesystem


class SkillListingUnavailable(RuntimeError): pass
class SkillListingLimit(ValueError): pass
class SkillListingConflict(RuntimeError): pass


@dataclass(frozen=True)
class SkillDirectoryScope:
    root: str
    device: int
    inode: int
    revision: str

    def validate(self):
        if type(self.root) is not str or not self.root.startswith('/') or len(self.root.encode())>4096:
            raise ValueError('Explicit root required')
        if any(p in ('.','..') for p in self.root.split('/')):
            raise ValueError('Canonical root required')
        if any(type(x) is not int or x<0 for x in (self.device,self.inode)):
            raise ValueError('Explicit root identity required')
        if type(self.revision) is not str or not self.revision or len(self.revision.encode())>256:
            raise ValueError('Revision required')


def _identity(st):
    return st.st_dev,st.st_ino,st.st_mode,st.st_mtime_ns,st.st_ctime_ns


class SkillFileListingReader:
    def __init__(self,scope,*,authorize,current,lifecycle_guard,max_entries=2000,
                 max_depth=32,max_text_bytes=65536):
        if type(scope) is not SkillDirectoryScope:raise ValueError('Exact scope required')
        scope.validate()
        if any(not callable(c) for c in (authorize,current,lifecycle_guard)):
            raise ValueError('Explicit skill-file authority required')
        for value,maximum in ((max_entries,10000),(max_depth,64),(max_text_bytes,1048576)):
            if type(value) is not int or not 1<=value<=maximum:raise ValueError('Invalid listing bound')
        self.scope,self.authorize,self.current,self.guard=scope,authorize,current,lifecycle_guard
        self.max_entries,self.max_depth,self.max_text_bytes=max_entries,max_depth,max_text_bytes
        self._pins=(scope,authorize,current,lifecycle_guard,max_entries,max_depth,max_text_bytes)
        self._scope=(scope.root,scope.device,scope.inode,scope.revision)

    def validate(self):
        s,a,c,g,e,d,b=self._pins
        if self.scope is not s or self.authorize is not a or self.current is not c or self.guard is not g:
            raise SkillListingUnavailable('Listing authority changed')
        if any(type(x) is not int for x in (self.max_entries,self.max_depth,self.max_text_bytes)) or (self.max_entries,self.max_depth,self.max_text_bytes)!=(e,d,b):
            raise SkillListingUnavailable('Listing bounds changed')
        s.validate()
        if (s.root,s.device,s.inode,s.revision)!=self._scope:
            raise SkillListingUnavailable('Listing scope changed')

    def _allow(self,name,disclosure):
        self.validate()
        if self._pins[2](self._pins[0],name) is not True:raise PermissionError('Skill listing scope expired')
        self.validate()
        if disclosure is not None:disclosure()
        self.validate()
        if self._pins[1](self._pins[0],name) is not True:raise PermissionError('Skill listing denied')
        self.validate()

    def _root_current(self):
        self.validate()
        scope=self._pins[0]
        with ScopedFilesystem(scope.root) as fs:
            st=os.fstat(fs._fd)
            if (st.st_dev,st.st_ino)!=(scope.device,scope.inode):
                raise SkillListingConflict('Root path changed')
        self.validate()

    def validate_disclosure(self,name,*,disclosure=None):
        if type(name) is not str or not valid_skill_name(name):raise ValueError('Invalid skill name')
        self.validate()
        with self._pins[3](self._pins[0],name):
            self._allow(name,disclosure)
            self._root_current()
        self._allow(name,disclosure)
        self._root_current()

    def read(self,name,*,disclosure=None):
        if type(name) is not str or not valid_skill_name(name):raise ValueError('Invalid skill name')
        self.validate()
        scope=self._pins[0]
        with self._pins[3](scope,name):
            self._allow(name,disclosure)
            with ScopedFilesystem(scope.root) as fs:
                root_before=os.fstat(fs._fd)
                if (root_before.st_dev,root_before.st_ino)!=(scope.device,scope.inode):
                    raise SkillListingConflict('Root changed')
                result=[];used=0;seen_entries=0
                def visit(parent,component,prefix,depth,expected=None):
                    nonlocal used,seen_entries
                    self._allow(name,disclosure)
                    meta=os.stat(component,dir_fd=parent,follow_symlinks=False)
                    if expected is not None and _identity(meta)!=expected:
                        raise SkillListingConflict('Directory entry changed')
                    if not stat.S_ISDIR(meta.st_mode) or meta.st_dev!=scope.device:
                        raise SkillListingUnavailable('Only same-device directories supported')
                    fd=os.open(component,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
                    try:
                        before=os.fstat(fd)
                        if _identity(before)!=_identity(meta):raise SkillListingConflict('Directory changed')
                        children=[]
                        with os.scandir(fd) as entries:
                            for entry in entries:
                                self._allow(name,disclosure)
                                seen_entries+=1
                                if seen_entries>self._pins[4]:raise SkillListingLimit('Entry limit exceeded')
                                relative=prefix+entry.name
                                if skill_rel_path(relative)!=relative:raise SkillListingUnavailable('Noncanonical skill path')
                                st=entry.stat(follow_symlinks=False)
                                directory=stat.S_ISDIR(st.st_mode)
                                if st.st_dev!=scope.device or not (directory or stat.S_ISREG(st.st_mode)) or (not directory and st.st_nlink!=1):
                                    raise SkillListingUnavailable('Symlinks, hardlinks and special files unsupported')
                                text=relative+'/' if directory else relative
                                used+=len(text.encode('utf-8'))
                                if used>self._pins[6]:raise SkillListingLimit('Listing text limit exceeded')
                                children.append((entry.name,text,directory,_identity(st)))
                        # WalkDir emits each directory before its sorted children.
                        for child,text,directory,entry_identity in sorted(children):
                            if len(result)>=self._pins[4]:raise SkillListingLimit('Entry limit exceeded')
                            result.append(text)
                            if directory:
                                if depth>=self._pins[5]:raise SkillListingLimit('Directory depth exceeded')
                                visit(fd,child,text,depth+1,entry_identity)
                        if _identity(os.fstat(fd))!=_identity(before) or _identity(os.stat(component,dir_fd=parent,follow_symlinks=False))!=_identity(before):
                            raise SkillListingConflict('Directory changed during listing')
                    finally:os.close(fd)
                visit(fs._fd,name,'',0)
                if _identity(os.fstat(fs._fd))!=_identity(root_before):raise SkillListingConflict('Root changed during listing')
                with ScopedFilesystem(scope.root) as current_root:
                    now=os.fstat(current_root._fd)
                    if (now.st_dev,now.st_ino)!=(scope.device,scope.inode):raise SkillListingConflict('Root path changed')
                self._allow(name,disclosure)
            self._allow(name,disclosure)
        self._allow(name,disclosure)
        self._root_current()
        return {'files':result}
