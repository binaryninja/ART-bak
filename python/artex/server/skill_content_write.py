"""Explicit UTF-8 skill-file create/save under an exclusively guarded root.

No inferred root, parent creation, binary files, execution or default wiring.
The host supplies exact content-digest mutation authority separately from reads.
New files0600, ordinary existing rwx preserved, special bits removed; unsupported
ACL/xattr/ownership fails closed. In-request versions detect observed changes,
not stale browser edits or hostile writers outside the host's exclusive guard.
"""
from dataclasses import dataclass
import errno
import hashlib
import json
import os
import re
from urllib.parse import urlsplit
from .auth import AuthService
from .core import Request, Router, json_response, error_response
from .disclosure import DisclosureResponse
from .wsgi import WSGIApplication
from ..skills import valid_skill_name, skill_rel_path
from ..skills.file_listing import SkillDirectoryScope
from ..tools.filesystem import ScopedFilesystem, FilesystemError, FilesystemConflict

_FAMILY = re.compile(r'/api/skills/([^/]+)/files/(.+)\Z')
MAX_BYTES = 262144
MAX_JSON_BYTES = 6 * MAX_BYTES + 32


class WriteUnavailable(RuntimeError): pass


@dataclass(frozen=True)
class SkillWriteSelection:
    name: str
    path: str

    def validate(self):
        if type(self.name) is not str or not valid_skill_name(self.name): raise ValueError()
        if type(self.path) is not str or skill_rel_path(self.path) != self.path or len(self.path.split('/')) > 32:
            raise ValueError()


@dataclass(frozen=True)
class SkillWriteProposal:
    scope: SkillDirectoryScope
    selection: SkillWriteSelection
    parent_identity: tuple
    previous_version: tuple | None
    content_bytes: int
    content_sha256: str


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError()
        result[key] = value
    return result


def _unknown():
    return json_response(500, {'error': 'Skill file outcome unknown; reconcile before retry',
                               'outcome': 'unknown', 'retry': False})


class SkillContentWriteService:
    def __init__(self, scope, *, authorize, mutation_authorize, current, writer_guard):
        if type(scope) is not SkillDirectoryScope or any(not callable(f) for f in (authorize, mutation_authorize, current, writer_guard)):
            raise ValueError('Exact skill root and independent mutation authority required')
        scope.validate()
        self.scope, self.authorize, self.mutate, self.current, self.guard = scope, authorize, mutation_authorize, current, writer_guard
        self._pins = (scope, authorize, mutation_authorize, current, writer_guard)
        self._scope = (scope.root, scope.device, scope.inode, scope.revision)

    def validate(self):
        if any(a is not b for a, b in zip((self.scope, self.authorize, self.mutate, self.current, self.guard), self._pins)):
            raise WriteUnavailable()
        self.scope.validate()
        if (self.scope.root, self.scope.device, self.scope.inode, self.scope.revision) != self._scope:
            raise WriteUnavailable()

    def _root_current(self):
        # Reopen the named root with no-follow ancestor traversal, not only the
        # old descriptor. Root replacement cannot redirect a granted operation.
        with ScopedFilesystem(self._scope[0]) as fs:
            info = os.fstat(fs._fd)
            if (info.st_dev, info.st_ino) != self._scope[1:3]: raise FilesystemConflict()

    def handle(self, request, *, request_authorize):
        started = False
        try:
            if not callable(request_authorize): raise WriteUnavailable()
            if (type(request) is not Request or request.method != 'PUT' or type(request.target) is not str
                    or len(request.target) > 4096 or type(request.body) is not bytes or len(request.body) > MAX_JSON_BYTES
                    or type(request.headers) is not dict
                    or any(type(k) is not str or type(v) is not str for k, v in request.headers.items())):
                raise ValueError()
            parts = urlsplit(request.target)
            match = _FAMILY.fullmatch(parts.path)
            if not match or parts.scheme or parts.netloc or parts.query or parts.fragment: raise ValueError()
            body = json.loads(request.body.decode('utf-8'), object_pairs_hook=_pairs,
                              parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))
            if type(body) is not dict or set(body) != {'content'}: raise ValueError()
            content = body['content']
            if type(content) is not str or '\0' in content: raise ValueError()
            encoded = content.encode('utf-8')
            if len(encoded) > MAX_BYTES: return error_response(413, 'Skill file exceeds byte limit')
            content_hash = hashlib.sha256(encoded).hexdigest()
            selection = SkillWriteSelection(match[1], match[2])
            selection.validate()
            identity = (selection.name, selection.path)
            headers = request.headers
            request_key = (request.method, request.target, request.body, tuple(headers.items()))
            pins = self._pins; scope_key = self._scope
            scope = pins[0]; proposal = None; proposal_key = None

            def pure():
                self.validate()
                if self._pins is not pins or self._scope is not scope_key: raise WriteUnavailable()
                if (type(request.method) is not str or type(request.target) is not str or type(request.body) is not bytes
                        or type(request.headers) is not dict or any(type(k) is not str or type(v) is not str for k,v in headers.items())
                        or request.headers is not headers or
                        (request.method, request.target, request.body, tuple(headers.items())) != request_key
                        or (selection.name, selection.path) != identity): raise PermissionError()
                selection.validate()
                if proposal is not None:
                    if (proposal.scope is not scope or proposal.selection is not selection
                            or proposal.parent_identity is not proposal_key[0]
                            or proposal.previous_version is not proposal_key[1]
                            or type(proposal.content_bytes) is not int or proposal.content_bytes != proposal_key[2]
                            or type(proposal.content_sha256) is not str or proposal.content_sha256 != proposal_key[3]):
                        raise PermissionError()

            def allow(*, mutation=False):
                pure()
                if self._pins[3](scope, selection) is not True: raise PermissionError()
                pure()
                if self._pins[1](scope, selection) is not True: raise PermissionError()
                pure()
                if mutation:
                    if self._pins[2](request, proposal) is not True: raise PermissionError()
                    pure()
                # The outer original JWT/global gate is last, not an inferred
                # permission from the filesystem reader or a previous request.
                if request_authorize(request, scope, selection) is not True: raise PermissionError()
                pure()

            path = selection.name + '/' + selection.path

            def observe():
                # Always walk the named root and parent again, not an old held
                # descriptor. No policy callback runs during this observation.
                self._root_current()
                with ScopedFilesystem(scope.root, max_bytes=MAX_BYTES) as fs:
                    parts = fs._parts(path)
                    with fs._directory(parts[:-1]) as fd:
                        parent = os.fstat(fd)
                        if parent.st_dev != scope.device: raise FilesystemError()
                        try:
                            data, version = fs._read(fd, parts[-1])
                            fs._text(data)  # Existing binary/non-UTF8 files are not replaced.
                        except FileNotFoundError:
                            version = None
                        if version is not None and version[0][0] != scope.device: raise FilesystemError()
                        return (parent.st_dev, parent.st_ino), version

            allow()
            with self._pins[4](scope, selection):
                allow()
                parent, previous = observe()
                proposal = SkillWriteProposal(scope, selection, parent, previous, len(encoded), content_hash)
                proposal_key = (parent, previous, len(encoded), content_hash)
                class Context:
                    def check(self):
                        allow(mutation=True)
                        if observe() != (parent, previous): raise FilesystemConflict()
                context = Context()
                context.check()  # Deny before even a temporary file is created.
                with ScopedFilesystem(scope.root, max_bytes=MAX_BYTES) as fs:
                    # Once the primitive starts, any exception is conservative
                    # uncertainty: temporary writes, replace or fsync may occur.
                    # It retains its own version/metadata checks before rename.
                    started = True
                    fs._call('Write', context, {'file_path': path, 'content': content})
                    allow(mutation=True)
                    observed = observe()
                    if (observed[0] != parent or observed[1] is None
                            or observed[1][1] != hashlib.sha256(encoded).digest()):
                        raise FilesystemConflict()
                allow(mutation=True)
                if observe() != observed: raise FilesystemConflict()

            def acknowledge():
                # Revalidate only; never repeat the write. Guards do not span
                # network delivery; only a finite cooperative observation.
                with self._pins[4](scope, selection):
                    allow(mutation=True)
                    if observe() != observed: raise FilesystemConflict()
                    pure()
                allow(mutation=True)
                if observe() != observed: raise FilesystemConflict()
                pure()
            acknowledge()
            response = json_response(200, {'ok': True})
            return DisclosureResponse(response.status, response.body, response.headers, acknowledge)
        except BaseException as exc:
            if started: return _unknown()
            if not isinstance(exc, Exception): raise
            if isinstance(exc, PermissionError): return error_response(403, 'Skill file write denied')
            if isinstance(exc, WriteUnavailable): return error_response(503, 'Skill file write unavailable')
            if isinstance(exc, FilesystemConflict): return error_response(409, 'Skill file changed or conflicts')
            if isinstance(exc, FileNotFoundError): return error_response(404, 'Skill or parent directory not found')
            if isinstance(exc, OSError) and exc.errno in (errno.ENOTDIR, errno.ELOOP):
                return error_response(400, 'Invalid skill file path')
            if isinstance(exc, (ValueError, TypeError, UnicodeError, FilesystemError)):
                return error_response(400, 'Invalid skill file request')
            return error_response(500, 'Skill file preparation failed')


_AUTH_METHOD = AuthService.authorized
_SERVICE_METHODS = (SkillContentWriteService.handle, SkillContentWriteService.validate, SkillContentWriteService._root_current)


def _overlap(path):
    parts = path.split('/')
    if len(parts) < 6: return False
    for actual, expected in zip(parts[:5], ('', 'api', 'skills', None, 'files')):
        if expected is None: continue
        pattern = re.escape(actual)
        for name in re.findall(r'\{([A-Za-z_][A-Za-z_0-9]*)\}', actual):
            pattern = pattern.replace(re.escape('{'+name+'}'), '[^/]+')
        if re.fullmatch(pattern, expected) is None: return False
    return True


def bind_skill_content_writes(application, *, service, authorize_global):
    """Return a new protected application; original routes/authority stay fixed.

    This intentionally accepts only an original WSGI/Router/AuthService base,
    not arbitrary overlay wrappers. Read-family composition is unchanged.
    """
    if (type(application) is not WSGIApplication or type(application.router) is not Router
            or type(service) is not SkillContentWriteService or not callable(authorize_global)):
        raise ValueError('Original application and explicit skill mutation authority required')
    base = application.router; auth = base.authorize; owner = getattr(auth, '__self__', None)
    if type(owner) is not AuthService or getattr(auth, '__func__', None) is not _AUTH_METHOD:
        raise ValueError('Original JWT authority required')
    if any(_overlap(path) for method, path, regex, handler, public in base._routes):
        raise ValueError('Skill file route conflicts')
    for name, function in zip(('handle', 'validate', '_root_current'), _SERVICE_METHODS):
        if getattr(getattr(service, name), '__func__', None) is not function: raise ValueError('Original service methods required')
    service.validate()
    service_pins = service._pins; service_scope = service._scope
    routes = tuple(base._routes); auth_pins = (owner.store, owner.codec, owner.key, owner.clock)
    body_bound = application.max_body_bytes
    methods = tuple((obj, name, getattr(getattr(obj, name), '__func__', getattr(obj, name)))
                    for obj, name in ((application, '__call__'), (base, 'dispatch'), (owner, 'authorized'),
                                      (service, 'handle'), (service, 'validate'), (service, '_root_current')))
    def check():
        if (application.router is not base or base.authorize is not auth or tuple(base._routes) != routes
                or type(application.max_body_bytes) is not int or application.max_body_bytes != body_bound
                or any(a is not b for a, b in zip((owner.store, owner.codec, owner.key, owner.clock), auth_pins))):
            raise PermissionError()
        for obj, name, function in methods:
            if getattr(getattr(obj, name), '__func__', getattr(obj, name)) is not function: raise PermissionError()
        if service._pins is not service_pins or service._scope is not service_scope: raise PermissionError()
        service.validate()
    def initial(request):
        check(); accepted = auth(request); check(); return accepted is True
    def gate(request, scope, selection):
        check()
        if auth(request) is not True: raise PermissionError()
        check()
        if authorize_global(request, scope, selection) is not True: raise PermissionError()
        check()
        if auth(request) is not True: raise PermissionError()
        check(); return True
    class Overlay:
        def dispatch(self, request):
            try: check()
            except Exception: return error_response(503, 'Skill file composition unavailable')
            if _FAMILY.fullmatch(request.path):
                # PEP3333 PATH_INFO is URI-decoded bytes carried in Latin-1,
                # not an already decoded Unicode application path. Decode the
                # carrier exactly once at this opt-in WSGI boundary. Never URL
                # unquote again: literal '%' aliases must remain invalid names.
                try:
                    if len(request.target) > 4096: raise ValueError()
                    parts = urlsplit(request.target)
                    path = request.path.encode('latin-1').decode('utf-8')
                    target = path + ('?' + parts.query if parts.query else '')
                    decoded = Request(request.method, target, request.headers, request.body)
                except (ValueError, UnicodeError):
                    return error_response(400, 'Invalid skill file request')
                if not initial(decoded): return error_response(401, 'Unauthorized')
                if decoded.method != 'PUT':
                    response = error_response(405, 'Method not allowed')
                    return type(response)(response.status, response.body, {**response.headers, 'Allow': 'PUT'})
                return service.handle(decoded, request_authorize=gate)
            return base.dispatch(request)
    return WSGIApplication(Overlay(), max_body_bytes=body_bound)
