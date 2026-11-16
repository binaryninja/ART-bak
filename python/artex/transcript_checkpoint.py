"""Strict Python linked-JSONL checkpoint observations, not recovery authority.

Uses existing session locks only; never creates locks/directories/files. Cooperating
TranscriptStore writers are excluded during the read. Host owns authorization and
quiescence; a returned digest is neither provenance nor a durable ownership fence.
This opt-in profile matches TranscriptStore._record, not all Norma/imported logs
or arbitrary batches accepted by commit. No existing read/write behavior changes.
"""
from dataclasses import dataclass, field
import fcntl
import hashlib
import os
import stat
from .transcripts import TranscriptStore, TranscriptError, _name, _decode


@dataclass(frozen=True)
class TranscriptCheckpoint:
    session_id: str
    agent_id: str
    sha256: str
    byte_count: int
    record_count: int
    tail_uuid: str | None
    recovery_authorized: bool = field(default=False, init=False)


def inspect_checkpoint(store, session_id, *, agent_id='', max_records=10000):
    """Read one existing file, strictly validating Python-generated linked records.

    Missing lock/file rejects, unlike begin's empty snapshot. Existing empty files
    produce an explicit zero-record observation, never readiness. Unknown record
    fields survive untouched because this function never rewrites. Any malformed,
    oversized, unsafe or concurrently changed file fails with a redacted error.
    Advisory locks cannot exclude noncooperating writers; metadata checks detect
    ordinary in-place changes but are not protection against a privileged attacker.
    """
    if type(store) is not TranscriptStore:
        raise ValueError('Exact transcript store required')
    _name(session_id)
    if agent_id: _name(agent_id)
    if type(agent_id) is not str or type(max_records) is not int or not 1 <= max_records <= 100000:
        raise ValueError('Bounded checkpoint configuration required')
    bound=store.max_bytes
    if type(bound) is not int or not 1 <= bound <= 1024**3:
        raise ValueError('Bounded transcript store required')
    root=lock=directory=fd=None
    try:
        root=store._root()
        lock=os.open('.lock-'+session_id,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=root)
        info=os.fstat(lock)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1: raise ValueError()
        fcntl.flock(lock,fcntl.LOCK_SH)
        directory=os.dup(root)
        if agent_id:
            for part in (session_id,'subagents'):
                child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
                os.close(directory);directory=child
        name=('agent-'+agent_id if agent_id else session_id)+'.jsonl'
        fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or before.st_size>bound: raise ValueError()
        chunks=[];remaining=bound+1
        while remaining:
            chunk=os.read(fd,min(65536,remaining))
            if not chunk: break
            chunks.append(chunk);remaining-=len(chunk)
        raw=b''.join(chunks)
        after=os.fstat(fd);named=os.stat(name,dir_fd=directory,follow_symlinks=False)
        signature=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns,s.st_nlink)
        if len(raw)>bound or signature(before)!=signature(after) or signature(after)!=signature(named): raise ValueError()
        # Python writers always terminate each record. Reject partial trailing data.
        if raw and not raw.endswith(b'\n'): raise ValueError()
        seen=set();tail=None;count=0
        for line in raw.splitlines():
            if not line: raise ValueError()
            count+=1
            if count>max_records: raise ValueError()
            record=_decode(line.decode('utf-8'))
            if type(record) is not dict: raise ValueError()
            uid=record.get('uuid')
            if type(uid) is not str or not 1<=len(uid)<=200 or uid in seen: raise ValueError()
            if record.get('session_id')!=session_id or record.get('agent_id','')!=agent_id: raise ValueError()
            if agent_id and record.get('is_sidechain') is not True: raise ValueError()
            if not agent_id and record.get('is_sidechain',False) is not False: raise ValueError()
            if record.get('parent_uuid')!=tail: raise ValueError()
            if record.get('type') not in ('message','boundary'): raise ValueError()
            payload='message' if record['type']=='message' else 'boundary'
            if type(record.get(payload)) is not dict: raise ValueError()
            seen.add(uid);tail=uid
        return TranscriptCheckpoint(session_id,agent_id,hashlib.sha256(raw).hexdigest(),len(raw),count,tail)
    except Exception:
        raise TranscriptError('Transcript checkpoint unavailable') from None
    finally:
        for descriptor in (fd,directory,lock,root):
            if descriptor is not None: os.close(descriptor)
