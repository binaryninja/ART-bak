"""Norma v0.4.3 JSONL transcript storage, without agent execution.

Explicit existing absolute root; POSIX dirfd/no-follow and advisory session locks.
All writers must use this store (Norma's original append does not share locks).
Atomic full-file replacement + fsync is bounded to max_bytes; this is not a DB
transaction. A post-rename fsync failure is an uncertain commit: reconcile by
UUID, never blindly retry. Root and ancestors must not be attacker-writable;
no symlinks are followed, but directory renames by a privileged actor are outside
this boundary. Delete/export, compaction policy and task lifetime guards are not
implemented. Unknown JSON record/message fields survive read/commit unchanged.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import stat
import uuid

class TranscriptError(RuntimeError): pass
class TranscriptConflict(TranscriptError): pass
class TranscriptUnavailable(TranscriptError): pass

_NAME=re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,199}\Z')

def _name(value):
    if not isinstance(value,str) or not _NAME.fullmatch(value):
        raise ValueError('Invalid transcript identity')
    return value

def _encode(value):
    try:return json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')
    except (ValueError,TypeError,UnicodeError):raise ValueError('Invalid transcript JSON') from None

def _decode(raw):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError()
            result[key]=value
        return result
    try:return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
    except (ValueError,UnicodeError):raise TranscriptError('Corrupt transcript JSON') from None

@dataclass(frozen=True)
class Snapshot:
    session_id:str
    agent_id:str
    revision:str
    records:tuple

class TranscriptStore:
    def __init__(self,root,*,max_bytes=64*1024*1024):
        if os.name!='posix' or not hasattr(os,'O_NOFOLLOW'):raise TranscriptUnavailable('POSIX no-follow filesystem required')
        root=os.fspath(root)
        if not isinstance(root,str) or not root.startswith('/') or any(p in ('.','..') for p in root.split('/')):raise ValueError('Existing absolute root required')
        if type(max_bytes) is not int or not 1<=max_bytes<=1024**3:raise ValueError('Invalid transcript size limit')
        self.root=root;self.max_bytes=max_bytes

    def _root(self):
        fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
        try:
            for part in filter(None,self.root.split('/')):
                child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                os.close(fd);fd=child
            return fd
        except BaseException:os.close(fd);raise

    @contextmanager
    def _locked(self,session,agent=''):
        import fcntl
        _name(session)
        if agent:_name(agent)
        root=self._root();lock=None;directory=None
        try:
            lock=os.open('.lock-'+session,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK,0o600,dir_fd=root)
            info=os.fstat(lock)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise TranscriptError('Unsafe transcript lock')
            fcntl.flock(lock,fcntl.LOCK_EX)
            directory=os.dup(root)
            if agent:
                for part in (session,'subagents'):
                    try:os.mkdir(part,0o700,dir_fd=directory);os.fsync(directory)
                    except FileExistsError:pass
                    child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
                    os.close(directory);directory=child
            yield directory,('agent-'+agent if agent else session)+'.jsonl'
        finally:
            if directory is not None:os.close(directory)
            if lock is not None:os.close(lock)
            os.close(root)

    def _load(self,directory,name):
        try:fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
        except FileNotFoundError:return b'',[]
        with os.fdopen(fd,'rb') as stream:
            info=os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise TranscriptError('Unsafe transcript file')
            if info.st_size>self.max_bytes:raise TranscriptError('Transcript exceeds configured limit')
            raw=stream.read(self.max_bytes+1)
        if len(raw)>self.max_bytes:raise TranscriptError('Transcript exceeds configured limit')
        records=[]
        for line in raw.splitlines():
            if not line:continue
            item=_decode(line)
            if not isinstance(item,dict):raise TranscriptError('Invalid transcript record')
            records.append(item)
        return raw,records

    @staticmethod
    def _revision(raw):return hashlib.sha256(raw).hexdigest()

    def begin(self,session_id,agent_id=''):
        with self._locked(session_id,agent_id) as (directory,name):raw,records=self._load(directory,name)
        return Snapshot(session_id,agent_id,self._revision(raw),tuple(records))

    def read(self,session,limit=1000,*,agent_id=''):
        if type(limit) is not int or not 1<=limit<=10000:raise ValueError('Invalid transcript limit')
        return list(self.begin(session,agent_id).records)[-limit:]

    def _write(self,directory,name,records):
        raw=b''.join(_encode(record)+b'\n' for record in records)
        if len(raw)>self.max_bytes:raise TranscriptError('Transcript exceeds configured limit')
        temporary='.tmp-'+uuid.uuid4().hex
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory)
        try:
            with os.fdopen(fd,'wb') as stream:
                stream.write(raw);stream.flush();os.fsync(stream.fileno())
            os.replace(temporary,name,src_dir_fd=directory,dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            try:os.unlink(temporary,dir_fd=directory)
            except FileNotFoundError:pass
        return self._revision(raw)

    def commit(self,snapshot,records):
        """Append full records only if the exact bytes read by begin remain current."""
        if not isinstance(snapshot,Snapshot):raise ValueError('Snapshot required')
        batch=_decode(_encode(list(records)))
        for record in batch:
            if not isinstance(record,dict) or record.get('session_id')!=snapshot.session_id or record.get('agent_id','')!=snapshot.agent_id:
                raise ValueError('Transcript record identity mismatch')
            if not isinstance(record.get('uuid'),str) or not record['uuid']:raise ValueError('Record UUID required')
        with self._locked(snapshot.session_id,snapshot.agent_id) as (directory,name):
            raw,current=self._load(directory,name)
            if self._revision(raw)!=snapshot.revision:raise TranscriptConflict('Transcript changed')
            ids={r.get('uuid') for r in current}
            for record in batch:
                if record['uuid'] in ids:raise TranscriptConflict('Duplicate transcript UUID')
                ids.add(record['uuid'])
            revision=self._write(directory,name,current+batch) if batch else snapshot.revision
        return Snapshot(snapshot.session_id,snapshot.agent_id,revision,tuple(current+batch))

    def record_message(self,session,message,usage=None,*,agent_id=''):
        return self._record(session,{'type':'message','message':message,**({'usage':usage} if usage else {})},agent_id)

    def record_boundary(self,session,boundary,*,agent_id=''):
        return self._record(session,{'type':'boundary','boundary':boundary},agent_id)

    def _record(self,session,fields,agent):
        # CAS deliberately reports concurrent turn conflicts instead of replaying a turn.
        snapshot=self.begin(session,agent)
        record=dict(fields,uuid=uuid.uuid4().hex,session_id=session,timestamp=datetime.now(timezone.utc).isoformat().replace('+00:00','Z'))
        if snapshot.records:record['parent_uuid']=snapshot.records[-1].get('uuid','')
        if agent:record.update(agent_id=agent,is_sidechain=True)
        return self.commit(snapshot,[record])

    def delete(self,*args,**kwargs):raise TranscriptUnavailable('Transcript deletion lifecycle is not ported')
    def export(self,*args,**kwargs):raise TranscriptUnavailable('Transcript export lifecycle is not ported')


def messages(records):
    """Norma raw reconstruction, not compaction/working-set selection."""
    output=[];usage={k:0 for k in ('input_tokens','output_tokens','cache_read_tokens','cache_write_tokens')}
    for record in records:
        if record.get('type')=='boundary' or record.get('message') is None:continue
        output.append(record['message'])
        for key in usage:
            value=(record.get('usage') or {}).get(key,0)
            if type(value) is not int:raise TranscriptError('Invalid transcript usage')
            usage[key]+=value
    return output,usage

class WorkerTranscriptBackend:
    """Generation guard must share worker lifecycle fencing; not a DB transaction.

    append consumes a full Norma record JSON with stable UUID. Equal UUID+record
    retries are idempotent; UUID reuse with different data conflicts. Guard must
    validate authoritative generation and remain held through durable commit.
    """
    def __init__(self,store,*,generation_guard=None):self.store=store;self.generation_guard=generation_guard
    def read(self,session,limit=1000):return self.store.read(session,limit)
    def append(self,session,data,version):
        if not callable(self.generation_guard):raise TranscriptUnavailable('Generation guard required')
        if type(version) is not int or version<0:raise ValueError('Invalid generation')
        record=_decode(data)
        if not isinstance(record,dict) or not record.get('uuid'):raise ValueError('Full record with stable UUID required')
        with self.generation_guard(session,version):
            snapshot=self.store.begin(session)
            for previous in snapshot.records:
                if previous.get('uuid')==record['uuid']:
                    if previous!=record:raise TranscriptConflict('Record UUID reused')
                    return snapshot
            return self.store.commit(snapshot,[record])
