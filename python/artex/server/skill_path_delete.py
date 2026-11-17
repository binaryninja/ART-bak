"""Explicit permanent removal of one skill file or empty subdirectory.

No recursion, top-level manifest removal, symlink/hardlink/special targets or
inferred grants. The host exclusively guards writes, renames and mount changes.
Observed target fingerprints are in-request checks, not stale-browser/OS CAS.
Possible effects are retained as unknown; no retry, rollback or undo is implied.
"""
from dataclasses import dataclass
import errno
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
from ..tools.workspace_mutations import (WorkspaceMutations, MutationTarget,
    WorkspaceMutationUncertain, WorkspaceMutationUnsupported)

_FAMILY = re.compile(r'/api/skills/([^/]+)/files/(.+)\Z')


class DeleteUnavailable(RuntimeError): pass


@dataclass(frozen=True)
class SkillPathDeleteSelection:
    name: str
    path: str

    def validate(self):
        if type(self.name) is not str or not valid_skill_name(self.name) or len(self.name.encode('utf-8')) > 255: raise ValueError()
        if type(self.path) is not str or skill_rel_path(self.path) != self.path or len(self.path.split('/')) > 32:
            raise ValueError()
        if any(len(part.encode('utf-8')) > 255 for part in self.path.split('/')): raise ValueError()
        # Also protect case aliases on case-insensitive filesystems. A nested
        # SKILL.md is ordinary data; only the skill's own manifest is protected.
        if self.path.casefold() == 'skill.md': raise PermissionError()


@dataclass(frozen=True)
class SkillPathDeleteProposal:
    scope: SkillDirectoryScope
    selection: SkillPathDeleteSelection
    target: MutationTarget


def _target_key(target):
    if (type(target) is not MutationTarget or type(target.action) is not str or target.action != 'delete'
            or type(target.path) is not str or type(target.kind) is not str or target.kind not in ('file','directory')
            or type(target.parent_identity) is not tuple or len(target.parent_identity) != 2
            or any(type(x) is not int for x in target.parent_identity)
            or type(target.target_fingerprint) is not tuple or len(target.target_fingerprint) != 9
            or any(type(x) is not int for x in target.target_fingerprint)):
        raise PermissionError()
    return target.action, target.path, target.parent_identity, target.target_fingerprint, target.kind


def _unknown():
    return json_response(500, {'error': 'Skill deletion outcome unknown; reconcile before retry',
                               'outcome': 'unknown', 'retry': False})


class SkillPathDeleteService:
    def __init__(self, scope, *, authorize, mutation_authorize, delete_authorize, current, writer_guard):
        callbacks = (authorize, mutation_authorize, delete_authorize, current, writer_guard)
        if type(scope) is not SkillDirectoryScope or any(not callable(f) for f in callbacks):
            raise ValueError('Exact skill root and independent permanent-delete authority required')
        scope.validate()
        self.scope, self.authorize, self.mutate, self.delete, self.current, self.guard = (scope, *callbacks)
        self._pins = (scope, *callbacks)
        self._scope = (scope.root, scope.device, scope.inode, scope.revision)

    def validate(self):
        if any(a is not b for a,b in zip((self.scope,self.authorize,self.mutate,self.delete,self.current,self.guard),self._pins)):
            raise DeleteUnavailable()
        self.scope.validate()
        if (self.scope.root,self.scope.device,self.scope.inode,self.scope.revision) != self._scope:
            raise DeleteUnavailable()

    def _root_current(self):
        with ScopedFilesystem(self._scope[0]) as fs:
            info = os.fstat(fs._fd)
            if (info.st_dev,info.st_ino) != self._scope[1:3]: raise FilesystemConflict()

    def _parent_current(self, path, expected):
        # Reopen the requested pathname, not only the primitive's already-open
        # parent fd. This check intentionally does NOT require the target to
        # exist: the primitive invokes it again after successful unlink/rmdir.
        with ScopedFilesystem(self._scope[0]) as fs:
            info = os.fstat(fs._fd)
            if (info.st_dev,info.st_ino) != self._scope[1:3]: raise FilesystemConflict()
            with fs._directory(fs._parts(path)[:-1]) as parent:
                info = os.fstat(parent)
                if (info.st_dev,info.st_ino) != expected: raise FilesystemConflict()

    def _observe_missing(self, path, expected):
        self._root_current()
        scope = self._pins[0]
        with WorkspaceMutations(scope.root,root_device=scope.device,root_inode=scope.inode) as operations:
            # prepare never mutates. Its mkdir view admits a missing slot,
            # unlike delete preparation, which correctly rejects a missing item.
            observed = operations.prepare('mkdir',path)
            if observed.kind != 'missing' or observed.target_fingerprint is not None or observed.parent_identity != expected:
                raise FilesystemConflict()
            return observed

    def handle(self, request, *, request_authorize):
        changed = False
        try:
            if not callable(request_authorize): raise DeleteUnavailable()
            if (type(request) is not Request or request.method != 'DELETE' or type(request.target) is not str
                    or len(request.target) > 4096 or type(request.body) is not bytes or request.body
                    or type(request.headers) is not dict
                    or any(type(k) is not str or type(v) is not str for k,v in request.headers.items())):
                raise ValueError()
            parts = urlsplit(request.target); match = _FAMILY.fullmatch(parts.path)
            if not match or parts.scheme or parts.netloc or parts.query or parts.fragment: raise ValueError()
            selection = SkillPathDeleteSelection(match[1],match[2]); selection.validate()
            identity = (selection.name,selection.path)
            headers = request.headers
            request_key = (request.method,request.target,request.body,tuple(headers.items()))
            pins, scope_key = self._pins, self._scope
            scope = pins[0]; proposal = None; target = None; target_key = None
            path = selection.name + '/' + selection.path

            def pure():
                self.validate()
                if self._pins is not pins or self._scope is not scope_key: raise DeleteUnavailable()
                if (type(request.method) is not str or type(request.target) is not str or type(request.body) is not bytes
                        or type(request.headers) is not dict or request.headers is not headers
                        or any(type(k) is not str or type(v) is not str for k,v in headers.items())
                        or (request.method,request.target,request.body,tuple(headers.items())) != request_key
                        or type(selection) is not SkillPathDeleteSelection or (selection.name,selection.path) != identity):
                    raise PermissionError()
                selection.validate()
                if proposal is not None:
                    if (type(proposal) is not SkillPathDeleteProposal or proposal.scope is not scope
                            or proposal.selection is not selection or proposal.target is not target
                            or _target_key(target) != target_key): raise PermissionError()

            def allow(*, mutation=False):
                pure()
                if pins[4](scope,selection) is not True: raise PermissionError()
                pure()
                if pins[1](scope,selection) is not True: raise PermissionError()
                pure()
                if mutation:
                    if pins[2](request,proposal) is not True: raise PermissionError()
                    pure()
                    if pins[3](request,proposal) is not True: raise PermissionError()
                    pure()
                if request_authorize(request,scope,selection) is not True: raise PermissionError()
                pure()

            allow()
            with pins[5](scope,selection):
                allow(); self._root_current()
                with WorkspaceMutations(scope.root,root_device=scope.device,root_inode=scope.inode) as operations:
                    target = operations.prepare('delete',path); target_key = _target_key(target)
                    proposal = SkillPathDeleteProposal(scope,selection,target)
                    service = self
                    class Context:
                        def check(self):
                            allow(mutation=True)
                            service._parent_current(path,target.parent_identity)
                    changed = operations.apply(target,Context())
                    if changed is not True: raise FilesystemConflict()
                    allow(mutation=True)
                    observed = self._observe_missing(path,target.parent_identity)
                allow(mutation=True)
                if self._observe_missing(path,target.parent_identity) != observed: raise FilesystemConflict()

            def acknowledge():
                # Only recheck the removed slot. Never repeat removal, remove a
                # replacement, or infer successful rollback from an exception.
                with pins[5](scope,selection):
                    allow(mutation=True)
                    if self._observe_missing(path,target.parent_identity) != observed: raise FilesystemConflict()
                    pure()
                # Guard cleanup itself may revoke permission or change names.
                # These are finite cooperative observations, not a guard held
                # across a socket send or an arbitrary-callback fixed point.
                allow(mutation=True)
                if self._observe_missing(path,target.parent_identity) != observed: raise FilesystemConflict()
                pure()
            acknowledge()
            response = json_response(200,{'deleted':selection.path})
            return DisclosureResponse(response.status,response.body,response.headers,acknowledge)
        except BaseException as exc:
            if changed or isinstance(exc,WorkspaceMutationUncertain): return _unknown()
            if not isinstance(exc,Exception): raise
            if isinstance(exc,PermissionError): return error_response(403,'Skill deletion denied')
            if isinstance(exc,DeleteUnavailable): return error_response(503,'Skill deletion unavailable')
            if isinstance(exc,(WorkspaceMutationUnsupported,FilesystemConflict)):
                return error_response(409,'Skill path changed or directory is not empty')
            if isinstance(exc,FileNotFoundError): return error_response(404,'Skill path not found')
            if isinstance(exc,OSError) and exc.errno in (errno.ENOTDIR,errno.ELOOP):
                return error_response(400,'Invalid skill path')
            if isinstance(exc,(ValueError,TypeError,UnicodeError,FilesystemError)):
                return error_response(400,'Invalid skill deletion request')
            return error_response(500,'Skill deletion preparation failed')


_AUTH_METHOD = AuthService.authorized
_SERVICE_NAMES = ('handle','validate','_root_current','_parent_current','_observe_missing')
_SERVICE_METHODS = tuple(getattr(SkillPathDeleteService,name) for name in _SERVICE_NAMES)
_BASE_METHODS = (WSGIApplication.__call__,WSGIApplication._handle,Router.dispatch,AuthService.authorized)


def _original_bound(obj, name, function):
    method = getattr(obj,name)
    return getattr(method,'__self__',None) is obj and getattr(method,'__func__',None) is function


def _overlap(path):
    parts = path.split('/')
    if len(parts) < 6: return False
    for actual,expected in zip(parts[:5],('','api','skills',None,'files')):
        if expected is None: continue
        pattern = re.escape(actual)
        for name in re.findall(r'\{([A-Za-z_][A-Za-z_0-9]*)\}',actual):
            pattern = pattern.replace(re.escape('{'+name+'}'),'[^/]+')
        if re.fullmatch(pattern,expected) is None: return False
    return True


def bind_skill_path_deletes(application, *, service, authorize_global):
    """Original exact base/JWT; no inherited read/write deletion permission."""
    if (type(application) is not WSGIApplication or type(application.router) is not Router
            or type(service) is not SkillPathDeleteService or not callable(authorize_global)):
        raise ValueError('Original application and explicit deletion authority required')
    base = application.router; auth = base.authorize; owner = getattr(auth,'__self__',None)
    if type(owner) is not AuthService or getattr(auth,'__func__',None) is not _AUTH_METHOD:
        raise ValueError('Original JWT authority required')
    if any(_overlap(path) for method,path,regex,handler,public in base._routes):
        raise ValueError('Skill deletion route conflicts')
    base_bindings = ((application,'__call__'),(application,'_handle'),(base,'dispatch'),(owner,'authorized'))
    methods = tuple((obj,name,function) for (obj,name),function in zip(base_bindings,_BASE_METHODS))
    methods += tuple((service,name,function) for name,function in zip(_SERVICE_NAMES,_SERVICE_METHODS))
    for obj,name,function in methods:
        if not _original_bound(obj,name,function): raise ValueError('Original bound method and owner required')
    service.validate(); pins,scope = service._pins,service._scope
    routes = tuple(base._routes); auth_pins = (owner.store,owner.codec,owner.key,owner.clock)
    bound = application.max_body_bytes
    def check():
        if (application.router is not base or base.authorize is not auth or tuple(base._routes) != routes
                or type(application.max_body_bytes) is not int or application.max_body_bytes != bound
                or any(a is not b for a,b in zip((owner.store,owner.codec,owner.key,owner.clock),auth_pins))): raise PermissionError()
        for obj,name,function in methods:
            if not _original_bound(obj,name,function): raise PermissionError()
        if service._pins is not pins or service._scope is not scope: raise PermissionError()
        service.validate()
    def initial(request):
        check(); accepted = auth(request); check(); return accepted is True
    def invoke(request):
        selected = None
        def gate(candidate,scope,selection):
            nonlocal selected
            check()
            if candidate is not request or scope is not pins[0] or type(selection) is not SkillPathDeleteSelection:
                raise PermissionError()
            if selected is None: selected = selection
            elif selection is not selected: raise PermissionError()
            if auth(request) is not True: raise PermissionError()
            check()
            if authorize_global(request,scope,selection) is not True: raise PermissionError()
            check()
            if auth(request) is not True: raise PermissionError()
            check(); return True
        return service.handle(request,request_authorize=gate)
    class Overlay:
        def dispatch(self,request):
            try: check()
            except Exception: return error_response(503,'Skill deletion composition unavailable')
            if _FAMILY.fullmatch(request.path):
                # Decode the PEP3333 Latin-1 URI-byte carrier once. Do not URL
                # unquote again: literal percent aliases must remain invalid.
                try:
                    if len(request.target) > 4096: raise ValueError()
                    parts = urlsplit(request.target)
                    path = request.path.encode('latin-1').decode('utf-8')
                    decoded = Request(request.method,path+('?' + parts.query if parts.query else ''),request.headers,request.body)
                except (ValueError,UnicodeError): return error_response(400,'Invalid skill deletion request')
                if not initial(decoded): return error_response(401,'Unauthorized')
                if decoded.method != 'DELETE':
                    response = error_response(405,'Method not allowed')
                    return type(response)(response.status,response.body,{**response.headers,'Allow':'DELETE'})
                return invoke(decoded)
            return base.dispatch(request)
    return WSGIApplication(Overlay(),max_body_bytes=bound)
