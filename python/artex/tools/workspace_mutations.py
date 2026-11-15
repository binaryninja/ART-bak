"""Bounded descriptor-relative mkdir/empty-directory-or-file deletion subset.

No recursive operations, parent creation, symlinks, hardlinked files or special
files. All filesystem access uses ScopedFilesystem's no-follow root/components.
Host MUST exclusively guard tree writes/renames/mount changes; stat/unlink is not
an OS CAS against hostile writers. New directories0700; existing directories are
idempotent. Removal is permanent; caller supplies exact action-time authority.
"""
from dataclasses import dataclass
import errno,os,stat
from .filesystem import ScopedFilesystem,FilesystemError,FilesystemConflict

class WorkspaceMutationUnsupported(RuntimeError):pass
class WorkspaceMutationUncertain(RuntimeError):pass

@dataclass(frozen=True)
class MutationTarget:
    action:str
    path:str
    parent_identity:tuple
    target_fingerprint:tuple|None
    kind:str

class WorkspaceMutations:
    def __init__(self,root,*,root_device,root_inode):
        self._root=(root_device,root_inode);self.fs=ScopedFilesystem(root)
        try:self._check_root()
        except BaseException:self.fs.close();raise
    def __enter__(self):return self
    def __exit__(self,exc_type,exc,traceback):
        try:self.fs.close()
        except BaseException:
            # Preserve the primary mutation outcome, particularly uncertainty.
            # A close failure after otherwise successful mutation propagates so
            # the HTTP owner still reports its recorded effect as uncertain.
            if exc is None:raise
        return False
    def _check_root(self):
        info=os.fstat(self.fs._fd)
        if (info.st_dev,info.st_ino)!=self._root:raise FilesystemConflict('Workspace root changed')
    def _parts(self,path):
        if type(path) is not str or len(path.encode())>4096:raise FilesystemError('Invalid path')
        return self.fs._parts(path)
    def _target(self,action,path,fd,name):
        parent=os.fstat(fd)
        if parent.st_dev!=self._root[0]:raise FilesystemError('Mounted workspace traversal unsupported')
        try:target=os.stat(name,dir_fd=fd,follow_symlinks=False)
        except FileNotFoundError:
            if action=='delete':raise
            return MutationTarget(action,path,(parent.st_dev,parent.st_ino),None,'missing')
        if target.st_dev!=self._root[0]:raise FilesystemError('Mounted workspace target unsupported')
        if stat.S_ISDIR(target.st_mode):kind='directory'
        elif stat.S_ISREG(target.st_mode) and target.st_nlink==1:kind='file'
        else:raise FilesystemError('Unsupported symlink, hardlink or special target')
        if action=='mkdir' and kind!='directory':raise FilesystemConflict('Directory target is a file')
        return MutationTarget(action,path,(parent.st_dev,parent.st_ino),self.fs._stat(target),kind)
    def prepare(self,action,path):
        if action not in ('mkdir','delete'):raise ValueError('Unsupported mutation')
        self._check_root();parts=self._parts(path)
        with self.fs._directory(parts[:-1]) as fd:return self._target(action,path,fd,parts[-1])
    def apply(self,target,context):
        if type(target) is not MutationTarget:raise ValueError('Exact immutable target required')
        changed=False;attempted=False
        try:
            context.check();self._check_root();parts=self._parts(target.path)
            with self.fs._directory(parts[:-1]) as fd:
                if self._target(target.action,target.path,fd,parts[-1])!=target:raise FilesystemConflict('Workspace target changed')
                context.check();self._check_root()
                # Repeat target comparison after approval/current callbacks.
                if self._target(target.action,target.path,fd,parts[-1])!=target:raise FilesystemConflict('Workspace target changed')
                if target.action=='mkdir':
                    if target.kind=='directory':return False
                    attempted=True
                    os.mkdir(parts[-1],mode=0o700,dir_fd=fd);changed=True
                elif target.action=='delete':
                    try:
                        attempted=True
                        if target.kind=='directory':os.rmdir(parts[-1],dir_fd=fd)
                        else:os.unlink(parts[-1],dir_fd=fd)
                    except OSError as exc:
                        if exc.errno in (errno.ENOTEMPTY,errno.EEXIST):
                            attempted=False  # rmdir reports no removal for a nonempty directory.
                            raise WorkspaceMutationUnsupported('Recursive directory deletion unavailable') from None
                        raise
                    changed=True
                else:raise ValueError('Unsupported mutation')
                os.fsync(fd);context.check();self._check_root()
            return changed
        except BaseException:
            if changed or attempted:raise WorkspaceMutationUncertain('Mutation may have occurred; durability or authority requires reconciliation') from None
            raise
