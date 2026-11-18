"""Host-private, origin-bound credentials. Public metadata contains references only."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat


class CredentialStore:
    def __init__(self, root, key):
        self.root=Path(root)
        self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
        info=self.root.stat()
        if self.root.resolve()!=self.root or not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700:
            raise ValueError('Owned private credential directory required')
        self._key=key

    def put(self, origin, username, password):
        if not isinstance(username,str) or not isinstance(password,str) or not username or not password or len(username.encode())>512 or len(password.encode())>4096:raise ValueError('Complete bounded credential required')
        data=json.dumps(dict(origin=origin,username=username,password=password),sort_keys=True).encode()
        ref=hmac.new(self._key,data,hashlib.sha256).hexdigest()
        path=self.root/ref
        try:
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        except FileExistsError:
            if self.get(ref,origin)!=(username,password):raise ValueError('Credential identity mismatch')
        else:
            with os.fdopen(fd,'wb') as file:file.write(data);file.flush();os.fsync(file.fileno())
        return ref

    def get(self, ref, origin):
        if not re.fullmatch('[0-9a-f]{64}',ref):raise ValueError('Invalid credential reference')
        fd=os.open(self.root/ref,os.O_RDONLY|os.O_NOFOLLOW)
        with os.fdopen(fd,'rb') as file:
            info=os.fstat(file.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600 or info.st_size>16384:raise ValueError('Private credential unavailable')
            value=json.load(file)
        if value['origin']!=origin:raise ValueError('Credential belongs to another origin')
        return value['username'],value['password']
