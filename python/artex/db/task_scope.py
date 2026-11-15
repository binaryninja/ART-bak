"""Explicitly approved task-scope metadata, ported from db/task_scope.go.

No target discovery, inherited writes, DNS, automatic scope or root promotion.
Unlike Go AddAgentScope's publicsuffix promotion, root_domain is an explicitly
approved canonical hostname: a subdomain is NEVER silently promoted to its root.
Company names are unsupported; numeric IDs need an independent task-scoped
company grant and an existing DB row. Approval callback must consult host-owned
policy, never echo the candidate supplied by a model. It receives the exact frozen
normalized row, including provenance/reason. This is not authorization to test it.
"""
from dataclasses import dataclass, asdict
import ipaddress
import re
from .core import Query
from .postgres import NoRowsError

class ScopeInvalid(ValueError): pass
class ScopeDenied(PermissionError): pass
class ScopeUnavailable(RuntimeError): pass

@dataclass(frozen=True)
class TaskScope:
    task_id: int
    kind: str
    company_id: int | None = None
    domain: str = ''
    net: str = ''
    value: str = ''
    source: str = 'agent'
    reason: str = ''

    def to_dict(self):
        return {k:v for k,v in asdict(self).items() if v is not None and v != ''}


def _text(value, limit=4096):
    if type(value) is not str or '\x00' in value: raise ScopeInvalid('Invalid scope text')
    try: size=len(value.encode('utf-8'))
    except UnicodeError: raise ScopeInvalid('Invalid scope text') from None
    if size>limit: raise ScopeInvalid('Scope text too large')
    return value


def _id(value):
    if type(value) is not int or not 0<value<2**63: raise ScopeInvalid('Positive task/company ID required')
    return value


def _host(value):
    if any(c in value for c in '/?#@') or any(c.isspace() for c in value):
        raise ScopeInvalid('Host or IP required, not URL')
    # Equivalent valid host:port subset of Go SplitHostPort, without URL parsing.
    if value.startswith('['):
        match=re.fullmatch(r'\[([^\]]+)\]:[^:]*',value)
        if match:return match[1]
    elif value.count(':')==1:
        return value.split(':')[0]
    return value


def normalize(task_id, kind, value, *, reason='', source='agent'):
    task_id=_id(task_id); value=_text(value).strip();reason=_text(reason)
    if type(kind) is not str or source not in ('agent','manual'): raise ScopeInvalid('Unsupported kind/source')
    if not value: raise ScopeInvalid('Empty scope')
    fields={}
    if kind=='company':
        if not re.fullmatch(r'[+]?[0-9]+',value):raise ScopeInvalid('Company names require a separate scoped resolver')
        fields['company_id']=_id(int(value))
    elif kind in ('root_domain','subdomain'):
        domain=_host(value).lower().removesuffix('.')
        # Source accepts malformed domains; deliberately reject them. No IDNA/PSL guess.
        if len(domain)>253 or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?',p) for p in domain.split('.')):
            raise ScopeInvalid('Explicit ASCII hostname required')
        try:ipaddress.ip_address(domain)
        except ValueError:pass
        else:raise ScopeInvalid('IP requires ip/cidr kind')
        fields['domain']=domain
    elif kind in ('ip','cidr'):
        try:
            if '/' in value:
                # PostgreSQL CIDR rejects host bits; never broaden by masking them.
                network=ipaddress.ip_network(value,strict=True);kind='cidr'
            else:
                address=ipaddress.ip_address(_host(value))
                if getattr(address,'scope_id',None):raise ValueError()
                network=ipaddress.ip_network(f'{address}/{address.max_prefixlen}');kind='ip'
            fields['net']=str(network)
        except ValueError:raise ScopeInvalid('Invalid IP/CIDR or nonzero host bits') from None
    elif kind in ('icp','keyword'):
        fields['value']=(''.join(value.split()) if kind=='icp' else ' '.join(value.split())).lower()
    else:raise ScopeInvalid('Unsupported scope kind')
    return TaskScope(task_id,kind,source=source,reason=reason,**fields)


class TaskScopeStore:
    def __init__(self,executor,task_id,*,lifecycle_guard,current,approve,company_allowed=None,max_batch=100,expected_exploration_id=None):
        self.executor=executor;self.task_id=_id(task_id)
        self.expected_exploration_id=None if expected_exploration_id is None else _id(expected_exploration_id)
        if not all(callable(x) for x in (lifecycle_guard,current,approve)):raise ScopeInvalid('Explicit lifecycle and scope approval required')
        if company_allowed is not None and not callable(company_allowed):raise ScopeInvalid('Invalid company grant')
        if type(max_batch) is not int or not 1<=max_batch<=1000:raise ScopeInvalid('Invalid batch limit')
        self.guard,self.current,self.approve=lifecycle_guard,current,approve
        self.company_allowed=company_allowed;self.max_batch=max_batch

    def _check(self):
        if self.current() is not True:raise ScopeDenied('Task lifetime/authority changed')

    def add_many(self,entries):
        if type(entries) not in (list,tuple) or not 1<=len(entries)<=self.max_batch:raise ScopeInvalid('Bounded nonempty batch required')
        entries=tuple(entries)
        # Reject forged/noncanonical DTO fields instead of silently renormalizing approval.
        for entry in entries:
            if type(entry) is not TaskScope or type(entry.task_id) is not int or entry.task_id!=self.task_id:raise ScopeInvalid('Exact task scope required')
            raw=str(entry.company_id) if entry.kind=='company' else entry.domain or entry.net or entry.value
            if entry.kind=='ip':raw=entry.net.split('/')[0]
            if normalize(self.task_id,entry.kind,raw,reason=entry.reason,source=entry.source)!=entry:raise ScopeInvalid('Canonical scope required')
        transaction=getattr(self.executor,'transaction',None)
        if not callable(transaction):raise ScopeInvalid('Transaction required')
        with self.guard():
            self._check()
            with transaction() as tx:
                columns='id,deleted_at' if self.expected_exploration_id is None else 'id,deleted_at,exploration_id'
                try:row=tx.fetch_one(Query('SELECT '+columns+' FROM tasks WHERE id=$1 FOR UPDATE',(self.task_id,)))
                except NoRowsError:row=None
                if row is None or row[0]!=self.task_id or row[1] is not None:raise ScopeDenied('Task unavailable')
                if self.expected_exploration_id is not None and (len(row)!=3 or type(row[2]) is not int or row[2]!=self.expected_exploration_id):raise ScopeDenied('Task exploration unavailable')
                archive=tx.fetch_all(Query('SELECT state FROM task_archives WHERE task_id=$1',(self.task_id,)))
                if archive and archive[0][0]!='archive_failed':raise ScopeDenied('Archive blocks scope writes')
                for entry in entries:
                    if entry.company_id is not None:
                        if self.company_allowed is None or self.company_allowed(self.task_id,entry.company_id) is not True:raise ScopeDenied('Company outside approved task scope')
                        self._check()
                        rows=tx.fetch_all(Query('SELECT id FROM companies WHERE id=$1 FOR KEY SHARE',(entry.company_id,)))
                        if rows!=[(entry.company_id,)]:raise ScopeDenied('Company unavailable')
                    if self.approve(entry) is not True:raise ScopeDenied('Exact normalized scope not independently approved')
                    self._check()
                    tx.execute(Query('''INSERT INTO task_scope(task_id,kind,company_id,domain,net,value,source,reason)
VALUES ($1,$2,$3,$4,$5::cidr,$6,$7,NULLIF($8,'')) ON CONFLICT DO NOTHING''',
                        (self.task_id,entry.kind,entry.company_id,entry.domain or None,entry.net or None,entry.value or None,entry.source,entry.reason)))
                    self._check()
                self._check()
        return entries

    def add_agent_scope(self,kind,value,*,reason='',source='agent'):
        return self.add_many((normalize(self.task_id,kind,value,reason=reason,source=source),))[0]

    def list_bounded(self):
        """Own-task scope only; explicit source inheritance is not assembled here.

        Any source relation fails closed, rather than returning an incomplete
        source-shaped list. Window totals and JSON sentinel bound transfer.
        """
        if self.executor.fetch_all(Query('SELECT 1 FROM task_relations WHERE task_id=$1 LIMIT 1',(self.task_id,))):
            raise ScopeUnavailable('Inherited scope view requires explicit source authorization')
        rows=self.executor.fetch_all(Query("""SELECT CASE WHEN total_count<=1000
AND total_bytes<=1048576 AND octet_length(item::text)<=16384 THEN item ELSE NULL END,total_count
FROM (SELECT item,id,COUNT(*) OVER () AS total_count,
SUM(octet_length(item::text)) OVER () AS total_bytes
FROM (SELECT jsonb_strip_nulls(jsonb_build_object('id',ts.id,'task_id',ts.task_id,
'kind',ts.kind,'company_id',ts.company_id,'company_name',NULLIF(c.name,''),
'domain',NULLIF(ts.domain,''),'net',NULLIF(ts.net::text,''),'value',NULLIF(ts.value,''),
'source',ts.source,'reason',NULLIF(ts.reason,''))) AS item,ts.id
FROM task_scope ts LEFT JOIN companies c ON c.id=ts.company_id
WHERE ts.task_id=$1) AS scope_items ORDER BY id LIMIT 1001) AS bounded_scope ORDER BY id""",(self.task_id,)))
        import json
        result=[];size=0
        if len(rows)>1000:raise ScopeUnavailable('Scope view exceeds bounds')
        for item,count in rows:
            if item is None or count!=len(rows):raise ScopeUnavailable('Scope view exceeds bounds')
            if isinstance(item,str):
                if len(item.encode())>16384:raise ScopeUnavailable('Scope view exceeds bounds')
                item=json.loads(item)
            if type(item) is not dict or item.get('task_id')!=self.task_id:raise ScopeUnavailable('Invalid scope view')
            encoded=json.dumps(item).encode();size+=len(encoded)
            if len(encoded)>16384 or size>1048576:raise ScopeUnavailable('Scope view exceeds bounds')
            result.append(item)
        return result

    def delete(self,scope_id,*,approve_delete):
        """Exact own-task deletion; host must separately grant destructive action.

        Caller must hold task lifecycle guard and SQL parent lock, as for HTTP.
        No source-task deletion or test authorization follows from this metadata.
        """
        scope_id=_id(scope_id)
        if self.expected_exploration_id is None:raise ScopeUnavailable('Exact exploration required')
        with self.guard():
            self._check()
            with self.executor.transaction() as tx:
                rows=tx.fetch_all(Query("""SELECT id,status,paused,queued FROM tasks
WHERE id=$1 AND exploration_id=$2 AND deleted_at IS NULL AND archived_at IS NULL
AND NOT EXISTS (SELECT 1 FROM task_archives WHERE task_id=$1) FOR UPDATE""",
                    (self.task_id,self.expected_exploration_id)))
                if len(rows)!=1 or rows[0][0]!=self.task_id or rows[0][1] not in ('created','done','failed','timeout') or rows[0][2] is not False or rows[0][3] is not False:
                    raise ScopeDenied('Task unavailable or active')
                if not callable(approve_delete) or approve_delete(self.task_id,scope_id) is not True:
                    raise ScopeDenied('Explicit deletion authorization required')
                self._check()
                count=tx.execute(Query('DELETE FROM task_scope WHERE id=$1 AND task_id=$2',(scope_id,self.task_id)))
                if type(count) is not int or count not in (0,1):raise ScopeUnavailable('Unknown scope deletion outcome')
                self._check()
                return count==1
