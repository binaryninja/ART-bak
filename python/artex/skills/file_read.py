"""Explicitly authorized, bounded skill file content snapshots, never execution.

The host must exclude uncooperative writes/renames/mounts. Metadata receipts
catch observed changes; they are not OS snapshots or arbitrary-writer CAS.
"""
from contextlib import ExitStack
from dataclasses import dataclass, field
import os
import stat
import hashlib
from weakref import WeakKeyDictionary
from . import valid_skill_name, skill_rel_path
from .file_listing import SkillDirectoryScope
from ..tools.filesystem import ScopedFilesystem, FilesystemConflict


class SkillFileUnavailable(RuntimeError): pass
class SkillFileConflict(RuntimeError): pass


@dataclass(frozen=True)
class SkillFileSelection:
    name: str
    file: str

    def validate(self):
        if type(self.name) is not str or not valid_skill_name(self.name):
            raise ValueError('Invalid skill name')
        if type(self.file) is not str or skill_rel_path(self.file)!=self.file:
            raise ValueError('Canonical relative file required')


@dataclass(frozen=True, eq=False)
class SkillFileReceipt:
    _issuer: object = field(repr=False)
    selection: tuple
    scope: tuple
    directories: tuple
    file_stat: tuple
    raw_digest: str
    content_digest: str


@dataclass(frozen=True)
class SkillFileSnapshot:
    content: str
    file: str
    receipt: SkillFileReceipt = field(repr=False)


class SkillFileReader:
    def __init__(self,scope,*,authorize,current,lifecycle_guard,max_bytes=2097152,
                 max_depth=32):
        if type(scope) is not SkillDirectoryScope:raise ValueError('Exact root scope required')
        scope.validate()
        if any(not callable(c) for c in (authorize,current,lifecycle_guard)):
            raise ValueError('Explicit content-read authority required')
        if type(max_bytes) is not int or not 1<=max_bytes<=20971520:
            raise ValueError('Invalid content byte bound')
        if type(max_depth) is not int or not 1<=max_depth<=64:
            raise ValueError('Invalid directory depth bound')
        self.scope,self.authorize,self.current,self.guard=scope,authorize,current,lifecycle_guard
        self.max_bytes,self.max_depth=max_bytes,max_depth
        self._pins=(scope,authorize,current,lifecycle_guard,max_bytes,max_depth)
        self._scope=(scope.root,scope.device,scope.inode,scope.revision)
        self._issuer=object()
        self._issued=WeakKeyDictionary()

    def validate(self):
        s,a,c,g,b,d=self._pins
        if self.scope is not s or self.authorize is not a or self.current is not c or self.guard is not g:
            raise SkillFileUnavailable('Content authority changed')
        if type(self.max_bytes) is not int or type(self.max_depth) is not int or (self.max_bytes,self.max_depth)!=(b,d):
            raise SkillFileUnavailable('Content limits changed')
        s.validate()
        if (s.root,s.device,s.inode,s.revision)!=self._scope:
            raise SkillFileUnavailable('Content scope changed')

    def _selection(self,selection,identity):
        self.validate()
        if type(selection) is not SkillFileSelection:raise ValueError('Exact content selection required')
        selection.validate()
        if (selection.name,selection.file)!=identity:raise SkillFileUnavailable('Selection changed')
        if len(selection.file.split('/'))>self._pins[5]:raise ValueError('Content path depth exceeded')

    def _allow(self,selection,identity,disclosure):
        self._selection(selection,identity)
        if self._pins[2](self._pins[0],selection) is not True:raise PermissionError('Content scope expired')
        self._selection(selection,identity)
        if disclosure is not None:disclosure()
        self._selection(selection,identity)
        if self._pins[1](self._pins[0],selection) is not True:raise PermissionError('Content read denied')
        self._selection(selection,identity)

    def _open(self,stack,selection):
        scope=self._pins[0]
        fs=stack.enter_context(ScopedFilesystem(scope.root,max_bytes=self._pins[4]))
        root=os.fstat(fs._fd)
        if (root.st_dev,root.st_ino)!=(scope.device,scope.inode):raise SkillFileConflict('Root changed')
        parent=fs._fd;directories=[]
        parts=[selection.name]+selection.file.split('/')
        for component in parts[:-1]:
            before=os.stat(component,dir_fd=parent,follow_symlinks=False)
            if not stat.S_ISDIR(before.st_mode) or before.st_dev!=scope.device:
                raise SkillFileUnavailable('Only same-device directories supported')
            fd=os.open(component,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
            stack.callback(os.close,fd)
            actual=os.fstat(fd)
            if fs._stat(before)!=fs._stat(actual):raise SkillFileConflict('Directory changed')
            directories.append(fs._stat(actual));parent=fd
        return fs,parent,parts[-1],tuple(directories)

    def _receipt_check(self,selection,identity,receipt):
        self._selection(selection,identity)
        if type(receipt) is not SkillFileReceipt or receipt._issuer is not self._issuer or receipt.selection!=identity or receipt.scope!=self._scope:
            raise SkillFileUnavailable('Original content receipt required')
        sealed=self._issued.get(receipt)
        if sealed is None or sealed!=(receipt.selection,receipt.scope,receipt.directories,receipt.file_stat,receipt.raw_digest,receipt.content_digest):
            raise SkillFileUnavailable('Unissued or changed content receipt')
        with ExitStack() as stack:
            fs,parent,name,directories=self._open(stack,selection)
            actual=os.stat(name,dir_fd=parent,follow_symlinks=False)
            if not stat.S_ISREG(actual.st_mode) or actual.st_nlink!=1 or actual.st_dev!=self.scope.device:
                raise SkillFileUnavailable('Only regular single-link content supported')
            if directories!=receipt.directories or fs._stat(actual)!=receipt.file_stat:
                raise SkillFileConflict('Content snapshot changed')
        self._selection(selection,identity)

    def _snapshot_check(self,selection,snapshot):
        if type(snapshot) is not SkillFileSnapshot or type(snapshot.content) is not str or snapshot.file!=selection.file:
            raise SkillFileUnavailable('Original content snapshot required')
        data=snapshot.content.encode('utf-8')
        if len(data)>3*self._pins[4] or type(snapshot.receipt) is not SkillFileReceipt or hashlib.sha256(data).hexdigest()!=snapshot.receipt.content_digest:
            raise SkillFileUnavailable('Content snapshot changed')

    def validate_disclosure(self,selection,snapshot,*,disclosure=None):
        if type(selection) is not SkillFileSelection:raise ValueError('Exact content selection required')
        identity=(selection.name,selection.file);self._selection(selection,identity)
        self._snapshot_check(selection,snapshot)
        receipt=snapshot.receipt
        with self._pins[3](self._pins[0],selection):
            self._allow(selection,identity,disclosure)
            self._snapshot_check(selection,snapshot)
            self._receipt_check(selection,identity,receipt)
        self._allow(selection,identity,disclosure)
        self._snapshot_check(selection,snapshot)
        if snapshot.receipt is not receipt:raise SkillFileUnavailable('Snapshot receipt changed')
        # Code-owned metadata validation follows final policy callbacks.
        self._receipt_check(selection,identity,receipt)

    def read(self,selection,*,disclosure=None):
        if type(selection) is not SkillFileSelection:raise ValueError('Exact content selection required')
        identity=(selection.name,selection.file);self._selection(selection,identity)
        with self._pins[3](self._pins[0],selection):
            self._allow(selection,identity,disclosure)
            with ExitStack() as stack:
                fs,parent,name,directories=self._open(stack,selection)
                before=os.stat(name,dir_fd=parent,follow_symlinks=False)
                if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or before.st_dev!=self.scope.device:
                    raise SkillFileUnavailable('Only regular single-link content supported')
                try:data,version=fs._read(parent,name)
                except FilesystemConflict:raise SkillFileConflict('Content changed during read') from None
                if fs._stat(before)!=version[0]:raise SkillFileConflict('Content replaced before read')
                # Go converts []byte to string; JSON replaces invalid UTF-8.
                # Go's JSON encoder replaces each invalid UTF-8 byte, including
                # every byte of an incomplete sequence, with U+FFFD.
                content=data.decode('utf-8',errors='surrogateescape').translate(
                    {code:0xfffd for code in range(0xdc80,0xdd00)})
                receipt=SkillFileReceipt(self._issuer,identity,self._scope,directories,version[0],version[1].hex(),hashlib.sha256(content.encode('utf-8')).hexdigest())
                self._issued[receipt]=(identity,self._scope,directories,version[0],receipt.raw_digest,receipt.content_digest)
            self._allow(selection,identity,disclosure)
            self._receipt_check(selection,identity,receipt)
        self._allow(selection,identity,disclosure)
        self._receipt_check(selection,identity,receipt)
        return SkillFileSnapshot(content,selection.file,receipt)
