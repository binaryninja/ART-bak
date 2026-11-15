"""Scoped UTF-8 subset of Norma v0.4.3 tool/files.go and tool/ls.go.

Exact tool names/arguments: Read(file_path,offset?,limit?), Write(file_path,
content), Edit(file_path,old_string,new_string,replace_all?), LS(path?). Relative
paths only, resolved against an explicitly supplied existing absolute root, NOT
cwd/home. No automatic parent creation, binary output, encoding conversion,
SDK capture spilling, glob, shell, images or Windows support. Unknown fields
are rejected. LS defaults only to the explicit root. Reads cap whole file bytes.

Use tools() with BoundedAgent: its mandatory Grant gate still applies to EVERY
call. This scope never grants permission or expands when a gate approves.
Callbacks themselves are trusted-host primitives, not an authorization API.

POSIX no-follow directory descriptors prevent symlink traversal. Hard-linked
files and special files are rejected. Atomic same-directory replacement and a
precommit stat/content fingerprint detect observed conflicts; a process lock
serializes this instance. This is NOT an OS compare-and-swap against arbitrary
external writers: the host must exclusively control the root tree and prevent
uncooperative renames/mount changes/writes during operations. Existing ordinary rwx permission bits are preserved; setuid/setgid/sticky bits
are stripped. New files are mode 0600. Ownership/group must match the newly
created replacement; mismatches are rejected without chown. Any existing or
inherited ACL/xattr is rejected, not silently discarded. Platforms without
fd-based xattr inspection cannot mutate files. Timestamps/inode change on replace.
Cancellation before commit leaves no target change; after rename a change is
already durable even if later cancellation is observed. No rollback/retry.
"""
from contextlib import contextmanager
import hashlib
import os
import stat
import threading
import uuid
from ..agent.runtime import Tool


class FilesystemError(ValueError):
    pass


class FilesystemConflict(FilesystemError):
    pass


class ScopedFilesystem:
    def __init__(self, root, *, max_bytes=1024 * 1024, max_entries=2000,
                 max_output_bytes=65536):
        if os.name != 'posix' or not hasattr(os, 'O_NOFOLLOW'):
            raise FilesystemError('POSIX no-follow filesystem support required')
        if not isinstance(root, str) or not root.startswith('/'):
            raise FilesystemError('An explicit existing absolute root is required')
        for n in (max_bytes, max_entries, max_output_bytes):
            if type(n) is not int or n <= 0:
                raise FilesystemError('Limits must be positive integers')
        self.max_bytes, self.max_entries, self.max_output_bytes = max_bytes, max_entries, max_output_bytes
        self._lock = threading.RLock()
        # Walk the configured root as well: symlink ancestors are not allowed.
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in root.split('/')[1:]:
                if not part:
                    continue
                if part in ('.', '..'):
                    raise FilesystemError('Root must be canonical')
                nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = nxt
            self._fd = fd
        except BaseException:
            os.close(fd)
            raise

    def close(self):
        with self._lock:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _parts(self, path, directory=False):
        if directory and path in ('', '.'):
            return []
        if not isinstance(path, str) or not path or path.startswith('/') or '\\' in path or any(ord(c) < 32 for c in path):
            raise FilesystemError('Invalid relative path')
        parts = path.split('/')
        if any(p in ('', '.', '..') for p in parts):
            raise FilesystemError('Path traversal is not allowed')
        path.encode('utf-8')
        return parts

    @contextmanager
    def _directory(self, parts):
        if self._fd is None:
            raise FilesystemError('Filesystem is closed')
        fd = os.dup(self._fd)
        try:
            for part in parts:
                nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = nxt
            yield fd
        finally:
            os.close(fd)

    def _read(self, fd, name):
        f = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            before = os.fstat(f)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise FilesystemError('Only regular single-link files are supported')
            if before.st_size > self.max_bytes:
                raise FilesystemError('File exceeds byte limit')
            chunks, size = [], 0
            while True:
                chunk = os.read(f, min(65536, self.max_bytes + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > self.max_bytes:
                    raise FilesystemError('File exceeds byte limit')
            after = os.fstat(f)
            data = b''.join(chunks)
            if self._stat(before) != self._stat(after):
                raise FilesystemConflict('File changed during read')
            return data, (self._stat(after), hashlib.sha256(data).digest())
        finally:
            os.close(f)

    @staticmethod
    def _stat(s):
        return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_nlink, s.st_mode, s.st_uid, s.st_gid

    def _version(self, fd, name):
        try:
            return self._read(fd, name)[1]
        except FileNotFoundError:
            return None

    @staticmethod
    def _check_metadata(fd):
        try:
            attrs = os.listxattr(fd)
        except (AttributeError, OSError, TypeError):
            raise FilesystemError('Filesystem metadata inspection unavailable') from None
        if attrs:
            raise FilesystemError('ACL and extended attribute preservation unsupported')

    def _replace(self, ctx, fd, name, data, expected):
        temp = '.artex-write-' + uuid.uuid4().hex
        f = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        try:
            with os.fdopen(f, 'wb') as out:
                mode = 0o600
                replacement = os.fstat(out.fileno())
                self._check_metadata(out.fileno())
                if expected is not None:
                    old = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                    try:
                        existing = os.fstat(old)
                        if self._stat(existing) != expected[0]:
                            raise FilesystemConflict('File metadata changed before commit')
                        self._check_metadata(old)
                        if (existing.st_uid, existing.st_gid) != (replacement.st_uid, replacement.st_gid):
                            raise FilesystemError('Ownership preservation is unsupported')
                        mode = stat.S_IMODE(existing.st_mode) & 0o777
                    finally:
                        os.close(old)
                out.write(data)
                out.flush()
                os.fchmod(out.fileno(), mode)
                os.fsync(out.fileno())
            ctx.check()
            if self._version(fd, name) != expected:
                raise FilesystemConflict('File changed before commit')
            ctx.check()
            os.replace(temp, name, src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
        finally:
            try:
                os.unlink(temp, dir_fd=fd)
            except FileNotFoundError:
                pass

    def _text(self, data):
        if b'\0' in data:
            raise FilesystemError('Binary files are unsupported')
        try:
            return data.decode('utf-8')
        except UnicodeError:
            raise FilesystemError('Only UTF-8 text is supported') from None

    def _validate(self, name, args):
        fields = {'Read': {'file_path', 'offset', 'limit'}, 'Write': {'file_path', 'content'},
                  'Edit': {'file_path', 'old_string', 'new_string', 'replace_all'}, 'LS': {'path'}}[name]
        required = {'Read': {'file_path'}, 'Write': {'file_path', 'content'},
                    'Edit': {'file_path', 'old_string', 'new_string'}, 'LS': set()}[name]
        if not isinstance(args, dict) or set(args) - fields or required - set(args):
            raise FilesystemError('Invalid tool arguments')
        self._parts(args.get('path', '') if name == 'LS' else args['file_path'], name == 'LS')
        for key in ('offset', 'limit'):
            if key in args and (type(args[key]) is not int or args[key] < 0):
                raise FilesystemError('Invalid read range')
        if 'replace_all' in args and type(args['replace_all']) is not bool:
            raise FilesystemError('replace_all must be boolean')
        for key in ('content', 'old_string', 'new_string'):
            if key in args:
                if not isinstance(args[key], str) or '\0' in args[key] or len(args[key].encode('utf-8')) > self.max_bytes:
                    raise FilesystemError('Invalid text or byte limit exceeded')
        if name == 'Edit' and (not args['old_string'] or args['old_string'] == args['new_string']):
            raise FilesystemError('Edit requires nonempty distinct old_string')
        return True

    def _call(self, name, ctx, args):
        self._validate(name, args)
        ctx.check()
        with self._lock:
            path = args.get('path', '') if name == 'LS' else args['file_path']
            parts = self._parts(path, name == 'LS')
            with self._directory(parts if name == 'LS' else parts[:-1]) as fd:
                if name == 'LS':
                    entries = []
                    with os.scandir(fd) as iterator:
                        for entry in iterator:
                            ctx.check()
                            if len(entries) >= self.max_entries:
                                raise FilesystemError('Directory exceeds entry limit')
                            info = entry.stat(follow_symlinks=False)
                            directory = stat.S_ISDIR(info.st_mode)
                            # Escape control characters so filenames cannot forge lines.
                            label = entry.name.encode('unicode_escape').decode('ascii')
                            entries.append((not directory, label, info.st_size))
                    text = ''.join(n + ('/\n' if not non_dir else '\t%d bytes\n' % size)
                                   for non_dir, n, size in sorted(entries)) or '(empty directory)'
                elif name == 'Read':
                    data, _ = self._read(fd, parts[-1])
                    lines = self._text(data).split('\n')
                    start = max(0, args.get('offset', 0) - 1)
                    limit = args.get('limit', 0) or 2000
                    text = ''.join('%6d\t%s\n' % (i + 1, line[:2000] + ('… [line truncated]' if len(line) > 2000 else ''))
                                   for i, line in enumerate(lines[start:start + limit], start)) or '(no lines in the requested range)'
                else:
                    expected = self._version(fd, parts[-1])
                    if name == 'Write':
                        data = args['content'].encode('utf-8')
                        text = '%s %s (%d bytes)' % ('Overwrote' if expected else 'Created', path, len(data))
                    else:
                        raw, version = self._read(fd, parts[-1])
                        if version != expected:
                            raise FilesystemConflict('File changed before edit')
                        original = self._text(raw)
                        count = original.count(args['old_string'])
                        if not count or (count != 1 and not args.get('replace_all', False)):
                            raise FilesystemError('old_string must match uniquely unless replace_all is true')
                        data = original.replace(args['old_string'], args['new_string']).encode('utf-8')
                        text = 'Edited %s (%d replacement(s))' % (path, count)
                    if len(data) > self.max_bytes:
                        raise FilesystemError('Write exceeds byte limit')
                    if len(text.encode('utf-8')) > self.max_output_bytes:
                        raise FilesystemError('Result exceeds output limit')
                    self._replace(ctx, fd, parts[-1], data, expected)
                if len(text.encode('utf-8')) > self.max_output_bytes:
                    raise FilesystemError('Result exceeds output limit; narrow the request')
                return text

    def tools(self):
        properties = {'file_path': {'type': 'string'}, 'path': {'type': 'string'},
                      'content': {'type': 'string'}, 'old_string': {'type': 'string'},
                      'new_string': {'type': 'string'}, 'replace_all': {'type': 'boolean'},
                      'offset': {'type': 'integer'}, 'limit': {'type': 'integer'}}
        definitions = [('Read', ['file_path', 'offset', 'limit'], ['file_path']),
                       ('Write', ['file_path', 'content'], ['file_path', 'content']),
                       ('Edit', ['file_path', 'old_string', 'new_string', 'replace_all'], ['file_path', 'old_string', 'new_string']),
                       ('LS', ['path'], [])]
        return tuple(Tool(name, 'Scoped UTF-8 filesystem ' + name,
                          {'type': 'object', 'properties': {k: properties[k] for k in fields},
                           'required': required, 'additionalProperties': False},
                          lambda ctx, args, n=name: self._call(n, ctx, args),
                          lambda args, n=name: self._validate(n, args))
                     for name, fields, required in definitions)
