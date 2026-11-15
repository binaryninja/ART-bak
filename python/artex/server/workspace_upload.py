"""Explicit bounded single-file multipart workspace upload subset.

Source POST /api/workspace/upload?path=<existing-dir> returns {uploaded:1}.
Only one form-data field named file with a plain quoted UTF-8 filename is accepted;
multiple files/fields, nested MIME, transfer encoding, filename*, quoted escapes,
non-browser boundary grammar and malformed framing fail before writes. No basename
normalization: separators/traversal are rejected, not silently redirected. Binary
bytes are stored as-is, never extracted/executed. No mkdir, global root, defaults
or recursive behavior. Entire body and existing overwrite snapshot are bounded.

Reuses audited ScopedFilesystem._replace CAS/no-follow/fsync/metadata checks.
Host-exclusive writer guard is required against external renames/mount changes.
Immutable approval includes exact destination, content digest/length and observed
existing fingerprint+digest (None means create). Every primitive checkpoint
rechecks authority. Any failure after entering replacement is unknown/no retry,
including cleanup or syscall interruption; never falsely report batch rollback.
"""
from dataclasses import dataclass
import errno,hashlib,os,re
from urllib.parse import parse_qsl,urlsplit
from .core import json_response,error_response
from .workspace import WorkspaceBinding,WorkspaceDenied,WorkspaceUnavailable
from ..tools.filesystem import ScopedFilesystem,FilesystemError,FilesystemConflict

@dataclass(frozen=True)
class UploadProposal:
    binding:WorkspaceBinding
    path:str
    size:int
    sha256:str
    previous_version:tuple|None
    parent_identity:tuple


def _multipart(content_type,body,max_bytes):
    match=re.fullmatch(r'multipart/form-data;\s*boundary=(?:"([A-Za-z0-9_-]{1,70})"|([A-Za-z0-9_-]{1,70}))',content_type,re.I)
    if not match:raise ValueError('Unsupported multipart content type')
    boundary=(match.group(1) or match.group(2)).encode('ascii');start=b'--'+boundary+b'\r\n'
    if not body.startswith(start):raise ValueError('Invalid multipart start')
    end=b'\r\n--'+boundary+b'--'
    if body.endswith(end+b'\r\n'):inner=body[len(start):-len(end)-2]
    elif body.endswith(end):inner=body[len(start):-len(end)]
    else:raise ValueError('Invalid multipart end')
    headers,sep,data=inner.partition(b'\r\n\r\n')
    if not sep or len(headers)>8192 or len(data)>max_bytes or b'\r\n--'+boundary in inner:raise ValueError('Multipart exceeds supported subset')
    fields={}
    for line in headers.split(b'\r\n'):
        if len(line)>4096 or b':' not in line:raise ValueError('Invalid part header')
        key,value=line.split(b':',1);key=key.decode('ascii').lower()
        if key not in ('content-disposition','content-type') or key in fields:raise ValueError('Unsupported part headers')
        # Validate before trimming: generic strip would erase malformed CR/LF,
        # vertical tab or form feed and silently accept broken MIME framing.
        if any(byte<32 and byte!=9 or byte==127 for byte in value):
            raise ValueError('Invalid raw header controls')
        fields[key]=value.strip(b' \t').decode('utf-8')
    if any(any(ord(c)<32 or ord(c)==127 for c in value) for value in fields.values()):raise ValueError('Invalid header text')
    if fields.get('content-type','').lower().startswith('multipart/'):
        raise ValueError('Nested multipart unsupported')
    disposition=re.fullmatch(r'form-data;\s*name="file";\s*filename="([^"\\]+)"',fields.get('content-disposition',''))
    if not disposition:raise ValueError('Exactly one named file required')
    filename=disposition.group(1)
    if filename in ('.','..') or '/' in filename or '\\' in filename or len(filename.encode())>255:raise ValueError('Invalid filename')
    return filename,data

class WorkspaceUploadService:
    def __init__(self,*,resolve,authorize,current,writer_guard,upload_authorize=None,max_bytes=2*1024*1024):
        if any(not callable(f) for f in (resolve,authorize,current,writer_guard)):raise ValueError('Explicit workspace authority required')
        if upload_authorize is not None and not callable(upload_authorize):raise ValueError('Invalid upload policy')
        if type(max_bytes) is not int or not 1<=max_bytes<=2*1024*1024:raise ValueError('Bounded upload required')
        self.resolve,self.authorize,self.current,self.guard=resolve,authorize,current,writer_guard
        self.upload,self.max_bytes=upload_authorize,max_bytes
    def register(self,router):router.add('POST','/api/workspace/upload',lambda req,p:self.handle(req))
    def handle(self,request):
        attempted=False
        try:
            if len(request.body)>self.max_bytes+16384:return error_response(413,'Workspace upload exceeds limit')
            query=urlsplit(request.target).query
            if len(query)>16384:raise ValueError()
            pairs=parse_qsl(query,keep_blank_values=True,strict_parsing=True,errors='strict',max_num_fields=2)
            if len(pairs)>1 or any(k!='path' for k,v in pairs):raise ValueError()
            directory=pairs[0][1] if pairs else ''
            if len(directory.encode())>4096 or directory.startswith('/') or '\\' in directory or any(ord(c)<32 for c in directory) or (directory and any(p in ('','.','..') for p in directory.split('/'))):raise ValueError()
            filename,data=_multipart(request.header('Content-Type'),request.body,self.max_bytes)
            path=directory+'/'+filename if directory else filename
            if len(path.encode())>4096:raise ValueError()
            if self.upload is None:raise WorkspaceUnavailable()
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
                if self.authorize(request,binding,path) is not True:raise WorkspaceDenied()
                check()
                with ScopedFilesystem(binding.root,max_bytes=self.max_bytes) as fs:
                    root=os.fstat(fs._fd)
                    if (root.st_dev,root.st_ino)!=identity[2:4]:raise WorkspaceUnavailable()
                    parts=fs._parts(path)
                    with fs._directory(parts[:-1]) as fd:
                        parent=os.fstat(fd)
                        if parent.st_dev!=root.st_dev:raise FilesystemError('Mounted destination unsupported')
                        previous=fs._version(fd,parts[-1])
                        proposal=UploadProposal(binding,path,len(data),hashlib.sha256(data).hexdigest(),previous,(parent.st_dev,parent.st_ino))
                        def grant():
                            check()
                            if self.upload(request,proposal) is not True:raise WorkspaceDenied()
                            check()
                            pinned=os.fstat(fs._fd);pinned_parent=os.fstat(fd)
                            if (pinned.st_dev,pinned.st_ino)!=identity[2:4] or (pinned_parent.st_dev,pinned_parent.st_ino)!=proposal.parent_identity:raise WorkspaceUnavailable()
                        class Context:
                            def check(self):
                                grant()
                                # Approval callbacks can mutate the destination;
                                # compare AFTER them, including the last pre-rename checkpoint.
                                if fs._version(fd,parts[-1])!=previous:raise FilesystemConflict('Approved overwrite version changed')
                        grant();attempted=True
                        fs._replace(Context(),fd,parts[-1],data,previous)
                        grant()
                    check()
                check()
            return json_response(200,{'uploaded':1})
        except BaseException as exc:
            if attempted:return json_response(500,{'error':'Upload outcome uncertain; reconcile before retrying','outcome':'unknown','retry':False})
            if not isinstance(exc,Exception):raise
            if isinstance(exc,WorkspaceDenied):return error_response(403,'Workspace upload denied')
            if isinstance(exc,WorkspaceUnavailable):return error_response(503,'Workspace upload capability unavailable')
            if isinstance(exc,FilesystemConflict):return error_response(409,'Workspace target changed')
            if isinstance(exc,FileNotFoundError):return error_response(404,'Workspace directory not found')
            if isinstance(exc,(FilesystemError,ValueError,TypeError,UnicodeError)) or (isinstance(exc,OSError) and exc.errno in (errno.ELOOP,errno.ENOTDIR)):return error_response(400,'Unsupported workspace upload')
            return error_response(500,'Workspace upload preparation failed')
