"""Explicit opt-in POST /api/workspace/write for host-exclusive POSIX roots.

Source JSON {path,content} -> {ok:true,path}. UTF-8 text only, max2MiB by
 default. Unlike Go, no mkdir, binary/Windows support or permission flattening:
 existing parent required, new0600, ordinary rwx preserved and special bits
 stripped; existing/inherited ACL/xattrs or ownership mismatch fail closed.

JWT/read permission does not authorize writes. Host writer_guard must exclude
all writers/root renames/mount changes and serialize revision/authority changes.
Mutation policy receives immutable exact binding/path/content digest and bytes,
and the request containing content, at admission AND each primitive checkpoint.
No policy is derived from model/tool provenance. All primitive CAS/fsync checks
remain intact. Once Write begins any failure is conservatively uncertain, with
no retry or rollback claim; in particular directory fsync may fail after rename.
No default wiring, mkdir/delete/upload, execution or background work.
"""
from dataclasses import dataclass
import hashlib,json,os
from urllib.parse import urlsplit
from .core import json_response,error_response
from .workspace import WorkspaceBinding,WorkspaceDenied,WorkspaceUnavailable
from ..tools.filesystem import ScopedFilesystem,FilesystemError

@dataclass(frozen=True)
class WorkspaceWriteProposal:
    task_id:int
    root:str
    root_device:int
    root_inode:int
    revision:str
    path:str
    content_bytes:int
    content_sha256:str


def _pairs(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('Duplicate key')
        result[key]=value
    return result

class WorkspaceWriteService:
    def __init__(self,*,resolve,authorize,current,writer_guard,mutation_authorize=None,max_bytes=2*1024*1024):
        if any(not callable(f) for f in (resolve,authorize,current,writer_guard)):raise ValueError('Explicit workspace and exclusive writer authority required')
        if mutation_authorize is not None and not callable(mutation_authorize):raise ValueError('Invalid mutation policy')
        if type(max_bytes) is not int or not 1<=max_bytes<=2*1024*1024:raise ValueError('Invalid write byte limit')
        self.resolve,self.authorize,self.current,self.guard=resolve,authorize,current,writer_guard
        self.mutate,self.max_bytes=mutation_authorize,max_bytes
    def register(self,router):
        router.add('POST','/api/workspace/write',lambda req,params:self.handle(req))
    def handle(self,request):
        started=False
        try:
            if urlsplit(request.target).query:raise ValueError('Query unsupported')
            # JSON escaping can expand text by six bytes per code point.
            if len(request.body)>6*self.max_bytes+32768:return error_response(413,'Workspace write exceeds limit')
            data=json.loads(request.body.decode('utf-8'),object_pairs_hook=_pairs,
                parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
            if type(data) is not dict or set(data)!={'path','content'}:raise ValueError('Exact path and content required')
            path,content=data['path'],data['content']
            if type(path) is not str or not path or len(path.encode())>4096 or type(content) is not str or '\0' in content:raise ValueError()
            encoded=content.encode('utf-8')
            if len(encoded)>self.max_bytes:return error_response(413,'Workspace write exceeds limit')
            # Reject rather than normalize a potentially different approved path.
            if path.startswith('/') or '\\' in path or any(ord(c)<32 for c in path) or any(p in ('','.','..') for p in path.split('/')):raise ValueError()
            if self.mutate is None:raise WorkspaceUnavailable()
            binding=self.resolve(request)
            if type(binding) is not WorkspaceBinding:raise WorkspaceUnavailable()
            if type(binding.task_id) is not int or not 0<binding.task_id<2**63 or type(binding.root) is not str or not binding.root.startswith('/') or type(binding.revision) is not str or not binding.revision or '\0' in binding.revision or len(binding.revision.encode())>256:raise WorkspaceUnavailable()
            if any(type(v) is not int or v<0 for v in (binding.root_device,binding.root_inode)):raise WorkspaceUnavailable()
            identity=(binding.task_id,binding.root,binding.root_device,binding.root_inode,binding.revision)
            proposal=WorkspaceWriteProposal(*identity,path,len(encoded),hashlib.sha256(encoded).hexdigest())
            def check():
                if (binding.task_id,binding.root,binding.root_device,binding.root_inode,binding.revision)!=identity:raise WorkspaceUnavailable()
                if self.current(binding) is not True:raise WorkspaceDenied()
                if (binding.task_id,binding.root,binding.root_device,binding.root_inode,binding.revision)!=identity:raise WorkspaceUnavailable()
            def grant():
                check()
                if self.mutate(request,binding,proposal) is not True:raise WorkspaceDenied()
                check()
            with self.guard(binding):
                check()
                if self.authorize(request,binding,proposal) is not True:raise WorkspaceDenied()
                grant()
                with ScopedFilesystem(binding.root,max_bytes=self.max_bytes) as fs:
                    info=os.fstat(fs._fd)
                    if (info.st_dev,info.st_ino)!=identity[2:4]:raise WorkspaceUnavailable()
                    fs._validate('Write',{'file_path':path,'content':content})
                    class Context:
                        def check(self):
                            grant()
                            pinned=os.fstat(fs._fd)
                            if (pinned.st_dev,pinned.st_ino)!=identity[2:4]:raise WorkspaceUnavailable()
                    ctx=Context();ctx.check()
                    started=True
                    fs._call('Write',ctx,{'file_path':path,'content':content})
                    # No success claim if authority changes during rename/fsync.
                    ctx.check()
                check()
            return json_response(200,{'ok':True,'path':path})
        except Exception as exc:
            if started:
                return json_response(500,{'error':'Workspace write outcome uncertain; reconcile before any retry','outcome':'unknown','retry':False})
            if isinstance(exc,WorkspaceDenied):return error_response(403,'Workspace write denied')
            if isinstance(exc,WorkspaceUnavailable):return error_response(503,'Workspace write capability unavailable')
            if isinstance(exc,(ValueError,TypeError,UnicodeError,FilesystemError)):return error_response(400,'Invalid workspace write')
            if isinstance(exc,FileNotFoundError):return error_response(404,'Workspace path not found')
            return error_response(500,'Workspace write preparation failed')
