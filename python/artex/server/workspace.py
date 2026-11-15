"""Opt-in read-only workspace API using audited no-follow filesystem primitives.

Host supplies an explicitly authorized task/root/revision and pinned root inode.
No global/cwd/default root, write routes, commands, arbitrary absolute paths,
symlinks, hardlinks or special files. Host guard must exclude hostile filesystem
renames/mount changes and serialize scope/revision changes. Existing filesystem
primitive private helpers are reused intentionally, with no writes.

Source list/read DTOs and attachment name encoding retained. Downloads are finite
buffered snapshots (8MiB default, max32MiB), not Go ServeFile streaming/ranges or
conditional requests. Unsupported range/conditional headers fail explicitly.
Read inline cap2MiB, listing2000 entries/1MiB JSON. No implicit registrations.
"""
from dataclasses import dataclass
import os,stat
from urllib.parse import parse_qsl,urlsplit,quote
from .core import Response,json_response,error_response
from ..tools.filesystem import ScopedFilesystem,FilesystemError,FilesystemConflict

@dataclass(frozen=True)
class WorkspaceBinding:
    task_id:int
    root:str
    root_device:int
    root_inode:int
    revision:str

class WorkspaceDenied(PermissionError):pass
class WorkspaceUnavailable(RuntimeError):pass
class WorkspaceLimit(RuntimeError):pass

class WorkspaceService:
    def __init__(self,*,resolve,authorize,current,lifecycle_guard,max_download_bytes=8*1024*1024,max_entries=2000):
        if any(not callable(f) for f in (resolve,authorize,current,lifecycle_guard)):raise ValueError('Explicit workspace policies required')
        if type(max_download_bytes) is not int or not 2*1024*1024<=max_download_bytes<=32*1024*1024:raise ValueError('Invalid download limit')
        if type(max_entries) is not int or not 1<=max_entries<=2000:raise ValueError('Invalid entry limit')
        self.resolve,self.authorize,self.current,self.guard=resolve,authorize,current,lifecycle_guard
        self.max_bytes,self.max_entries=max_download_bytes,max_entries
    def register(self,router):
        for action in ('list','read','download'):
            router.add('GET','/api/workspace/'+action,lambda req,params,a=action:self.handle(a,req))
    def handle(self,action,request):
        try:
            if action not in ('list','read','download') or request.body:raise FilesystemError()
            query=urlsplit(request.target).query
            if len(query)>16384:raise FilesystemError()
            pairs=parse_qsl(query,keep_blank_values=True,encoding='utf-8',errors='strict',max_num_fields=2)
            if len(pairs)>1 or any(k!='path' for k,v in pairs):raise FilesystemError()
            path=pairs[0][1] if pairs else ''
            if len(path.encode())>4096:raise FilesystemError()
            if any(request.header(h) for h in ('Range','If-Range','If-Modified-Since','If-None-Match')):return error_response(400,'Conditional and range requests unsupported')
            binding=self.resolve(request)
            if type(binding) is not WorkspaceBinding:raise WorkspaceUnavailable()
            identity=(binding.task_id,binding.root,binding.root_device,binding.root_inode,binding.revision)
            if type(binding.task_id) is not int or not 0<binding.task_id<2**63 or type(binding.revision) is not str or not binding.revision or len(binding.revision.encode())>256:raise WorkspaceUnavailable()
            if any(type(x) is not int or x<0 for x in (binding.root_device,binding.root_inode)):raise WorkspaceUnavailable()
            def check():
                if (binding.task_id,binding.root,binding.root_device,binding.root_inode,binding.revision)!=identity:raise WorkspaceUnavailable()
                if self.current(binding) is not True:raise WorkspaceDenied()
                if (binding.task_id,binding.root,binding.root_device,binding.root_inode,binding.revision)!=identity:raise WorkspaceUnavailable()
            with self.guard(binding):
                check()
                if self.authorize(request,action,binding,path) is not True:raise WorkspaceDenied()
                check()
                with ScopedFilesystem(binding.root,max_bytes=self.max_bytes,max_entries=self.max_entries) as fs:
                    info=os.fstat(fs._fd)
                    if (info.st_dev,info.st_ino)!=(binding.root_device,binding.root_inode):raise WorkspaceUnavailable()
                    parts=fs._parts(path,directory=action=='list');path='/'.join(parts)
                    if action=='list':
                        entries=[]
                        with fs._directory(parts) as fd:
                            with os.scandir(fd) as iterator:
                                for entry in iterator:
                                    check()
                                    if len(entries)>=self.max_entries:raise WorkspaceLimit()
                                    fs._parts(entry.name)
                                    meta=entry.stat(follow_symlinks=False)
                                    if not (stat.S_ISDIR(meta.st_mode) or stat.S_ISREG(meta.st_mode)) or (stat.S_ISREG(meta.st_mode) and meta.st_nlink!=1):raise FilesystemError()
                                    entries.append({'name':entry.name,'path':('/'.join(parts+[entry.name])),'dir':stat.S_ISDIR(meta.st_mode),'size':meta.st_size,'mtime':meta.st_mtime_ns//1000000})
                        entries.sort(key=lambda e:(not e['dir'],e['name'].lower()))
                        response=json_response(200,{'path':path,'entries':entries})
                        if len(response.body)>1048576:raise WorkspaceLimit()
                    else:
                        with fs._directory(parts[:-1]) as fd:
                            meta=os.stat(parts[-1],dir_fd=fd,follow_symlinks=False)
                            if not stat.S_ISREG(meta.st_mode) or meta.st_nlink!=1:raise FilesystemError()
                            if action=='read' and meta.st_size>2*1024*1024:
                                response=json_response(200,{'path':path,'size':meta.st_size,'too_large':True,'binary':True})
                            else:
                                if meta.st_size>self.max_bytes:raise WorkspaceLimit()
                                data,_=fs._read(fd,parts[-1]);check()
                                if action=='download':
                                    name=parts[-1];fallback=''.join(c if 32<=ord(c)<127 and c not in '\\";' else '_' for c in name)
                                    response=Response(200,data,{'Content-Type':'application/octet-stream','Content-Disposition':'attachment; filename="'+fallback+'"; filename*=UTF-8\'\''+quote(name,safe=''),'X-Content-Type-Options':'nosniff'})
                                elif len(data)>2*1024*1024:
                                    response=json_response(200,{'path':path,'size':len(data),'too_large':True,'binary':True})
                                else:
                                    try:content=data.decode('utf-8');binary='\0' in content
                                    except UnicodeError:content='';binary=True
                                    result={'path':path,'size':len(data),'binary':binary}
                                    if not binary:result['content']=content
                                    response=json_response(200,result)
                    check()
                return response
        except WorkspaceDenied:return error_response(403,'Workspace access denied')
        except WorkspaceUnavailable:return error_response(503,'Workspace capability unavailable')
        except WorkspaceLimit:return error_response(413,'Workspace limit exceeded')
        except FileNotFoundError:return error_response(404,'Workspace path not found')
        except FilesystemConflict:return error_response(409,'Workspace changed during read')
        except (FilesystemError,ValueError,UnicodeError,TypeError):return error_response(400,'Invalid workspace path or request')
        except OSError:return error_response(400,'Workspace path unavailable')
        except Exception:return error_response(500,'Workspace operation failed')
