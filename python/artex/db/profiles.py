"""Credential-bearing LLM profile storage, no HTTP/provider calls.

Exact persisted profile/retry fields from db/config.go. list() selects hints,
not keys; get()/active()/pool() expose keys ONLY to a trusted authorized host.
Use public_dict() for UI: never dataclasses.asdict() on a private profile.
Save preserves an existing key when an update supplies an empty key. Active
selection is a separate transaction; save never changes is_default. Retry
values clamp like upstream; other invalid configuration fails before writes.
Deletion is deliberately unavailable until reference-lock ordering, failover
cursor repair and bounded retries are ported. No silent FK-only deletion.
Errors and repr omit credentials; SQL adapters must likewise disable bind logging.
Public URL userinfo/query/fragment are redacted beyond upstream's key-only hiding.
Redacted URLs and masked replacement keys are rejected, not persisted. Public
DTOs containing URL placeholders require an explicit real URL before saving.
Hints for keys of four characters or fewer are suppressed beyond upstream.
No driver/DB execution has been validated here beyond injected contract tests.
"""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass, field, fields
import math
import re
from urllib.parse import urlsplit,urlunsplit,unquote
from .core import Query,int64


class ProfileInvalid(ValueError):pass
class ProfileNotFound(LookupError):pass
class ProfileStorageError(RuntimeError):pass


@dataclass(frozen=True)
class RetryRule:
    attempts:int=0
    interval_ms:int=0
    def clamped(self):
        if type(self.attempts) is not int or type(self.interval_ms) is not int:
            raise ProfileInvalid('retry fields must be integers')
        return RetryRule(max(-1,min(20,self.attempts)),max(0,min(3600000,self.interval_ms)))


@dataclass(frozen=True)
class RetryOverride:
    connect:RetryRule=field(default_factory=RetryRule)
    empty:RetryRule=field(default_factory=RetryRule)
    stream:RetryRule=field(default_factory=RetryRule)
    def values(self):
        values=[]
        for rule in (self.connect,self.empty,self.stream):
            if not isinstance(rule,RetryRule):raise ProfileInvalid('invalid retry rule')
            value=rule.clamped();values.extend((value.attempts,value.interval_ms))
        return tuple(values)
    def public_dict(self):
        return {name:{'attempts':getattr(self,name).attempts,'interval_ms':getattr(self,name).interval_ms} for name in ('connect','empty','stream')}


def _public_url(value):
    if not value:return ''
    try:
        p=urlsplit(value)
        host=p.netloc.rsplit('@',1)[-1]
        if '@' in p.netloc:host='[redacted]@'+host
        return urlunsplit((p.scheme,host,p.path,'[redacted]' if p.query else '', '[redacted]' if p.fragment else ''))
    except ValueError:return '[redacted URL]'


@dataclass(repr=False)
class LLMProfile:
    id:int=0
    name:str=''
    format:str='openai'
    base_url:str=''
    proxy:str=''
    model:str=''
    api_key:str=''
    api_key_hint:str=''
    rate_per_second:float=0
    rate_per_minute:float=0
    context_window_k:int=0
    reasoning_effort:str=''
    is_default:bool=False
    priority:int=0
    pool_exclude:bool=False
    thinking_type:str=''
    streaming:bool=True
    max_tokens:int=0
    max_tokens_field:str=''
    session_header_key:str=''
    retry:RetryOverride=field(default_factory=RetryOverride)

    def __repr__(self):return f'LLMProfile(id={self.id}, credentials=<redacted>)'
    def public_dict(self):
        result={f.name:getattr(self,f.name) for f in fields(self) if f.name not in ('api_key','retry')}
        result['base_url']=_public_url(self.base_url);result['proxy']=_public_url(self.proxy)
        result['retry']=self.retry.public_dict()
        # A trusted loaded key should never accidentally become a full-key hint.
        if self.api_key and result['api_key_hint']==self.api_key:result['api_key_hint']='[redacted]'
        return result


_RETRY_NAMES=('retry_connect_attempts','retry_connect_interval_ms','retry_empty_attempts','retry_empty_interval_ms','retry_stream_attempts','retry_stream_interval_ms')
_READ_PREFIX="id,name,format,COALESCE(base_url,''),COALESCE(proxy,''),model,"
_READ_SUFFIX=",rate_per_second,rate_per_minute,context_window_k,COALESCE(reasoning_effort,''),is_default,priority,pool_exclude,COALESCE(thinking_type,''),COALESCE(streaming,true),COALESCE(max_tokens,0),COALESCE(max_tokens_field,''),COALESCE(session_header_key,''),"+','.join(f'COALESCE({name},0)' for name in _RETRY_NAMES)
PUBLIC_COLUMNS=_READ_PREFIX+"COALESCE(api_key_hint,'')"+_READ_SUFFIX
PRIVATE_COLUMNS=_READ_PREFIX+"COALESCE(api_key,'')"+_READ_SUFFIX


def _scan(row,private):
    if len(row)!=25:raise ProfileStorageError('invalid profile row shape')
    profile=LLMProfile(id=row[0],name=row[1],format=row[2],base_url=row[3],proxy=row[4],model=row[5],
        rate_per_second=row[7],rate_per_minute=row[8],context_window_k=row[9],reasoning_effort=row[10],
        is_default=row[11],priority=row[12],pool_exclude=row[13],thinking_type=row[14],streaming=row[15],
        max_tokens=row[16],max_tokens_field=row[17],session_header_key=row[18],
        retry=RetryOverride(RetryRule(*row[19:21]),RetryRule(*row[21:23]),RetryRule(*row[23:25])))
    if private:profile.api_key=row[6]
    else:profile.api_key_hint=row[6]
    return profile


def _validate(profile):
    if not isinstance(profile,LLMProfile):raise ProfileInvalid('expected an LLMProfile')
    if int64(profile.id,'profile id')<0:raise ProfileInvalid('profile id cannot be negative')
    for name in ('name','format','base_url','proxy','model','api_key','api_key_hint','reasoning_effort','thinking_type','max_tokens_field','session_header_key'):
        value=getattr(profile,name)
        if not isinstance(value,str):raise ProfileInvalid(f'{name} must be text')
        if '\x00' in value:raise ProfileInvalid(f'{name} contains invalid text')
        try:value.encode('utf-8')
        except UnicodeError:raise ProfileInvalid(f'{name} contains invalid text') from None
    key=profile.api_key.strip()
    if key.startswith(('…','...')) or key.casefold() in ('[redacted]','[redacted key]'):
        raise ProfileInvalid('masked API keys cannot be saved')
    if not profile.name.strip() or not profile.model.strip():raise ProfileInvalid('profile name and model are required')
    if profile.format not in ('openai','anthropic','openai-responses'):raise ProfileInvalid('invalid profile format')
    if profile.thinking_type not in ('','enabled','disabled'):raise ProfileInvalid('invalid thinking type')
    if profile.reasoning_effort not in ('','low','medium','high','xhigh','max'):raise ProfileInvalid('invalid reasoning effort')
    if profile.max_tokens_field not in ('','max_completion_tokens'):raise ProfileInvalid('invalid maximum-token field')
    for name in ('is_default','pool_exclude','streaming'):
        if type(getattr(profile,name)) is not bool:raise ProfileInvalid(f'{name} must be boolean')
    for name in ('priority','context_window_k','max_tokens'):
        value=getattr(profile,name)
        if type(value) is not int or not -(2**31)<=value<2**31:raise ProfileInvalid(f'{name} must fit INTEGER')
    if not 0<=profile.context_window_k<=1000 or profile.max_tokens<0:raise ProfileInvalid('invalid token bounds')
    for name in ('rate_per_second','rate_per_minute'):
        value=getattr(profile,name)
        if type(value) not in (int,float) or not math.isfinite(value) or value<0:raise ProfileInvalid('rate limits must be finite and nonnegative')
    if profile.session_header_key and not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+",profile.session_header_key):
        raise ProfileInvalid('invalid session header name')
    for name,schemes in [('base_url',('http','https')),('proxy',('http','https','socks5','socks5h'))]:
        value=getattr(profile,name)
        if not value:continue
        # Accept only replacement credentials, never a display-only placeholder.
        # Decode once to also reject normal URL-encoded public DTO round trips.
        display=unquote(value).casefold()
        if '[redacted]' in display or '[redacted url]' in display:
            raise ProfileInvalid(f'redacted {name} cannot be saved')
        try:
            parsed=urlsplit(value)
            valid=parsed.scheme in schemes and bool(parsed.hostname) and not any(ord(ch)<33 for ch in value)
            parsed.port
        except ValueError:valid=False
        if not valid:raise ProfileInvalid(f'invalid {name}')
    if not isinstance(profile.retry,RetryOverride):raise ProfileInvalid('invalid retry override')
    return profile.retry.values()


@contextmanager
def _safe_storage():
    try:yield
    except (ProfileInvalid,ProfileNotFound,ProfileStorageError):raise
    except Exception:raise ProfileStorageError('profile database operation failed') from None


class ProfileStore:
    def __init__(self,executor):self.executor=executor

    def _rows(self,sql,params=(),private=False):
        with _safe_storage():return [_scan(row,private) for row in self.executor.fetch_all(Query(sql,params))]

    def list(self):return self._rows('SELECT '+PUBLIC_COLUMNS+' FROM llm_profiles ORDER BY id')
    def get(self,id):
        id=int64(id,'profile id')
        rows=self._rows('SELECT '+PRIVATE_COLUMNS+' FROM llm_profiles WHERE id=$1',(id,),True)
        return rows[0] if rows else None
    def active(self):
        rows=self._rows('SELECT '+PRIVATE_COLUMNS+' FROM llm_profiles WHERE is_default LIMIT 1',private=True)
        return rows[0] if rows else None
    def pool(self):return self._rows('SELECT '+PRIVATE_COLUMNS+" FROM llm_profiles WHERE COALESCE(api_key,'') <> '' AND (is_default OR NOT pool_exclude) ORDER BY is_default DESC, priority DESC, id ASC",private=True)

    def _transaction(self):
        factory=getattr(self.executor,'transaction',None)
        if factory is None:raise ProfileStorageError('profile writes require a transaction-capable executor')
        return factory()

    def save(self,profile):
        retries=_validate(profile)
        # Only trusted constants become identifiers. Values remain bound.
        columns=['name','format','base_url','proxy','model']
        values=[profile.name,profile.format,profile.base_url,profile.proxy,profile.model]
        if profile.id==0 or profile.api_key:
            hint='…'+profile.api_key[-4:] if len(profile.api_key)>4 else ''
            columns.extend(['api_key','api_key_hint']);values.extend([profile.api_key,hint])
        rest=['rate_per_second','rate_per_minute','context_window_k','reasoning_effort','priority','pool_exclude','thinking_type','streaming','max_tokens','max_tokens_field','session_header_key']
        columns.extend(rest);values.extend(getattr(profile,key) for key in rest)
        if profile.format!='openai':values[columns.index('max_tokens_field')]=''
        columns.extend(_RETRY_NAMES);values.extend(retries)
        def bind(index,column):
            key=f'${index}'
            return f"NULLIF({key},'')" if column in ('base_url','proxy') or profile.id==0 and column in ('api_key','api_key_hint') else key
        with _safe_storage():
            with self._transaction() as tx:
                if profile.id==0:
                    sql='INSERT INTO llm_profiles('+','.join(columns)+') VALUES ('+','.join(bind(i,col) for i,col in enumerate(columns,1))+') RETURNING id'
                    id=tx.fetch_one(Query(sql,tuple(values)))[0]
                else:
                    sql='UPDATE llm_profiles SET '+','.join(f'{col}={bind(i,col)}' for i,col in enumerate(columns,1))+f' WHERE id=${len(values)+1}'
                    affected=tx.execute(Query(sql,tuple(values)+(profile.id,)))
                    if affected!=1:raise ProfileNotFound('LLM profile not found')
                    id=profile.id
        return id

    def set_active(self,id):
        id=int64(id,'profile id')
        if id<=0:raise ProfileInvalid('profile id must be positive')
        with _safe_storage():
            with self._transaction() as tx:
                # Source's two-update transaction; row uniqueness remains schema-owned.
                tx.execute(Query('UPDATE llm_profiles SET is_default=false WHERE is_default'))
                if tx.execute(Query('UPDATE llm_profiles SET is_default=true WHERE id=$1',(id,)))!=1:
                    raise ProfileNotFound('LLM profile not found')

    def delete(self,id):
        raise NotImplementedError('profile deletion requires unported reference locks and failover cursor repair')
