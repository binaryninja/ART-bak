"""Opt-in skill subdirectory creation; no file writes, deletes or execution.

Host exclusively guards tree writes, root renames and mount changes. Existing
parents are required; this is not Go's recursive MkdirAll. New directories are
0700. Source/read permission alone never authorizes mutation. Possible effects
are reported as unknown, never retried or described as rolled back.
"""
from dataclasses import dataclass
import errno
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
from ..tools.workspace_mutations import WorkspaceMutations, MutationTarget, WorkspaceMutationUncertain

_FAMILY = re.compile(r'/api/skills/([^/]+)/dirs\Z')
_PATH = '/api/skills/{name}/dirs'


class DirectoryUnavailable(RuntimeError): pass


@dataclass(frozen=True)
class SkillDirectorySelection:
    name: str
    path: str

    def validate(self):
        if type(self.name) is not str or not valid_skill_name(self.name): raise ValueError()
        if type(self.path) is not str or skill_rel_path(self.path) != self.path or len(self.path.split('/')) > 32:
            raise ValueError()


@dataclass(frozen=True)
class SkillDirectoryProposal:
    scope: SkillDirectoryScope
    selection: SkillDirectorySelection
    target: MutationTarget


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError()
        result[key] = value
    return result


def _unknown():
    return json_response(500, {'error': 'Skill directory outcome unknown; reconcile before retry',
                               'outcome': 'unknown', 'retry': False})


class SkillDirectoryCreateService:
    def __init__(self, scope, *, authorize, mutation_authorize, current, writer_guard):
        if type(scope) is not SkillDirectoryScope or any(not callable(f) for f in (authorize, mutation_authorize, current, writer_guard)):
            raise ValueError('Exact skill root and independent mutation authority required')
        scope.validate()
        self.scope, self.authorize, self.mutate, self.current, self.guard = scope, authorize, mutation_authorize, current, writer_guard
        self._pins = (scope, authorize, mutation_authorize, current, writer_guard)
        self._scope = (scope.root, scope.device, scope.inode, scope.revision)

    def validate(self):
        if any(a is not b for a, b in zip((self.scope, self.authorize, self.mutate, self.current, self.guard), self._pins)):
            raise DirectoryUnavailable()
        self.scope.validate()
        if (self.scope.root, self.scope.device, self.scope.inode, self.scope.revision) != self._scope:
            raise DirectoryUnavailable()

    def _root_current(self):
        # Reopen the named root with no-follow ancestor traversal, not only the
        # old descriptor. Root replacement cannot redirect a granted operation.
        with ScopedFilesystem(self._scope[0]) as fs:
            info = os.fstat(fs._fd)
            if (info.st_dev, info.st_ino) != self._scope[1:3]: raise FilesystemConflict()

    def handle(self, request, *, request_authorize):
        changed = False
        try:
            if not callable(request_authorize): raise DirectoryUnavailable()
            if (type(request) is not Request or request.method != 'POST' or type(request.target) is not str
                    or len(request.target) > 4096 or type(request.body) is not bytes or len(request.body) > 4096
                    or type(request.headers) is not dict
                    or any(type(k) is not str or type(v) is not str for k, v in request.headers.items())):
                raise ValueError()
            parts = urlsplit(request.target)
            match = _FAMILY.fullmatch(parts.path)
            if not match or parts.scheme or parts.netloc or parts.query or parts.fragment: raise ValueError()
            body = json.loads(request.body.decode('utf-8'), object_pairs_hook=_pairs,
                              parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))
            if type(body) is not dict or set(body) != {'path'}: raise ValueError()
            selection = SkillDirectorySelection(match[1], body['path'])
            selection.validate()
            identity = (selection.name, selection.path)
            headers = request.headers
            request_key = (request.method, request.target, request.body, tuple(headers.items()))
            pins = self._pins; scope_key = self._scope
            scope = pins[0]; proposal = None; target_key = None

            def pure():
                self.validate()
                if self._pins is not pins or self._scope is not scope_key: raise DirectoryUnavailable()
                if (type(request.method) is not str or type(request.target) is not str or type(request.body) is not bytes
                        or type(request.headers) is not dict or any(type(k) is not str or type(v) is not str for k,v in headers.items())
                        or request.headers is not headers or
                        (request.method, request.target, request.body, tuple(headers.items())) != request_key
                        or (selection.name, selection.path) != identity): raise PermissionError()
                selection.validate()
                if proposal is not None:
                    if (proposal.scope is not scope or proposal.selection is not selection
                            or proposal.target is not target or type(proposal.target) is not MutationTarget
                            or tuple(vars(proposal.target).values()) != target_key): raise PermissionError()

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

            allow()
            with self._pins[4](scope, selection):
                allow(); self._root_current()
                path = selection.name + '/' + selection.path
                with WorkspaceMutations(scope.root, root_device=scope.device, root_inode=scope.inode) as operations:
                    target = operations.prepare('mkdir', path)
                    proposal = SkillDirectoryProposal(scope, selection, target)
                    target_key = tuple(vars(target).values())
                    service = self
                    class Context:
                        def check(self):
                            allow(mutation=True); service._root_current()
                            # apply may already hold the parent descriptor while
                            # a cooperative policy callback moves its pathname.
                            # Compare a fresh named-parent walk before every
                            # primitive checkpoint, not merely the old fd.
                            named = operations.prepare('mkdir', path)
                            if named.parent_identity != target.parent_identity:
                                raise FilesystemConflict()
                    changed = operations.apply(target, Context())
                    allow(mutation=True); self._root_current()
                    observed = operations.prepare('mkdir', path)
                    if observed.kind != 'directory' or observed.parent_identity != target.parent_identity:
                        raise FilesystemConflict()
                allow(mutation=True); self._root_current()

            def acknowledge():
                # Revalidate only; never repeat mkdir or infer restart authority.
                with self._pins[4](scope, selection):
                    allow(mutation=True); self._root_current()
                    with WorkspaceMutations(scope.root, root_device=scope.device, root_inode=scope.inode) as operations:
                        if operations.prepare('mkdir', path) != observed: raise FilesystemConflict()
                    pure()
                # Guard cleanup is a callback boundary too. Final read-only
                # permission checks intentionally run after release, followed
                # only by code-owned descriptor/identity validation. These are
                # finite observations, not a lock held across network delivery.
                allow(mutation=True); self._root_current()
                with WorkspaceMutations(scope.root, root_device=scope.device, root_inode=scope.inode) as operations:
                    if operations.prepare('mkdir', path) != observed: raise FilesystemConflict()
                pure()
            acknowledge()
            response = json_response(201, {'dir': selection.path})
            return DisclosureResponse(response.status, response.body, response.headers, acknowledge)
        except BaseException as exc:
            if changed or isinstance(exc, WorkspaceMutationUncertain): return _unknown()
            if not isinstance(exc, Exception): raise
            if isinstance(exc, PermissionError): return error_response(403, 'Skill directory creation denied')
            if isinstance(exc, DirectoryUnavailable): return error_response(503, 'Skill directory creation unavailable')
            if isinstance(exc, FilesystemConflict): return error_response(409, 'Skill directory changed or conflicts')
            if isinstance(exc, FileNotFoundError): return error_response(404, 'Skill or parent directory not found')
            if isinstance(exc, OSError) and exc.errno in (errno.ENOTDIR, errno.ELOOP):
                return error_response(400, 'Invalid skill directory path')
            if isinstance(exc, (ValueError, TypeError, UnicodeError, FilesystemError)):
                return error_response(400, 'Invalid skill directory request')
            return error_response(500, 'Skill directory preparation failed')


_AUTH_METHOD = AuthService.authorized
_SERVICE_METHODS = (SkillDirectoryCreateService.handle, SkillDirectoryCreateService.validate, SkillDirectoryCreateService._root_current)


def _overlap(path):
    parts = path.split('/')
    if len(parts) != 5: return False
    for actual, expected in zip(parts, ('', 'api', 'skills', None, 'dirs')):
        if expected is None: continue
        pattern = re.escape(actual)
        for name in re.findall(r'\{([A-Za-z_][A-Za-z_0-9]*)\}', actual):
            pattern = pattern.replace(re.escape('{'+name+'}'), '[^/]+')
        if re.fullmatch(pattern, expected) is None: return False
    return True


def bind_skill_directory_creation(application, *, service, authorize_global):
    """Return a new protected application; original routes/authority stay fixed.

    This intentionally accepts only an original WSGI/Router/AuthService base,
    not arbitrary overlay wrappers. Read-family composition is unchanged.
    """
    if (type(application) is not WSGIApplication or type(application.router) is not Router
            or type(service) is not SkillDirectoryCreateService or not callable(authorize_global)):
        raise ValueError('Original application and explicit skill mutation authority required')
    base = application.router; auth = base.authorize; owner = getattr(auth, '__self__', None)
    if type(owner) is not AuthService or getattr(auth, '__func__', None) is not _AUTH_METHOD:
        raise ValueError('Original JWT authority required')
    if any(_overlap(path) for method, path, regex, handler, public in base._routes):
        raise ValueError('Skill directory route conflicts')
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
    protected = Router(initial)
    protected.add('POST', _PATH, lambda request, params: service.handle(request, request_authorize=gate))
    class Overlay:
        def dispatch(self, request):
            try: check()
            except Exception: return error_response(503, 'Skill directory composition unavailable')
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
                    return error_response(400, 'Invalid skill directory request')
                return protected.dispatch(decoded)
            return base.dispatch(request)
    return WSGIApplication(Overlay(), max_body_bytes=body_bound)
