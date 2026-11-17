"""Explicit create-only archive installation into a cooperatively owned root.

The whole ZIP is validated in bounded memory before effects. Installation is NOT
atomic: partial new directories/files remain on uncertainty, with no recursive
cleanup or retry. The containing host must quarantine failed/abandoned mutations.
Inert file bytes confer no tool, MCP, provider, or execution authority.
"""
from dataclasses import dataclass
import os
import re
import stat

from .auth import AuthService
from .core import Request, Router, error_response, json_response
from .disclosure import DisclosureResponse
from .wsgi import WSGIApplication
from .workspace_upload import _multipart
from ..skills.archive_plan import (ArchivePlan, ArchiveFile, ArchiveLimit, plan_archive,
                                  MAX_ARCHIVE_BYTES, MAX_FILE_BYTES)
from ..skills.file_listing import SkillDirectoryScope
from ..tools.filesystem import ScopedFilesystem, FilesystemConflict, FilesystemError

MAX_JSON_BYTES = 6 * MAX_FILE_BYTES + 32
_PLAN = plan_archive
_MULTIPART = _multipart


class ImportUnavailable(RuntimeError): pass


@dataclass(frozen=True)
class SkillArchiveImportSelection:
    name: str
    plan: ArchivePlan


@dataclass(frozen=True)
class SkillArchiveImportProposal:
    scope: SkillDirectoryScope
    selection: SkillArchiveImportSelection
    parent_identity: tuple
    archive_sha256: str
    content_bytes: int
    files: int


def _plan_key(plan):
    if (type(plan) is not ArchivePlan or type(plan.name) is not str
            or type(plan.directories) is not tuple or any(type(p) is not str for p in plan.directories)
            or type(plan.files) is not tuple or type(plan.archive_sha256) is not str
            or type(plan.total_bytes) is not int): raise PermissionError()
    for f in plan.files:
        if (type(f) is not ArchiveFile or type(f.path) is not str or type(f.data) is not bytes
                or type(f.sha256) is not str): raise PermissionError()
    return (plan.name,plan.directories,tuple((f.path,f.data,f.sha256) for f in plan.files),plan.archive_sha256,plan.total_bytes)


def _unknown():
    return json_response(500,{'error':'Skill import outcome unknown; reconcile before retry',
                              'outcome':'unknown','retry':False})


class SkillArchiveImportService:
    def __init__(self, scope, *, authorize, mutation_authorize, import_authorize, current, writer_guard):
        if type(scope) is not SkillDirectoryScope or any(not callable(f) for f in (
                authorize,mutation_authorize,import_authorize,current,writer_guard)):
            raise ValueError('Exact owned root and independent import authority required')
        scope.validate()
        self.scope,self.authorize,self.mutate,self.import_authorize,self.current,self.guard = (
            scope,authorize,mutation_authorize,import_authorize,current,writer_guard)
        self._pins = (scope,authorize,mutation_authorize,import_authorize,current,writer_guard)
        self._scope = (scope.root,scope.device,scope.inode,scope.revision)

    def validate(self):
        if plan_archive is not _PLAN or _multipart is not _MULTIPART or any(a is not b for a,b in zip(
                (self.scope,self.authorize,self.mutate,self.import_authorize,self.current,self.guard),self._pins)):
            raise ImportUnavailable()
        self.scope.validate()
        if (self.scope.root,self.scope.device,self.scope.inode,self.scope.revision) != self._scope:
            raise ImportUnavailable()

    def _root_current(self):
        with ScopedFilesystem(self._scope[0]) as fs:
            info = os.fstat(fs._fd)
            if (info.st_dev,info.st_ino) != self._scope[1:3]: raise FilesystemConflict()
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise FilesystemError('Owner-only root required')
            fs._check_metadata(fs._fd)

    def _observe(self, fs, name, records):
        """Callback-free exact partial-tree receipt; never follows or repairs."""
        self._root_current()
        root = os.fstat(fs._fd)
        if (root.st_dev,root.st_ino) != self._scope[1:3]: raise FilesystemConflict()
        for path,(directory,version,data) in records.items():
            parts = [name]+(path.split('/') if path else [])
            with fs._directory(parts[:-1]) as fd:
                named = os.stat(parts[-1],dir_fd=fd,follow_symlinks=False)
                if fs._stat(named) != version: raise FilesystemConflict()
                if directory:
                    with fs._directory(parts) as child:
                        info = os.fstat(child)
                        if (fs._stat(info) != version or not stat.S_ISDIR(info.st_mode)
                                or info.st_dev != self._scope[1] or info.st_uid != os.geteuid()
                                or stat.S_IMODE(info.st_mode) != 0o700): raise FilesystemConflict()
                        fs._check_metadata(child)
                        expected = {p.rsplit('/',1)[-1] for p in records if p and p.rpartition('/')[0] == path}
                        if set(os.listdir(child)) != expected or fs._stat(os.fstat(child)) != version:
                            raise FilesystemConflict()
                else:
                    if (not stat.S_ISREG(named.st_mode) or named.st_nlink != 1
                            or named.st_dev != self._scope[1] or named.st_uid != os.geteuid()
                            or stat.S_IMODE(named.st_mode) != 0o600): raise FilesystemConflict()
                    file_fd = os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
                    try:
                        if fs._stat(os.fstat(file_fd)) != version: raise FilesystemConflict()
                        fs._check_metadata(file_fd)
                    finally: os.close(file_fd)
                    actual,observed = fs._read(fd,parts[-1])
                    if observed[0] != version or actual != data: raise FilesystemConflict()
                if fs._stat(os.stat(parts[-1],dir_fd=fd,follow_symlinks=False)) != version:
                    raise FilesystemConflict()
        self._root_current()

    def handle(self, request, *, request_authorize):
        attempted = False
        try:
            if not callable(request_authorize): raise ImportUnavailable()
            if (type(request) is not Request or request.method != 'POST' or request.target != '/api/skills/upload'
                    or type(request.body) is not bytes or type(request.headers) is not dict
                    or any(type(k) is not str or type(v) is not str for k,v in request.headers.items())): raise ValueError()
            if len(request.body) > MAX_JSON_BYTES: raise ArchiveLimit()
            filename,archive = _MULTIPART(request.header('Content-Type'),request.body,MAX_ARCHIVE_BYTES)
            if not filename.lower().endswith('.zip'): raise ValueError()
            plan = _PLAN(archive)
            plan_key = _plan_key(plan)
            selection = SkillArchiveImportSelection(plan.name,plan)
            pins = self._pins; scope_key = self._scope; scope = pins[0]
            headers = request.headers
            request_key = (request.method,request.target,request.body,tuple(headers.items()))
            proposal = SkillArchiveImportProposal(scope,selection,scope_key[1:3],plan.archive_sha256,plan.total_bytes,len(plan.files))
            proposal_key = (proposal.parent_identity,proposal.archive_sha256,proposal.content_bytes,proposal.files)

            def pure():
                self.validate()
                if self._pins is not pins or self._scope is not scope_key: raise ImportUnavailable()
                if (type(request.method) is not str or type(request.target) is not str or type(request.body) is not bytes
                        or request.headers is not headers or type(headers) is not dict
                        or any(type(k) is not str or type(v) is not str for k,v in headers.items())
                        or (request.method,request.target,request.body,tuple(headers.items())) != request_key
                        or type(selection.name) is not str or selection.name != plan_key[0] or selection.plan is not plan
                        or _plan_key(plan) != plan_key or proposal.scope is not scope or proposal.selection is not selection
                        or proposal.parent_identity is not proposal_key[0]
                        or type(proposal.archive_sha256) is not str or proposal.archive_sha256 != proposal_key[1]
                        or type(proposal.content_bytes) is not int or proposal.content_bytes != proposal_key[2]
                        or type(proposal.files) is not int or proposal.files != proposal_key[3]): raise PermissionError()

            def allow():
                pure()
                if pins[4](scope,selection) is not True: raise PermissionError()
                pure()
                if pins[1](scope,selection) is not True: raise PermissionError()
                pure()
                if pins[2](request,proposal) is not True: raise PermissionError()
                pure()
                if pins[3](request,proposal) is not True: raise PermissionError()
                pure()
                if request_authorize(request,scope,selection) is not True: raise PermissionError()
                pure()

            allow()
            with pins[5](scope,selection):
                allow(); self._root_current()
                with ScopedFilesystem(scope.root,max_bytes=MAX_FILE_BYTES) as fs:
                    def missing():
                        pure(); self._root_current()
                        if (os.fstat(fs._fd).st_dev,os.fstat(fs._fd).st_ino) != scope_key[1:3]: raise FilesystemConflict()
                        try: os.stat(selection.name,dir_fd=fs._fd,follow_symlinks=False)
                        except FileNotFoundError: return
                        raise FilesystemConflict()
                    missing(); allow(); missing()
                    attempted = True
                    os.mkdir(selection.name,mode=0o700,dir_fd=fs._fd)
                    records = {'':(True,fs._stat(os.stat(selection.name,dir_fd=fs._fd,follow_symlinks=False)),None)}
                    self._observe(fs,selection.name,records)
                    entries = [(path,True,None) for path in plan.directories]
                    entries += [(f.path,False,f.data) for f in plan.files]
                    for path,directory,data in entries:
                        parent,_,leaf = path.rpartition('/')
                        allow(); self._observe(fs,selection.name,records)
                        with fs._directory([selection.name]+(parent.split('/') if parent else [])) as fd:
                            if fs._stat(os.fstat(fd)) != records[parent][1]: raise FilesystemConflict()
                            if directory:
                                os.mkdir(leaf,mode=0o700,dir_fd=fd)
                                records[path] = (True,fs._stat(os.stat(leaf,dir_fd=fd,follow_symlinks=False)),None)
                                records[parent] = (True,fs._stat(os.fstat(fd)),None)
                                self._observe(fs,selection.name,records)
                            else:
                                file_fd = os.open(leaf,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
                                try:
                                    records[path] = (False,fs._stat(os.fstat(file_fd)),b'')
                                    records[parent] = (True,fs._stat(os.fstat(fd)),None)
                                    self._observe(fs,selection.name,records)
                                    offset = 0
                                    while offset < len(data):
                                        allow(); self._observe(fs,selection.name,records)
                                        if fs._stat(os.fstat(file_fd)) != records[path][1]: raise FilesystemConflict()
                                        chunk = data[offset:offset+65536]
                                        count = os.write(file_fd,chunk)
                                        if type(count) is not int or not 0 < count <= len(chunk): raise OSError()
                                        offset += count
                                        records[path] = (False,fs._stat(os.fstat(file_fd)),data[:offset])
                                    allow(); self._observe(fs,selection.name,records)
                                    if fs._stat(os.fstat(file_fd)) != records[path][1]: raise FilesystemConflict()
                                    os.fsync(file_fd)
                                    self._observe(fs,selection.name,records)
                                finally: os.close(file_fd)
                    for path in reversed(('',)+plan.directories):
                        allow(); self._observe(fs,selection.name,records)
                        with fs._directory([selection.name]+(path.split('/') if path else [])) as fd:
                            if fs._stat(os.fstat(fd)) != records[path][1]: raise FilesystemConflict()
                            os.fsync(fd)
                    allow(); self._observe(fs,selection.name,records); os.fsync(fs._fd)
                    self._observe(fs,selection.name,records)
                allow(); self._root_current()

            def acknowledge():
                with pins[5](scope,selection):
                    allow()
                    with ScopedFilesystem(scope.root,max_bytes=MAX_FILE_BYTES) as fs:
                        self._observe(fs,selection.name,records)
                    pure()
                allow()
                with ScopedFilesystem(scope.root,max_bytes=MAX_FILE_BYTES) as fs:
                    self._observe(fs,selection.name,records)
                pure()
            acknowledge()
            response = json_response(201,{'name':selection.name,'files':len(plan.files)})
            return DisclosureResponse(response.status,response.body,response.headers,acknowledge)
        except BaseException as exc:
            if attempted: return _unknown()
            if not isinstance(exc,Exception): raise
            if isinstance(exc,PermissionError): return error_response(403,'Skill import denied')
            if isinstance(exc,ImportUnavailable): return error_response(503,'Skill import unavailable')
            if isinstance(exc,ArchiveLimit): return error_response(413,'Skill archive exceeds supported limit')
            if isinstance(exc,FilesystemConflict): return error_response(409,'Skill already exists or root changed')
            if isinstance(exc,(ValueError,TypeError,UnicodeError,FilesystemError)): return error_response(400,'Invalid skill archive request')
            return error_response(500,'Skill import preparation failed')


_AUTH_METHOD = AuthService.authorized
_SERVICE_METHODS = (SkillArchiveImportService.handle,SkillArchiveImportService.validate,SkillArchiveImportService._root_current,SkillArchiveImportService._observe)
_BASE_METHODS = (WSGIApplication.__call__,WSGIApplication._handle,Router.dispatch,AuthService.authorized)


def _original_bound(obj, name, function):
    method = getattr(obj,name)
    return getattr(method,'__self__',None) is obj and getattr(method,'__func__',None) is function


def _overlap(path):
    pattern = re.escape(path)
    for name in re.findall(r'\{([A-Za-z_][A-Za-z_0-9]*)\}',path):
        pattern = pattern.replace(re.escape('{'+name+'}'),'[^/]+')
    return re.fullmatch(pattern,'/api/skills/upload') is not None


def bind_skill_archive_imports(application, *, service, authorize_global):
    """Original base/JWT only; no read grant can imply root import authority."""
    if (type(application) is not WSGIApplication or type(application.router) is not Router
            or type(service) is not SkillArchiveImportService or not callable(authorize_global)):
        raise ValueError('Original application and explicit skill import authority required')
    base = application.router; auth = base.authorize; owner = getattr(auth,'__self__',None)
    if type(owner) is not AuthService or getattr(auth,'__func__',None) is not _AUTH_METHOD:
        raise ValueError('Original JWT authority required')
    if any(_overlap(path) for method,path,regex,handler,public in base._routes): raise ValueError('Skill import route conflicts')
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
        if scope is not pins[0] or type(selection) is not SkillArchiveImportSelection: raise PermissionError()
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
            if candidate is not request or scope is not pins[0] or type(selection) is not SkillArchiveImportSelection:
                raise PermissionError()
            if selected is None: selected = selection
            elif selection is not selected: raise PermissionError()
            return gate(request,scope,selection)
        return service.handle(request,request_authorize=request_gate)
    protected.add('POST','/api/skills/upload',invoke)
    class Overlay:
        def dispatch(self,request):
            try: check()
            except Exception: return error_response(503,'Skill import composition unavailable')
            return protected.dispatch(request) if request.path == '/api/skills/upload' else base.dispatch(request)
    return WSGIApplication(Overlay(),max_body_bytes=bound)
