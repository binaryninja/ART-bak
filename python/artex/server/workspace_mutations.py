"""Explicit source mkdir/delete routes, safe nonrecursive POSIX subset only.

POST mkdir {path} -> {ok:true,path}; DELETE delete?path -> {ok:true}.
No default registration, uploads, recursion, parent mkdir or root inference.
Existing directories are idempotent; new directories0700 (source0755 differs).
Nonempty-directory deletion returns409 with no partial traversal/deletion.
Explicit immutable per-action policy receives task/root/revision and observed
parent/target fingerprint. Delete authority must cover permanent removal, not
just read/write permission. Host-exclusive writer guard is mandatory. Uncertain
post-mutation outcomes never claim rollback or automatically retry.
"""
from dataclasses import dataclass
from urllib.parse import urlsplit,parse_qsl
import json,errno
from .core import json_response,error_response
from .workspace import WorkspaceBinding,WorkspaceDenied,WorkspaceUnavailable
from .workspace_write import _pairs
from ..tools.filesystem import FilesystemError,FilesystemConflict
from ..tools.workspace_mutations import WorkspaceMutations,MutationTarget,WorkspaceMutationUncertain,WorkspaceMutationUnsupported

@dataclass(frozen=True)
class WorkspaceMutationProposal:
    binding:WorkspaceBinding
    target:MutationTarget

class WorkspaceMutationService:
    def __init__(self,*,resolve,authorize,current,writer_guard,mutation_authorize=None,delete_authorize=None):
        if any(not callable(f) for f in (resolve,authorize,current,writer_guard)):raise ValueError('Explicit workspace authority required')
        if any(f is not None and not callable(f) for f in (mutation_authorize,delete_authorize)):raise ValueError('Invalid mutation policy')
        self.resolve,self.authorize,self.current,self.guard=resolve,authorize,current,writer_guard
        self.mutate,self.delete=mutation_authorize,delete_authorize
    def register(self,router):
        router.add('POST','/api/workspace/mkdir',lambda req,p:self.handle('mkdir',req))
        router.add('DELETE','/api/workspace/delete',lambda req,p:self.handle('delete',req))
    def handle(self,action,request):
        changed=False
        try:
            query=urlsplit(request.target).query
            if action=='mkdir':
                if query or len(request.body)>32768:raise ValueError()
                data=json.loads(request.body.decode(),object_pairs_hook=_pairs)
                if type(data) is not dict or set(data)!={'path'}:raise ValueError()
                path=data['path']
            elif action=='delete':
                if request.body or len(query)>16384:raise ValueError()
                pairs=parse_qsl(query,keep_blank_values=True,strict_parsing=True,errors='strict',max_num_fields=2)
                if len(pairs)!=1 or pairs[0][0]!='path':raise ValueError()
                path=pairs[0][1]
            else:raise ValueError()
            if type(path) is not str or not path or len(path.encode())>4096 or path.startswith('/') or '\\' in path or any(ord(c)<32 for c in path) or any(p in ('','.','..') for p in path.split('/')):raise ValueError()
            if self.mutate is None or (action=='delete' and self.delete is None):raise WorkspaceUnavailable()
            binding=self.resolve(request)
            if type(binding) is not WorkspaceBinding or type(binding.task_id) is not int or not 0<binding.task_id<2**63 or type(binding.root) is not str or not binding.root.startswith('/') or type(binding.revision) is not str or not binding.revision or '\0' in binding.revision or len(binding.revision.encode())>256:raise WorkspaceUnavailable()
            if any(type(v) is not int or v<0 for v in (binding.root_device,binding.root_inode)):raise WorkspaceUnavailable()
            identity=(binding.task_id,binding.root,binding.root_device,binding.root_inode,binding.revision)
            def check():
                if (binding.task_id,binding.root,binding.root_device,binding.root_inode,binding.revision)!=identity:raise WorkspaceUnavailable()
                if self.current(binding) is not True:raise WorkspaceDenied()
                if (binding.task_id,binding.root,binding.root_device,binding.root_inode,binding.revision)!=identity:raise WorkspaceUnavailable()
            with self.guard(binding):
                check()
                if self.authorize(request,action,binding,path) is not True:raise WorkspaceDenied()
                check()
                with WorkspaceMutations(binding.root,root_device=binding.root_device,root_inode=binding.root_inode) as operations:
                    target=operations.prepare(action,path);proposal=WorkspaceMutationProposal(binding,target)
                    def grant():
                        check()
                        if self.mutate(request,proposal) is not True:raise WorkspaceDenied()
                        check()
                        if action=='delete':
                            if self.delete(request,proposal) is not True:raise WorkspaceDenied()
                            check()
                    class Context:
                        def check(self):grant()
                    grant();changed=operations.apply(target,Context());check()
                check()
            return json_response(200,{'ok':True,'path':path} if action=='mkdir' else {'ok':True})
        except BaseException as exc:
            if changed or isinstance(exc,WorkspaceMutationUncertain):return json_response(500,{'error':'Workspace mutation outcome uncertain; reconcile before retrying','outcome':'unknown','retry':False})
            if not isinstance(exc,Exception):raise
            if isinstance(exc,WorkspaceDenied):return error_response(403,'Workspace mutation denied')
            if isinstance(exc,WorkspaceUnavailable):return error_response(503,'Workspace mutation capability unavailable')
            if isinstance(exc,(WorkspaceMutationUnsupported,FilesystemConflict)):return error_response(409,'Workspace mutation unsupported or target changed')
            if isinstance(exc,FileNotFoundError):return error_response(404,'Workspace path not found')
            if isinstance(exc,OSError) and exc.errno in (errno.ENOTDIR,errno.ELOOP):return error_response(400,'Invalid workspace mutation path')
            if isinstance(exc,(FilesystemError,ValueError,TypeError,UnicodeError)):return error_response(400,'Invalid workspace mutation')
            return error_response(500,'Workspace mutation failed')
