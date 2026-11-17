"""Explicit POSIX advisory process lease for a trusted local development directory.

Acquire before constructing the runtime. All cooperating launchers must use the
same file. This is not a database/distributed fence or defense against a directory
owner replacing files. Never unlink the lock file; never fork while holding it.
"""
import fcntl
import os
import stat
import sys
from contextlib import contextmanager
from .transcripts import TranscriptStore
from functools import wraps
from threading import RLock


class LeaseUnavailable(RuntimeError): pass


_NAMESPACE_LOCK='.artex-runtime.lock'
_TRANSCRIPT_OPEN=TranscriptStore._open_root_directory
_TRANSCRIPT_BIND=TranscriptStore._bind_runtime_lease
_TRANSCRIPT_METHODS=(TranscriptStore._root,TranscriptStore._locked,TranscriptStore._locked_native,
                     TranscriptStore._open_root_directory,TranscriptStore._runtime_owner,TranscriptStore._runtime_io)


def _locked(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return call


class RuntimeLease:
    def __init__(self, path):
        if type(path) is not str or not os.path.isabs(path) or os.path.basename(path) in ('', '.', '..'):
            raise ValueError('An explicit absolute lease path is required')
        self._lock = RLock()
        self.path = path
        self._fd = None
        self._directory = None
        self._pid = None
        self._identity = None
        self._holder = None
        self._release_permit = None
        self._namespace_store = self._namespace_root = self._namespace_path = None
        self._namespace_active = 0

    @classmethod
    def for_transcripts(cls,store):
        """Bind one unused original store to its fixed canonical local lock.

        Inert filesystem-wise. Explicitly acquire before assembling resources.
        Binding is one-shot, not a grant, task adoption or recovery capability.
        """
        if (cls is not RuntimeLease or type(store) is not TranscriptStore
                or type(store.root) is not str or not store.root.startswith('/')
                or any(part in ('.','..') for part in store.root.split('/'))):
            raise ValueError('Exact native transcript store and absolute namespace required')
        path=os.path.join(os.path.normpath(store.root),_NAMESPACE_LOCK)
        lease=cls(path)
        lease._namespace_store=store;lease._namespace_root=store.root;lease._namespace_path=path
        _TRANSCRIPT_BIND(store,lease)
        return lease

    def _namespace_binding(self):
        store=self._namespace_store
        if store is None:return
        if (type(store) is not TranscriptStore or store._runtime_lease is not self
                or store._runtime_binding is not self
                or type(store.root) is not str or store.root != self._namespace_root
                or type(self.path) is not str or self.path != self._namespace_path):
            raise LeaseUnavailable('Transcript namespace ownership changed')
        for name,method in zip(('_root','_locked','_locked_native','_open_root_directory','_runtime_owner','_runtime_io'),_TRANSCRIPT_METHODS):
            bound=getattr(store,name,None)
            if getattr(bound,'__self__',None) is not store or getattr(bound,'__func__',None) is not method:
                raise LeaseUnavailable('Original transcript namespace methods required')

    @_locked
    def validate_runtime_store(self,store):
        self.validate()
        if self._namespace_store is not None and store is not self._namespace_store:
            raise LeaseUnavailable('Runtime requires the exact lease-bound transcript store')
        return True

    @_locked
    def _check_transcript_root(self,store,descriptor):
        self.validate_runtime_store(store)
        opened=os.fstat(descriptor);owned=os.fstat(self._directory)
        self._safe(opened,True)
        if (opened.st_dev,opened.st_ino)!=(owned.st_dev,owned.st_ino):
            raise LeaseUnavailable('Transcript root differs from owned namespace')

    @contextmanager
    def _transcript_io(self,store):
        # No lease mutex spans transcript locks, filesystem work or callbacks.
        with self._lock:
            if self._namespace_store is None:raise LeaseUnavailable('Bound namespace required')
            self.validate_runtime_store(store)
            self._namespace_active+=1
        try:
            yield
        finally:
            original=sys.exc_info()[0] is not None
            failure=None
            with self._lock:
                try:self.validate_runtime_store(store)
                except BaseException as error:failure=error
                finally:self._namespace_active-=1
            if failure is not None and not original:raise failure

    @staticmethod
    def _safe(info, directory=False):
        kind = stat.S_ISDIR if directory else stat.S_ISREG
        if not kind(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise LeaseUnavailable('Trusted owner-controlled lease storage required')
        if not directory and (info.st_nlink != 1 or info.st_mode & 0o077):
            raise LeaseUnavailable('Private single-link lease file required')

    @_locked
    def acquire(self):
        if self._fd is not None:
            self.validate()
            return False
        directory = descriptor = None
        try:
            self._namespace_binding()
            directory = (_TRANSCRIPT_OPEN(self._namespace_store,namespace=True) if self._namespace_store is not None else
                         os.open(os.path.dirname(self.path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC))
            self._safe(os.fstat(directory), True)
            descriptor = os.open(os.path.basename(self.path), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                                 0o600, dir_fd=directory)
            info = os.fstat(descriptor)
            self._safe(info)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._fd, self._directory, self._pid = descriptor, directory, os.getpid()
            self._identity = (info.st_dev, info.st_ino)
            self.validate()
            return True
        except BaseException:
            self._fd = self._directory = self._pid = self._identity = None
            if descriptor is not None: os.close(descriptor)
            if directory is not None: os.close(directory)
            raise LeaseUnavailable('Runtime lease could not be acquired') from None

    @_locked
    def validate(self):
        try:
            if self._fd is None or self._pid != os.getpid(): raise LeaseUnavailable()
            info = os.fstat(self._fd)
            self._safe(info)
            directory = os.fstat(self._directory)
            self._safe(directory, True)
            current = os.stat(os.path.dirname(self.path), follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (directory.st_dev, directory.st_ino): raise LeaseUnavailable()
            current = os.stat(os.path.basename(self.path), dir_fd=self._directory, follow_symlinks=False)
            if (info.st_dev, info.st_ino) != self._identity or (current.st_dev, current.st_ino) != self._identity:
                raise LeaseUnavailable()
            self._safe(current)
            self._namespace_binding()
            if self._namespace_store is not None:
                root=_TRANSCRIPT_OPEN(self._namespace_store,namespace=True)
                try:
                    live=os.fstat(root)
                    if (live.st_dev,live.st_ino)!=(directory.st_dev,directory.st_ino):raise LeaseUnavailable()
                finally:os.close(root)
        except Exception:
            raise LeaseUnavailable('Runtime lease identity unavailable') from None
        return True

    @_locked
    def claim(self, owner, *, guarded=False):
        self.validate()
        if owner is None or self._holder is not None or self._namespace_active:
            raise LeaseUnavailable('Lease already belongs to a runtime session')
        if type(guarded) is not bool:raise ValueError('Explicit guarded ownership required')
        self._holder = owner
        self._release_permit = object() if guarded else None
        return self._release_permit

    @_locked
    def transfer(self, owner, replacement, *, guarded=False):
        """Transfer an existing claim without an unlocked/unclaimed interval."""
        self.validate()
        if (owner is None or replacement is None or self._holder is not owner
                or self._release_permit is not None or self._namespace_active):
            raise LeaseUnavailable('Exact current lease owner required')
        if type(guarded) is not bool:raise ValueError('Explicit guarded ownership required')
        self._holder = replacement
        self._release_permit = object() if guarded else None
        return self._release_permit

    @_locked
    def release(self, *, owner=None, permit=None):
        # A renamed/unlinked path is a validation failure, but the original
        # descriptor must still be releasable after the runtime has drained.
        if self._fd is None: return False
        if self._holder is not None and owner is not self._holder:
            raise LeaseUnavailable('Only the owning drained session may release the lease')
        if self._pid != os.getpid(): raise LeaseUnavailable('Lease belongs to another process')
        if self._release_permit is not None and permit is not self._release_permit:
            raise LeaseUnavailable('Original session drain permit required')
        if self._namespace_active:raise LeaseUnavailable('Transcript I/O is still owned')
        os.close(self._fd)
        self._fd = None
        os.close(self._directory)
        self._directory = self._pid = self._identity = None
        self._holder = self._release_permit = None
        return True
