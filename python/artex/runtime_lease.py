"""Explicit POSIX advisory process lease for a trusted local development directory.

Acquire before constructing the runtime. All cooperating launchers must use the
same file. This is not a database/distributed fence or defense against a directory
owner replacing files. Never unlink the lock file; never fork while holding it.
"""
import fcntl
import os
import stat
from functools import wraps
from threading import RLock


class LeaseUnavailable(RuntimeError): pass


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
            directory = os.open(os.path.dirname(self.path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
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
        except Exception:
            raise LeaseUnavailable('Runtime lease identity unavailable') from None
        return True

    @_locked
    def claim(self, owner):
        self.validate()
        if owner is None or self._holder is not None:
            raise LeaseUnavailable('Lease already belongs to a runtime session')
        self._holder = owner

    @_locked
    def transfer(self, owner, replacement):
        """Transfer an existing claim without an unlocked/unclaimed interval."""
        self.validate()
        if owner is None or replacement is None or self._holder is not owner:
            raise LeaseUnavailable('Exact current lease owner required')
        self._holder = replacement

    @_locked
    def release(self, *, owner=None):
        # A renamed/unlinked path is a validation failure, but the original
        # descriptor must still be releasable after the runtime has drained.
        if self._fd is None: return False
        if self._holder is not None and owner is not self._holder:
            raise LeaseUnavailable('Only the owning drained session may release the lease')
        if self._pid != os.getpid(): raise LeaseUnavailable('Lease belongs to another process')
        os.close(self._fd)
        self._fd = None
        os.close(self._directory)
        self._directory = self._pid = self._identity = None
        self._holder = None
        return True
