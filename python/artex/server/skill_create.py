"""Explicit create-only top-level Skills workspace mutation.

The host exclusively owns/guards the private root, including external writers,
renames and mount changes. This is not an arbitrary-writer filesystem transaction
or a transport-wide revocation barrier. A failed attempt may leave an incomplete
new directory; never delete it, retry automatically or claim rollback. Generated
SKILL.md is inert text, not an instruction to this server or an execution grant.
"""
from dataclasses import dataclass
import hashlib
import json
import os
import re
import stat

from .auth import AuthService
from .core import Request, Router, error_response, json_response
from .disclosure import DisclosureResponse
from .wsgi import WSGIApplication
from ..skills import parse_skill, render_skill, valid_skill_name
from ..skills.file_listing import SkillDirectoryScope
from ..tools.filesystem import ScopedFilesystem, FilesystemConflict, FilesystemError

MAX_BYTES = 262144
MAX_METADATA_BYTES = 16384
MAX_JSON_BYTES = 6 * MAX_BYTES + 32
_RENDER = render_skill
_PARSE = parse_skill
_FIELDS = ('name', 'description', 'license', 'compatibility', 'instructions')


class CreateUnavailable(RuntimeError): pass
class CreateLimit(ValueError): pass


@dataclass(frozen=True)
class SkillCreateSelection:
    name: str
    description: str
    license: str = ''
    compatibility: str = ''
    instructions: str = ''

    def validate(self):
        values = tuple(getattr(self, field) for field in _FIELDS)
        if any(type(value) is not str or '\0' in value for value in values): raise ValueError()
        encoded = tuple(value.encode('utf-8') for value in values)
        if not valid_skill_name(self.name) or len(encoded[0]) > 255: raise ValueError()
        if not self.description.strip(): raise ValueError()
        if any('\r' in value or '\n' in value for value in values[1:4]): raise ValueError()
        if any(len(value) > MAX_METADATA_BYTES for value in encoded[1:4]) or len(encoded[4]) > MAX_BYTES:
            raise CreateLimit()


@dataclass(frozen=True)
class SkillCreateProposal:
    scope: SkillDirectoryScope
    selection: SkillCreateSelection
    parent_identity: tuple
    content_bytes: int
    content_sha256: str


def _pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out: raise ValueError()
        out[key] = value
    return out


def _unknown():
    return json_response(500, {'error': 'Skill creation outcome unknown; reconcile before retry',
                               'outcome': 'unknown', 'retry': False})


class SkillCreateService:
    def __init__(self, scope, *, authorize, mutation_authorize, current, writer_guard):
        if type(scope) is not SkillDirectoryScope or any(not callable(f) for f in (authorize, mutation_authorize, current, writer_guard)):
            raise ValueError('Exact owned root and independent creation authority required')
        scope.validate()
        self.scope, self.authorize, self.mutate, self.current, self.guard = scope, authorize, mutation_authorize, current, writer_guard
        self._pins = (scope, authorize, mutation_authorize, current, writer_guard)
        self._scope = (scope.root, scope.device, scope.inode, scope.revision)

    def validate(self):
        if render_skill is not _RENDER or parse_skill is not _PARSE or any(a is not b for a,b in zip(
                (self.scope,self.authorize,self.mutate,self.current,self.guard),self._pins)):
            raise CreateUnavailable()
        self.scope.validate()
        if (self.scope.root,self.scope.device,self.scope.inode,self.scope.revision) != self._scope:
            raise CreateUnavailable()

    def _root_current(self):
        with ScopedFilesystem(self._scope[0]) as fs:
            info = os.fstat(fs._fd)
            if (info.st_dev,info.st_ino) != self._scope[1:3]: raise FilesystemConflict()
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise FilesystemError('Owner-only root required')
            fs._check_metadata(fs._fd)

    def _observe(self, fs, name, *, content=None, expected=None):
        """Callback-free observation of exactly one newly created inert file."""
        self._root_current()
        root = os.fstat(fs._fd)
        if (root.st_dev,root.st_ino) != self._scope[1:3]: raise FilesystemConflict()
        before = os.stat(name, dir_fd=fs._fd, follow_symlinks=False)
        if not stat.S_ISDIR(before.st_mode) or before.st_dev != self._scope[1]: raise FilesystemConflict()
        with fs._directory([name]) as fd:
            if fs._stat(os.fstat(fd)) != fs._stat(before): raise FilesystemConflict()
            if before.st_uid != os.geteuid() or stat.S_IMODE(before.st_mode) != 0o700:
                raise FilesystemError('Private directory required')
            fs._check_metadata(fd)
            if os.listdir(fd) != ['SKILL.md']: raise FilesystemConflict()
            file_fd = os.open('SKILL.md', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            try:
                meta = os.fstat(file_fd)
                if (not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1
                        or meta.st_dev != self._scope[1] or meta.st_uid != os.geteuid()
                        or stat.S_IMODE(meta.st_mode) != 0o600): raise FilesystemConflict()
                fs._check_metadata(file_fd)
            finally: os.close(file_fd)
            data, version = fs._read(fd, 'SKILL.md')
            if fs._stat(meta) != version[0] or (content is not None and data != content): raise FilesystemConflict()
            if fs._stat(os.fstat(fd)) != fs._stat(before): raise FilesystemConflict()
        if fs._stat(os.stat(name, dir_fd=fs._fd, follow_symlinks=False)) != fs._stat(before): raise FilesystemConflict()
        self._root_current()
        result = (fs._stat(before), version)
        if expected is not None and result != expected: raise FilesystemConflict()
        return result

    def handle(self, request, *, request_authorize):
        attempted = False
        try:
            if not callable(request_authorize): raise CreateUnavailable()
            if (type(request) is not Request or request.method != 'POST' or request.target != '/api/skills'
                    or type(request.body) is not bytes or type(request.headers) is not dict
                    or any(type(k) is not str or type(v) is not str for k,v in request.headers.items())):
                raise ValueError()
            if len(request.body) > MAX_JSON_BYTES: raise CreateLimit()
            try:
                body = json.loads(request.body.decode('utf-8'), object_pairs_hook=_pairs,
                                  parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            except RecursionError: raise ValueError() from None
            if type(body) is not dict or set(body)-set(_FIELDS) or not {'name','description'} <= set(body): raise ValueError()
            selection = SkillCreateSelection(**body)
            selection.validate()
            encoded = _RENDER(selection.name, selection.description, license=selection.license,
                              compatibility=selection.compatibility, instructions=selection.instructions).encode('utf-8')
            if len(encoded) > MAX_BYTES: raise CreateLimit()
            parsed = _PARSE(encoded.decode('utf-8'))
            if (any(getattr(parsed,field) != getattr(selection,field) for field in _FIELDS[:4])
                    or parsed.mcps or (selection.instructions.strip() and parsed.instructions != selection.instructions)):
                raise ValueError()
            identity = tuple(getattr(selection, field) for field in _FIELDS)
            headers = request.headers
            request_key = (request.method, request.target, request.body, tuple(headers.items()))
            pins = self._pins; scope_key = self._scope; scope = pins[0]
            proposal = SkillCreateProposal(scope, selection, scope_key[1:3], len(encoded), hashlib.sha256(encoded).hexdigest())
            proposal_key = (proposal.parent_identity, proposal.content_bytes, proposal.content_sha256)

            def pure():
                self.validate()
                if self._pins is not pins or self._scope is not scope_key: raise CreateUnavailable()
                if (type(request.method) is not str or type(request.target) is not str or type(request.body) is not bytes
                        or request.headers is not headers or type(request.headers) is not dict
                        or any(type(k) is not str or type(v) is not str for k,v in headers.items())
                        or (request.method,request.target,request.body,tuple(headers.items())) != request_key
                        or tuple(getattr(selection, field) for field in _FIELDS) != identity
                        or proposal.scope is not scope or proposal.selection is not selection
                        or proposal.parent_identity is not proposal_key[0]
                        or type(proposal.content_bytes) is not int or proposal.content_bytes != proposal_key[1]
                        or type(proposal.content_sha256) is not str or proposal.content_sha256 != proposal_key[2]):
                    raise PermissionError()
                selection.validate()

            def allow():
                pure()
                if pins[3](scope, selection) is not True: raise PermissionError()
                pure()
                if pins[1](scope, selection) is not True: raise PermissionError()
                pure()
                if pins[2](request, proposal) is not True: raise PermissionError()
                pure()
                if request_authorize(request, scope, selection) is not True: raise PermissionError()
                pure()

            allow()
            with pins[4](scope, selection):
                allow(); self._root_current()
                with ScopedFilesystem(scope.root, max_bytes=MAX_BYTES) as fs:
                    def root_check():
                        pure(); self._root_current()
                        if (os.fstat(fs._fd).st_dev,os.fstat(fs._fd).st_ino) != scope_key[1:3]: raise FilesystemConflict()
                    def missing():
                        root_check()
                        try: os.stat(selection.name, dir_fd=fs._fd, follow_symlinks=False)
                        except FileNotFoundError: return
                        raise FilesystemConflict()
                    missing(); allow(); missing()
                    # Once mkdir is attempted, even exceptional syscall return
                    # is uncertain. No recursive cleanup or retry is safe here.
                    attempted = True
                    os.mkdir(selection.name, mode=0o700, dir_fd=fs._fd)
                    created = os.stat(selection.name, dir_fd=fs._fd, follow_symlinks=False)
                    with fs._directory([selection.name]) as fd:
                        def directory_check(expected):
                            root_check()
                            current = os.fstat(fd)
                            named = os.stat(selection.name, dir_fd=fs._fd, follow_symlinks=False)
                            if (fs._stat(current) != expected or fs._stat(named) != expected
                                    or not stat.S_ISDIR(current.st_mode) or current.st_dev != scope.device
                                    or current.st_uid != os.geteuid() or stat.S_IMODE(current.st_mode) != 0o700):
                                raise FilesystemConflict()
                            fs._check_metadata(fd)
                        directory = fs._stat(created)
                        directory_check(directory)
                        if os.listdir(fd): raise FilesystemConflict()
                        allow(); directory_check(directory)
                        file_fd = os.open('SKILL.md', os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
                        try:
                            directory = fs._stat(os.fstat(fd))
                            file_version = fs._stat(os.fstat(file_fd))
                            def file_check():
                                directory_check(directory)
                                meta = os.fstat(file_fd)
                                if (fs._stat(meta) != file_version
                                        or fs._stat(os.stat('SKILL.md',dir_fd=fd,follow_symlinks=False)) != file_version
                                        or not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1
                                        or meta.st_dev != scope.device or meta.st_uid != os.geteuid()
                                        or stat.S_IMODE(meta.st_mode) != 0o600): raise FilesystemConflict()
                                fs._check_metadata(file_fd)
                            offset = 0
                            while offset < len(encoded):
                                allow(); file_check()
                                count = os.write(file_fd, encoded[offset:offset+65536])
                                if type(count) is not int or not 0 < count <= min(65536,len(encoded)-offset): raise OSError()
                                offset += count
                                file_version = fs._stat(os.fstat(file_fd))
                            allow(); file_check(); os.fsync(file_fd)
                            file_check()
                        finally: os.close(file_fd)
                        allow(); directory_check(directory); os.fsync(fd)
                    allow(); root_check(); os.fsync(fs._fd)
                    observed = self._observe(fs, selection.name, content=encoded)
                allow(); self._root_current()

            def acknowledge():
                with pins[4](scope, selection):
                    allow()
                    with ScopedFilesystem(scope.root,max_bytes=MAX_BYTES) as fs:
                        self._observe(fs,selection.name,expected=observed)
                    pure()
                # Finite cooperative receipt checks, not socket-send atomicity.
                allow()
                with ScopedFilesystem(scope.root,max_bytes=MAX_BYTES) as fs:
                    self._observe(fs,selection.name,expected=observed)
                pure()
            acknowledge()
            response = json_response(201, {'name': selection.name})
            return DisclosureResponse(response.status,response.body,response.headers,acknowledge)
        except BaseException as exc:
            if attempted: return _unknown()
            if not isinstance(exc,Exception): raise
            if isinstance(exc,PermissionError): return error_response(403,'Skill creation denied')
            if isinstance(exc,CreateUnavailable): return error_response(503,'Skill creation unavailable')
            if isinstance(exc,CreateLimit): return error_response(413,'Skill creation exceeds byte limit')
            if isinstance(exc,FilesystemConflict): return error_response(409,'Skill already exists or root changed')
            if isinstance(exc,(ValueError,TypeError,UnicodeError,FilesystemError)): return error_response(400,'Invalid skill creation request')
            return error_response(500,'Skill creation preparation failed')


_AUTH_METHOD = AuthService.authorized
_SERVICE_METHODS = (SkillCreateService.handle,SkillCreateService.validate,SkillCreateService._root_current,SkillCreateService._observe)
_BASE_METHODS = (WSGIApplication.__call__,WSGIApplication._handle,Router.dispatch,AuthService.authorized)


def _original_bound(obj, name, function):
    method = getattr(obj,name)
    return getattr(method,'__self__',None) is obj and getattr(method,'__func__',None) is function


def _overlap(path):
    pattern = re.escape(path)
    for name in re.findall(r'\{([A-Za-z_][A-Za-z_0-9]*)\}',path):
        pattern = pattern.replace(re.escape('{'+name+'}'),'[^/]+')
    return re.fullmatch(pattern,'/api/skills') is not None


def bind_skill_creation(application, *, service, authorize_global):
    """Original base/JWT only; no read grant can imply root creation authority."""
    if (type(application) is not WSGIApplication or type(application.router) is not Router
            or type(service) is not SkillCreateService or not callable(authorize_global)):
        raise ValueError('Original application and explicit skill creation authority required')
    base = application.router; auth = base.authorize; owner = getattr(auth,'__self__',None)
    if type(owner) is not AuthService or getattr(auth,'__func__',None) is not _AUTH_METHOD:
        raise ValueError('Original JWT authority required')
    if any(_overlap(path) for method,path,regex,handler,public in base._routes): raise ValueError('Skill creation route conflicts')
    names = ('handle','validate','_root_current','_observe')
    for name,function in zip(names,_SERVICE_METHODS):
        if not _original_bound(service,name,function): raise ValueError('Original service method and owner required')
    base_bindings = ((application,'__call__'),(application,'_handle'),(base,'dispatch'),(owner,'authorized'))
    for (obj,name),function in zip(base_bindings,_BASE_METHODS):
        if not _original_bound(obj,name,function): raise ValueError('Original transport method and owner required')
    service.validate()
    pins,scope = service._pins,service._scope
    routes = tuple(base._routes); auth_pins = (owner.store,owner.codec,owner.key,owner.clock)
    bound = application.max_body_bytes
    methods = tuple((obj,name,function) for (obj,name),function in zip(base_bindings,_BASE_METHODS))
    methods += tuple((service,name,function) for name,function in zip(names,_SERVICE_METHODS))
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
    def gate(request,scope,selection):
        check()
        if scope is not pins[0] or type(selection) is not SkillCreateSelection: raise PermissionError()
        if auth(request) is not True: raise PermissionError()
        check()
        if authorize_global(request,scope,selection) is not True: raise PermissionError()
        check()
        if auth(request) is not True: raise PermissionError()
        check(); return True
    protected = Router(initial)
    def invoke(request,params):
        selected = None
        def request_gate(candidate,scope,selection):
            nonlocal selected
            check()
            if candidate is not request or scope is not pins[0] or type(selection) is not SkillCreateSelection:
                raise PermissionError()
            if selected is None: selected = selection
            elif selection is not selected: raise PermissionError()
            return gate(request,scope,selection)
        return service.handle(request,request_authorize=request_gate)
    protected.add('POST','/api/skills',invoke)
    class Overlay:
        def dispatch(self,request):
            try: check()
            except Exception: return error_response(503,'Skill creation composition unavailable')
            return protected.dispatch(request) if request.path == '/api/skills' else base.dispatch(request)
    return WSGIApplication(Overlay(),max_body_bytes=bound)
