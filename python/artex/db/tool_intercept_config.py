"""Bounded literal tool policy persistence. Not pending approvals or execution."""
from dataclasses import dataclass,asdict
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
import hashlib,json
from .core import Query
from .tool_intercept_read import ToolInterceptReader
from ..intercept import Rule
class PolicyDenied(PermissionError):pass
class PolicyConflict(RuntimeError):pass
class PolicyUnavailable(RuntimeError):pass
class PolicyUncertain(RuntimeError):pass
@dataclass(frozen=True)
class ToolRuleProposal:
    name:str
    enabled:bool=False
    priority:int=0
    match_target:str='tool_name'
    match_type:str='string'
    pattern:str=''
    action:str='deny'
    message:str=''
    timeout_enabled:bool=False
    timeout_seconds:int=0
    timeout_action:str='deny'
    def validate(self):
        if type(self) is not ToolRuleProposal:raise ValueError('Exact proposal required')
        for k in ('name','match_target','match_type','pattern','action','message','timeout_action'):
            v=getattr(self,k)
            if type(v) is not str or '\0' in v or len(v.encode())>65536:raise ValueError('Invalid text')
        if not self.name or not self.pattern or self.match_target not in ('tool_name','tool_input') or self.match_type!='string' or self.action not in ('allow','deny','ask'):raise ValueError('Unsupported literal rule')
        for k in ('enabled','timeout_enabled'):
            if type(getattr(self,k)) is not bool:raise ValueError('Invalid boolean')
        for k in ('priority','timeout_seconds'):
            if type(getattr(self,k)) is not int or not -2**31<=getattr(self,k)<2**31:raise ValueError('Invalid integer')
@dataclass(frozen=True)
class PolicyMutation:
    operation:str
    rule_id:int|None=None
    proposal:ToolRuleProposal|None=None
    enabled:bool|None=None
    expected_updated_at:datetime|None=None
    tools:tuple[str,...]|None=None
    expected_tools_digest:str|None=None
    def validate(self):
        if type(self) is not PolicyMutation or type(self.operation) is not str or self.operation not in ('create','update','delete','toggle','tools'):raise ValueError('Invalid operation')
        if self.operation in ('create','update'):
            if type(self.proposal) is not ToolRuleProposal:raise ValueError('Rule required')
            self.proposal.validate()
        elif self.proposal is not None:raise ValueError('Unexpected proposal')
        if self.operation in ('update','delete','toggle'):
            if type(self.rule_id) is not int or not 0<self.rule_id<2**63 or type(self.expected_updated_at) is not datetime or type(self.expected_updated_at.tzinfo) not in (timezone,ZoneInfo):raise ValueError('Timestamp CAS required')
        elif self.rule_id is not None or self.expected_updated_at is not None:raise ValueError('Unexpected identity')
        if self.operation=='toggle':
            if type(self.enabled) is not bool:raise ValueError('Boolean required')
        elif self.enabled is not None:raise ValueError('Unexpected toggle')
        if self.operation=='tools':
            if type(self.tools) is not tuple or len(self.tools)>256 or any(type(v) is not str or '\0' in v or len(v.encode())>1024 for v in self.tools):raise ValueError('Bounded list required')
            d=self.expected_tools_digest
            if type(d) is not str or len(d)!=64 or any(c not in '0123456789abcdef' for c in d):raise ValueError('Expected digest required')
        elif self.tools is not None or self.expected_tools_digest is not None:raise ValueError('Unexpected tools')
def mutation_identity(m):
    """Validated primitive snapshot, never retain mutable dataclass field aliases."""
    m.validate()
    proposal=tuple((k,type(getattr(m.proposal,k)),getattr(m.proposal,k)) for k in ToolRuleProposal.__dataclass_fields__) if m.proposal is not None else None
    return (m.operation,m.rule_id,proposal,m.enabled,m.expected_updated_at,m.tools,m.expected_tools_digest)

def tools_digest(raw):return hashlib.sha256(json.dumps(raw,ensure_ascii=True,separators=(',',':')).encode()).hexdigest()
class ToolInterceptConfigStore:
    def __init__(self,executor,*,max_rows=1000,max_bytes=1048576):
        if not callable(getattr(executor,"lock_intercept_settings",None)):raise ValueError("Exact transaction settings-lock capability required; pools unsupported")
        self._settings_lock=executor.lock_intercept_settings
        self.executor=executor;self._executor=executor
        self.reader=ToolInterceptReader(executor,max_rows=max_rows,max_bytes=max_bytes);self._reader=self.reader
        self._bounds=(max_rows,max_bytes)
        self._methods=tuple(getattr(self,k).__func__ for k in ("validate","list_rules","get_enabled_tools","_tools","_apply"))
    def validate(self):
        if self._executor.lock_intercept_settings!=self._settings_lock:raise PolicyDenied("Settings-lock binding changed")
        if getattr(self,"_unresolved",False):raise PolicyUncertain("Policy reconciliation required")
        if self.executor is not self._executor or self.reader is not self._reader or self.reader.executor is not self._executor or (self.reader.max_rows,self.reader.max_bytes)!=self._bounds:raise PolicyDenied('Binding changed')
        if tuple(getattr(getattr(self,k),"__func__",None) for k in ("validate","list_rules","get_enabled_tools","_tools","_apply"))!=self._methods:raise PolicyDenied("Adapter methods changed")
        self.reader.validate()
    def list_rules(self):
        self.validate()
        try:rows=self.reader.read()
        except Exception:raise PolicyUnavailable("Configuration unavailable") from None
        self.validate();out=[]
        for r in rows:
            p=ToolRuleProposal(**{k:r[k] for k in ToolRuleProposal.__dataclass_fields__});p.validate();out.append(Rule(id=r['id'],**asdict(p)))
        return out
    def _tools(self,tx,lock=False):
        rows=tx.fetch_all(Query("SELECT CASE WHEN octet_length(value)<=$1 THEN value ELSE NULL END FROM settings WHERE key='intercept_enabled_tools'"+(' FOR UPDATE' if lock else ''),(self._bounds[1],)))
        if type(rows) not in (list,tuple) or len(rows)>1:raise PolicyUnavailable('Invalid configuration')
        if not rows:return None
        if len(rows[0])!=1 or type(rows[0][0]) is not str:raise PolicyUnavailable('Invalid configuration')
        return rows[0][0]
    def get_enabled_tools(self):
        self.validate()
        try:raw=self._tools(self._executor)
        except Exception:raise PolicyUnavailable("Configuration unavailable") from None
        self.validate()
        if raw is None:return None
        v=json.loads(raw)
        if type(v) is not list or len(v)>256 or any(type(x) is not str or len(x.encode())>1024 for x in v):raise PolicyUnavailable('Invalid configuration')
        return v
    def _apply(self,tx,m,gate,attempt):
        self.validate();m.validate()
        if m.operation=='tools':
            lock=getattr(tx,'lock_intercept_settings',None)
            if not callable(lock):raise PolicyUnavailable('Exact settings lock capability required')
            lock()
            if tools_digest(self._tools(tx,True))!=m.expected_tools_digest:raise PolicyConflict('Configuration changed')
            gate();attempt();n=tx.execute(Query("INSERT INTO settings(key,value) VALUES ('intercept_enabled_tools',$1) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,updated_at=now()",(json.dumps(m.tools,separators=(',',':')),)))
            if type(n) is not int or n!=1:raise PolicyUnavailable('Write result invalid')
            return {'ok':True}
        if m.rule_id is not None:
            rows=tx.fetch_all(Query('SELECT updated_at FROM intercept_rules WHERE id=$1 FOR UPDATE',(m.rule_id,)))
            if len(rows)!=1 or rows[0][0]!=m.expected_updated_at:raise PolicyConflict('Rule changed')
        fields=tuple(ToolRuleProposal.__dataclass_fields__)
        if m.operation=='create':
            sql='INSERT INTO intercept_rules('+','.join(fields)+') VALUES ('+','.join('$'+str(i+1) for i in range(len(fields)))+') RETURNING id,created_at,updated_at';params=tuple(getattr(m.proposal,k) for k in fields)
        elif m.operation=='update':
            sql='UPDATE intercept_rules SET '+','.join(k+'=$'+str(i+3) for i,k in enumerate(fields))+' WHERE id=$1 AND updated_at=$2 RETURNING id,created_at,updated_at';params=(m.rule_id,m.expected_updated_at)+tuple(getattr(m.proposal,k) for k in fields)
        elif m.operation=='toggle':sql='UPDATE intercept_rules SET enabled=$3 WHERE id=$1 AND updated_at=$2 RETURNING id';params=(m.rule_id,m.expected_updated_at,m.enabled)
        else:sql='DELETE FROM intercept_rules WHERE id=$1 AND updated_at=$2 RETURNING id';params=(m.rule_id,m.expected_updated_at)
        gate();attempt();rows=tx.fetch_all(Query(sql,params))
        if len(rows)!=1 or type(rows[0][0]) is not int or rows[0][0]<=0:raise PolicyUnavailable('Write result invalid')
        if m.operation in ('create','update'):
            if len(rows[0])!=3 or any(type(v) is not datetime or v.tzinfo is None for v in rows[0][1:]):raise PolicyUnavailable('Timestamp unavailable')
            return {'id':rows[0][0],**asdict(m.proposal),'created_at':rows[0][1].isoformat().replace('+00:00','Z'),'updated_at':rows[0][2].isoformat().replace('+00:00','Z')}
        return {'deleted':m.rule_id} if m.operation=='delete' else {'ok':True,'enabled':m.enabled}
    def create_pending(self,*a,**k):raise PolicyUnavailable('Approval persistence unsupported')
    def get_pending(self,*a,**k):raise PolicyUnavailable('Approval persistence unsupported')
    def resolve_pending(self,*a,**k):raise PolicyUnavailable('Approval persistence unsupported')
    def log_decision(self,*a,**k):raise PolicyUnavailable('Decision persistence unsupported')
