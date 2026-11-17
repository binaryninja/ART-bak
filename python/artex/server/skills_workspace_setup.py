"""One explicit Skills workspace, composed from original exact-base siblings.

No default mounting, root inference, SQL, permissions, files or listener creation.
The same exact root and exclusive guard bind independent read/write authorities.
PUT remains upsert; no stale-editor or create-only guarantee is invented.
"""
from dataclasses import dataclass, field
import re
from .core import Router, error_response
from .wsgi import WSGIApplication
from .skill_catalog_setup import bind_skill_catalog_reads
from .skill_files_setup import bind_skill_files_reads
from .skill_content_setup import bind_skill_content_reads
from .skill_content_write import bind_skill_content_writes
from .skill_directory_create import bind_skill_directory_creation
from .skill_create import bind_skill_creation
from .skill_path_delete import bind_skill_path_deletes
from .skill_archive_import import bind_skill_archive_imports

_BINDERS=(bind_skill_catalog_reads,bind_skill_files_reads,bind_skill_content_reads,
          bind_skill_content_writes,bind_skill_directory_creation,bind_skill_creation,bind_skill_path_deletes,
          bind_skill_archive_imports)
_PATTERNS=(re.compile(r'/api/skills\Z'),re.compile(r'/api/skills/[^/]+/files\Z'),
           re.compile(r'/api/skills/[^/]+/files/.+\Z'),re.compile(r'/api/skills/[^/]+/dirs\Z'),
           re.compile(r'/api/skills/upload\Z'))
_ALLOWED=(('GET',),('GET','HEAD'),('GET','HEAD','PUT'),('POST',),('POST',))


def _key(method):return getattr(method,'__self__',None),getattr(method,'__func__',method)
def _same(method,key):return all(a is b for a,b in zip(_key(method),key))


def _factories():
    if any(a is not b for a,b in zip((bind_skill_catalog_reads,bind_skill_files_reads,
            bind_skill_content_reads,bind_skill_content_writes,bind_skill_directory_creation,bind_skill_creation,
            bind_skill_path_deletes,bind_skill_archive_imports),_BINDERS)):
        raise ValueError('Original Skills workspace binders required')


@dataclass(frozen=True)
class SkillsWorkspaceBundle:
    application: object=field(repr=False)
    catalog: object=field(repr=False)
    files: object=field(repr=False)
    content: object=field(repr=False)
    write: object=field(repr=False)
    directory: object=field(repr=False)
    create: object=field(default=None,repr=False)
    delete: object=field(default=None,repr=False)
    importer: object=field(default=None,repr=False)


class SkillsWorkspaceApplication:
    def __init__(self,base,services,views,scope,guard,*,create_view=None,delete_view=None,import_view=None):
        self.original=base;self.services=services;self.views=views
        self.scope=scope;self.guard=guard
        self.create_view=create_view;self.delete_view=delete_view;self.import_view=import_view
        self._pins=(base,services,views,scope,guard,create_view,delete_view,import_view)
        self._base_methods=tuple((_key(getattr(base,name)),name) for name in ('__call__','_handle'))
        self._view_methods=tuple(tuple((_key(getattr(view,name)),name) for name in
            (('__call__','_check') if index<3 else ('__call__','_handle')))
            for index,view in enumerate(views))
        self._routers=tuple((view.router,_key(view.router.dispatch),view.max_body_bytes)
                           for view in views[3:])
        self._checks=tuple(_key(view._check) for view in views[:3])
        self.validate()

    def validate(self):
        _factories()
        if any(a is not b for a,b in zip((self.original,self.services,self.views,self.scope,self.guard,
                                        self.create_view,self.delete_view,self.import_view),self._pins)):
            raise PermissionError('Workspace composition changed')
        for optional in (self.create_view,self.delete_view,self.import_view):
            if optional is not None and not any(optional is view for view in self.views[5:]):
                raise PermissionError('Workspace extension binding changed')
        for key,name in self._base_methods:
            if not _same(getattr(self.original,name),key):raise PermissionError()
        catalog,files,content,write,directory=self.services[:5]
        mutations=self.services[3:]
        for service in (catalog,files,content):service._check()
        for service in mutations:service.validate()
        if (catalog.reader.scope is not self.scope or files.reader.scope is not self.scope
                or content.reader.scope is not self.scope or write.scope is not self.scope
                or any(service.scope is not self.scope for service in mutations) or catalog.reader.content_reader is not content.reader
                or catalog.reader.usage_reader is not None):raise PermissionError('One exact filesystem scope required')
        if any(value is not self.guard for value in (catalog.reader.guard,files.reader.guard,
                content.reader.guard,*(service.guard for service in mutations))):
            raise PermissionError('One exclusive writer guard required')
        for view,methods in zip(self.views,self._view_methods):
            for key,name in methods:
                if not _same(getattr(view,name),key):raise PermissionError('Workspace delegate changed')
        for view,key in zip(self.views[:3],self._checks):
            if not _same(view._check,key):raise PermissionError()
            view._check()
        for view,(router,dispatch,bound) in zip(self.views[3:],self._routers):
            if view.router is not router or not _same(router.dispatch,dispatch) or type(view.max_body_bytes) is not int or view.max_body_bytes!=bound:
                raise PermissionError('Write transport changed')

    def __call__(self,environ,start_response):
        try:self.validate()
        except Exception:return self._error(environ,start_response,503,'Skills workspace unavailable')
        path=environ.get('PATH_INFO','');method=environ.get('REQUEST_METHOD','GET')
        family=next((i for i,pattern in enumerate(_PATTERNS) if type(path) is str and pattern.fullmatch(path)),None)
        if family is None or family==4 and self.import_view is None:
            return self.original(environ,start_response)
        allowed=_ALLOWED[family]+(('POST',) if family==0 and self.create_view is not None else ())
        if family==2 and self.delete_view is not None:allowed+=('DELETE',)
        if type(method) is not str or method.upper() not in allowed:
            # Use the original transport and JWT, not an unauthenticated shortcut.
            outer=self
            class Dispatch:
                def dispatch(self,request):
                    outer.validate()
                    if outer.original.router.authorize(request) is not True:return error_response(401,'Unauthorized')
                    outer.validate()
                    response=error_response(405,'Method not allowed')
                    return type(response)(response.status,response.body,{**response.headers,'Allow':', '.join(allowed)})
            return WSGIApplication(Dispatch(),max_body_bytes=self.original.max_body_bytes)(environ,start_response)
        if family==4:view=self.import_view
        elif family==0 and method.upper()=='POST':view=self.create_view
        elif family==2 and method.upper()=='DELETE':view=self.delete_view
        else:
            index=0 if family==0 else 1 if family==1 else (3 if method.upper()=='PUT' else 2) if family==2 else 4
            view=self.views[index]
        def checked_start(status,headers,exc_info=None):
            self.validate()
            result=start_response(status,headers) if exc_info is None else start_response(status,headers,exc_info)
            self.validate()
            return result
        body=view(environ,checked_start)
        try:self.validate()
        except BaseException:
            if callable(getattr(body,'close',None)):body.close()
            raise
        def output():
            try:
                iterator=iter(body)
                while True:
                    self.validate()
                    try:chunk=next(iterator)
                    except StopIteration:break
                    self.validate()
                    yield chunk
            finally:
                if callable(getattr(body,'close',None)):body.close()
        return output()

    @staticmethod
    def _error(environ,start_response,status,message):
        class Dispatch:
            def dispatch(self,request):return error_response(status,message)
        return WSGIApplication(Dispatch())(environ,start_response)


_APPLICATION_METHODS=(SkillsWorkspaceApplication.__init__,SkillsWorkspaceApplication.validate,SkillsWorkspaceApplication.__call__)


def bind_skills_workspace(application,*,catalog_service,files_service,content_service,
        write_service,directory_service,authorize_catalog,authorize_files,authorize_content,
        authorize_write,authorize_directory,create_service=None,authorize_create=None,
        delete_service=None,authorize_delete=None,import_service=None,authorize_import=None):
    """Bind original siblings to one untouched base; all grants are explicit.

    Filesystem catalog omits SQL usage statistics. Existing DTO placeholders must
    be labelled unavailable by the Python frontend, never displayed as measured.
    The caller owns guard/exclusivity and any mutation-uncertainty quarantine.
    """
    _factories()
    if (create_service is None) != (authorize_create is None):
        raise ValueError('Creation service and independent grant must be supplied together')
    if (delete_service is None) != (authorize_delete is None):
        raise ValueError('Deletion service and independent grant must be supplied together')
    if (import_service is None) != (authorize_import is None):
        raise ValueError('Import service and independent grant must be supplied together')
    if (type(application) is not WSGIApplication or type(application.router) is not Router
            or any(a is not b for a,b in zip((SkillsWorkspaceApplication.__init__,
                SkillsWorkspaceApplication.validate,SkillsWorkspaceApplication.__call__),_APPLICATION_METHODS))):
        raise ValueError('Original base and workspace implementation required')
    # Each binder validates its own exact service/reader/JWT/route authority.
    views=(bind_skill_catalog_reads(application,catalog_service=catalog_service,authorize_global=authorize_catalog).application,
        bind_skill_files_reads(application,files_service=files_service,authorize_global=authorize_files).application,
        bind_skill_content_reads(application,content_service=content_service,authorize_global=authorize_content).application,
        bind_skill_content_writes(application,service=write_service,authorize_global=authorize_write),
        bind_skill_directory_creation(application,service=directory_service,authorize_global=authorize_directory))
    services=(catalog_service,files_service,content_service,write_service,directory_service)
    create_view=delete_view=import_view=None
    if create_service is not None:
        create_view=bind_skill_creation(application,service=create_service,authorize_global=authorize_create)
        views+=(create_view,)
        services+=(create_service,)
    if delete_service is not None:
        delete_view=bind_skill_path_deletes(application,service=delete_service,authorize_global=authorize_delete)
        views+=(delete_view,)
        services+=(delete_service,)
    if import_service is not None:
        import_view=bind_skill_archive_imports(application,service=import_service,authorize_global=authorize_import)
        views+=(import_view,)
        services+=(import_service,)
    scope=content_service.reader.scope;guard=content_service.reader.guard
    view=SkillsWorkspaceApplication(application,services,views,scope,guard,
        create_view=create_view,delete_view=delete_view,import_view=import_view)
    return SkillsWorkspaceBundle(view,*services[:5],create_service,delete_service,import_service)
