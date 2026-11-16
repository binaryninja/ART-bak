"""Bounded metadata catalog snapshots; inert text, no SDK execution or writes.

Requires cooperative writer/lifecycle exclusion. Final metadata rescans detect
observed changes, not atomic filesystem snapshots or arbitrary callback cycles.
"""
from dataclasses import dataclass,field
from weakref import WeakKeyDictionary
import hashlib,json,os,stat
from . import parse_skill,valid_skill_name,skill_rel_path
from .file_listing import SkillDirectoryScope
from .file_read import SkillFileReader,SkillFileSelection
from ..tools.filesystem import ScopedFilesystem

_PARSER=parse_skill
_CONTENT_NAMES=("read","validate","validate_disclosure","_allow","_open","_selection","_snapshot_check","_receipt_check")
_CONTENT_METHODS=tuple(getattr(SkillFileReader,n) for n in _CONTENT_NAMES)

class SkillCatalogUnavailable(RuntimeError):pass
class SkillCatalogConflict(RuntimeError):pass
class SkillCatalogLimit(ValueError):pass

@dataclass(frozen=True,eq=False)
class SkillCatalogSnapshot:
    payload: str
    _tree: tuple=field(repr=False)
    _contents: tuple=field(repr=False)

class SkillCatalogReader:
    def __init__(self,scope,content_reader,*,authorize_global,current,lifecycle_guard,
                 max_entries=4000,max_depth=32,max_text_bytes=1048576,max_metadata_bytes=4194304):
        if type(scope) is not SkillDirectoryScope or type(content_reader) is not SkillFileReader or content_reader.scope is not scope:
            raise ValueError('Exact shared catalog root and content reader required')
        scope.validate();content_reader.validate()
        if any(not callable(c) for c in (authorize_global,current,lifecycle_guard)):raise ValueError('Explicit catalog authority required')
        for value,maximum in ((max_entries,10000),(max_depth,64),(max_text_bytes,16777216),(max_metadata_bytes,16777216)):
            if type(value) is not int or not 1<=value<=maximum:raise ValueError('Invalid catalog limits')
        self.scope,self.content_reader=scope,content_reader
        self.authorize_global,self.current,self.guard=authorize_global,current,lifecycle_guard
        self._pins=(scope,content_reader,authorize_global,current,lifecycle_guard)
        self._scope=(scope.root,scope.device,scope.inode,scope.revision)
        self._limits=(max_entries,max_depth,max_text_bytes,max_metadata_bytes)
        self._issued=WeakKeyDictionary()
    def validate(self):
        if any(a is not b for a,b in zip((self.scope,self.content_reader,self.authorize_global,self.current,self.guard),self._pins)):
            raise SkillCatalogUnavailable('Catalog binding changed')
        s=self._pins[0];s.validate()
        if (s.root,s.device,s.inode,s.revision)!=self._scope or self.content_reader.scope is not s:raise SkillCatalogUnavailable('Catalog scope changed')
        if parse_skill is not _PARSER or any(getattr(getattr(self.content_reader,n),"__self__",None) is not self.content_reader or getattr(getattr(self.content_reader,n),"__func__",None) is not method for n,method in zip(_CONTENT_NAMES,_CONTENT_METHODS)):
            raise SkillCatalogUnavailable("Catalog content implementation changed")
        self.content_reader.validate()
    def _allow(self,disclosure):
        self.validate()
        if self._pins[3](self.scope) is not True:raise PermissionError('Catalog scope expired')
        self.validate()
        if disclosure is not None:disclosure()
        self.validate()
        if self._pins[2](self.scope) is not True:raise PermissionError('Catalog denied')
        self.validate()
    def _scan(self):
        """Code-owned bounded scan, no policy callbacks or file-content reads."""
        self.validate();scope=self.scope;count=used=0;tree=[];nodes={}
        with ScopedFilesystem(scope.root) as fs:
            root=os.fstat(fs._fd)
            if (root.st_dev,root.st_ino)!=(scope.device,scope.inode):raise SkillCatalogConflict('Catalog root changed')
            tree.append(('',fs._stat(root)))
            def visit(fd,prefix,depth,owner):
                nonlocal count,used
                before=os.fstat(fd);children=[]
                with os.scandir(fd) as entries:
                    for entry in entries:
                        count+=1
                        if count>self._limits[0]:raise SkillCatalogLimit('Catalog entry bound exceeded')
                        path=prefix+entry.name
                        if skill_rel_path(path)!=path:raise SkillCatalogUnavailable('Noncanonical catalog path')
                        used+=len(path.encode('utf-8'))
                        if used>self._limits[2]:raise SkillCatalogLimit('Catalog text bound exceeded')
                        meta=entry.stat(follow_symlinks=False);directory=stat.S_ISDIR(meta.st_mode)
                        if meta.st_dev!=scope.device or not (directory or stat.S_ISREG(meta.st_mode)) or (not directory and meta.st_nlink!=1):
                            raise SkillCatalogUnavailable('Unsupported catalog entry')
                        children.append((entry.name,path,directory,fs._stat(meta)))
                for name,path,directory,fingerprint in sorted(children):
                    tree.append((path,fingerprint))
                    selected=owner
                    if depth==0 and directory:
                        if not valid_skill_name(name):raise SkillCatalogUnavailable('Invalid catalog directory')
                        selected=name;nodes[name]=[]
                    elif owner is not None:nodes[owner].append(path[len(owner)+1:]+('/' if directory else ''))
                    if directory:
                        if depth>=self._limits[1]:raise SkillCatalogLimit('Catalog depth bound exceeded')
                        child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                        try:
                            if fs._stat(os.fstat(child))!=fingerprint:raise SkillCatalogConflict('Catalog directory changed')
                            visit(child,path+'/',depth+1,selected)
                            if fs._stat(os.stat(name,dir_fd=fd,follow_symlinks=False))!=fingerprint:raise SkillCatalogConflict('Catalog directory changed')
                        finally:os.close(child)
                if fs._stat(os.fstat(fd))!=fs._stat(before):raise SkillCatalogConflict('Catalog directory changed')
            visit(fs._fd,'',0,None)
        with ScopedFilesystem(scope.root) as check:
            if fs._stat(os.fstat(check._fd))!=fs._stat(root):raise SkillCatalogConflict('Catalog root changed')
        self.validate()
        return tuple(tree),nodes
    def _snapshot_check(self,snapshot):
        self.validate()
        if type(snapshot) is not SkillCatalogSnapshot or type(snapshot.payload) is not str:raise SkillCatalogUnavailable('Original catalog snapshot required')
        sealed=self._issued.get(snapshot)
        if sealed is None or sealed!=(hashlib.sha256(snapshot.payload.encode()).digest(),snapshot._tree,tuple((id(q),id(s)) for q,s in snapshot._contents)):
            raise SkillCatalogUnavailable('Catalog snapshot changed')
    def _receipt_check(self,snapshot):
        self._snapshot_check(snapshot)
        tree,_=self._scan()
        if tree!=snapshot._tree:raise SkillCatalogConflict('Catalog tree changed')
        for selection,content in snapshot._contents:
            self.content_reader._snapshot_check(selection,content)
            self.content_reader._receipt_check(selection,(selection.name,selection.file),content.receipt)
        self._snapshot_check(snapshot)
    def validate_disclosure(self,snapshot,*,disclosure=None):
        self._snapshot_check(snapshot)
        with self._pins[4](self.scope):
            for selection,content in snapshot._contents:
                self.content_reader.validate_disclosure(selection,content,disclosure=lambda:self._allow(disclosure))
            self._allow(disclosure)
        # All callback/guard exits precede code-owned receipt checks.
        self._allow(disclosure)
        # Catalog-current observers may revoke child content grants. Run child
        # current/authority gates after that phase, then request/global and a
        # final content-authority-only sweep (no more current callbacks).
        for selection,content in snapshot._contents:
            self.content_reader._allow(selection,(selection.name,selection.file),None)
        if disclosure is not None:disclosure()
        self.validate()
        if self._pins[2](self.scope) is not True:raise PermissionError('Catalog denied')
        self.validate()
        self._content_authority_check(snapshot)
        self._receipt_check(snapshot)
    def _content_authority_check(self,snapshot):
        self._snapshot_check(snapshot)
        for selection,content in snapshot._contents:
            if self.content_reader._pins[1](self.scope,selection) is not True:raise PermissionError('Content denied')
            self.validate()
        self._snapshot_check(snapshot)
    def read(self,*,disclosure=None):
        self.validate()
        with self._pins[4](self.scope):
            self._allow(disclosure);tree,nodes=self._scan();rows=[];contents=[];total=0
            for name,files in nodes.items():
                self._allow(disclosure)
                row={'name':name,'files':files,'calls':0,'tasks':0,'usage_agents':[]}
                if 'SKILL.md' in files:
                    selection=SkillFileSelection(name,'SKILL.md')
                    content=self.content_reader.read(selection,disclosure=lambda:self._allow(disclosure))
                    total+=len(content.content.encode('utf-8'))
                    if total>self._limits[3]:raise SkillCatalogLimit('Catalog metadata bound exceeded')
                    try:metadata=parse_skill(content.content)
                    except (ValueError,TypeError,AttributeError):raise SkillCatalogUnavailable('Unsupported skill metadata') from None
                    for field in ('description','license','compatibility','mcps'):
                        value=getattr(metadata,field)
                        if value:row[field]=list(value) if field=='mcps' else value
                    contents.append((selection,content))
                rows.append(row)
            payload=json.dumps({'skills':rows},ensure_ascii=False,separators=(',',':'))
            if len(payload.encode('utf-8'))>self._limits[2]:raise SkillCatalogLimit('Catalog response bound exceeded')
            snapshot=SkillCatalogSnapshot(payload,tree,tuple(contents))
            self._issued[snapshot]=(hashlib.sha256(payload.encode()).digest(),tree,tuple((id(q),id(s)) for q,s in contents))
        self.validate_disclosure(snapshot,disclosure=disclosure)
        return snapshot
