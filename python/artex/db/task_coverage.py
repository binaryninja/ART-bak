"""Bounded, read-only task coverage/asset references from source task_assets_context.

Current task plus DIRECT related sources only, individually authorized before
asset/content reads. No recursive inheritance, global asset fallback, copied
scope, writes or discovery. Existing archived contexts fail closed: archive joins
are not implemented. Guard/current must fence task/source deletion and relation
changes; source row locks supplement host authority. SQL uses native bound arrays,
JSONB payloads and PostgreSQL try_inet/inet containment exactly as source coverage.
Coverage is approximate: every fact anchor counts tested, not proof of a test.
Keyword scope does not match denominator (source behavior). Anchored assets join
the denominator even if not declared in scope, matching source context coverage.

Graph connector root/company lookup requires separate explicit read authorization;
missing policy rejects, never silently reads the global catalog. Missing connector
root uses source synthetic r:<domain>. Bounds fail closed, never truncate metrics.
No HTTP/default wiring. Scope grants authorize reads, never testing those assets.
"""
from dataclasses import dataclass
from contextlib import contextmanager
from urllib.parse import urlsplit
import json
from .core import Query,int64
from .tool_catalog import value_json

class CoverageDenied(PermissionError):pass
class CoverageUnavailable(RuntimeError):pass

@dataclass(frozen=True)
class CoverageScope:
    task_id:int
    exploration_id:int
    revision:str

# Freeze the already-authorized task IDs; never allow a fresh relation added
# during a query to silently enter the result. IDs are bound BIGINT[] values.
CONTEXT="context_tasks AS (SELECT id AS task_id,exploration_id FROM tasks WHERE id=ANY($1) AND deleted_at IS NULL)"
COVERAGE=CONTEXT+''', target AS (
 SELECT DISTINCT a.id,a.type,COALESCE(a.url,a.domain,a.ip,a.app_name,a.root_domain,'') AS label
 FROM assets a JOIN task_scope ts ON (
 (ts.kind='company' AND a.company_id=ts.company_id)
 OR (ts.kind='root_domain' AND a.root_domain=ts.domain)
 OR (ts.kind='subdomain' AND a.domain=ts.domain)
 OR (ts.kind IN ('ip','cidr') AND ts.net >>= try_inet(a.ip))
 OR (ts.kind='icp' AND (
 lower(regexp_replace(COALESCE(a.icp,''),'[[:space:]]+','','g'))=ts.value
 OR lower(regexp_replace(COALESCE(a.app_icp,''),'[[:space:]]+','','g'))=ts.value)))
 JOIN context_tasks ctx ON ctx.task_id=ts.task_id
 UNION
 SELECT a.id,a.type,COALESCE(a.url,a.domain,a.ip,a.app_name,a.root_domain,'') AS label
 FROM assets a JOIN exploration_anchors ea ON ea.asset_id=a.id
 JOIN exploration_nodes en ON en.id=ea.node_id
 JOIN context_tasks ctx ON ctx.exploration_id=en.exploration_id
), tested AS (
 SELECT DISTINCT ea.asset_id FROM exploration_anchors ea
 JOIN exploration_nodes en ON en.id=ea.node_id AND en.kind='fact'
 JOIN context_tasks ctx ON ctx.exploration_id=en.exploration_id
)'''

def _positive(value):
    if int64(value,'coverage identity')<=0:raise ValueError('Positive identity required')
    return value

def _count(value):
    if type(value) is not int or not 0<=value<2**63:raise CoverageUnavailable('Invalid aggregate count')
    return value

class TaskCoverageStore:
    def __init__(self,executor,scope,*,authorize,current,lifecycle_guard,connector_authorize=None,max_rows=2000,max_bytes=2097152):
        if not isinstance(scope,CoverageScope):raise ValueError('Coverage scope required')
        _positive(scope.task_id);_positive(scope.exploration_id)
        if not isinstance(scope.revision,str) or not scope.revision or len(scope.revision)>256:raise ValueError('Bounded revision required')
        if any(not callable(f) for f in (authorize,current,lifecycle_guard)) or (connector_authorize is not None and not callable(connector_authorize)):raise ValueError('Explicit read authority required')
        if type(max_rows) is not int or not 1<=max_rows<=10000 or type(max_bytes) is not int or not 1024<=max_bytes<=16777216:raise ValueError('Invalid coverage bounds')
        self.executor,self.scope=executor,scope;self._executor,self._scope=executor,scope
        self.authorize,self.current,self.guard,self.connector=authorize,current,lifecycle_guard,connector_authorize
        self.max_rows,self.max_bytes=max_rows,max_bytes
    def _check(self):
        if self.executor is not self._executor or self.scope!=self._scope:raise CoverageDenied('Coverage binding changed')
        if self.current(self._scope) is not True:raise CoverageDenied('Coverage authority stale')
        if self.executor is not self._executor or self.scope!=self._scope:raise CoverageDenied('Coverage binding changed')
    def _allow(self,task,exp):
        if self.authorize(self._scope,task,exp) is not True:raise CoverageDenied('Task context read not authorized')
        self._check()
    @contextmanager
    def _context(self):
        callbacks=(self.authorize,self.current,self.guard,self.connector)
        def policy_pins():
            if any(a is not b for a,b in zip(callbacks,(self.authorize,self.current,self.guard,self.connector))):
                raise CoverageDenied('Coverage policy changed')
        with callbacks[2](self._scope):
            policy_pins()
            self._check();self._allow(self.scope.task_id,self.scope.exploration_id)
            with self.executor.transaction() as tx:
                rows=tx.fetch_all(Query('''SELECT id,exploration_id,COALESCE(coverage_enabled,true) FROM tasks
WHERE id=$1 AND exploration_id=$2 AND deleted_at IS NULL AND archived_at IS NULL
AND NOT EXISTS(SELECT 1 FROM task_archives WHERE task_id=$1) FOR SHARE''',(self.scope.task_id,self.scope.exploration_id)))
                if len(rows)!=1 or rows[0][:2] not in ((self.scope.task_id,self.scope.exploration_id),[self.scope.task_id,self.scope.exploration_id]) or type(rows[0][2]) is not bool:raise CoverageUnavailable('Task context unavailable')
                enabled=rows[0][2]
                sources=tx.fetch_all(Query('''SELECT source.id,source.exploration_id,source.archived_at,
EXISTS(SELECT 1 FROM task_archives WHERE task_id=source.id)
FROM task_relations relation JOIN tasks source ON source.id=relation.source_task_id
WHERE relation.task_id=$1 AND source.deleted_at IS NULL
ORDER BY source.id LIMIT 9 FOR SHARE OF relation,source''',(self.scope.task_id,)))
                if len(sources)>8:raise CoverageUnavailable('Direct source bound exceeded')
                ids=[self.scope.task_id];exps={self.scope.exploration_id:self.scope.task_id}
                for id,exp,archived,archiving in sources:
                    _positive(id);_positive(exp)
                    if id in ids or exp in exps:raise CoverageUnavailable('Duplicate context identity')
                    self._allow(id,exp)
                    if archived is not None or archiving is not False:raise CoverageUnavailable('Archived source projection unavailable')
                    ids.append(id);exps[exp]=id
                policy_pins();self._check();yield tx,ids,exps,enabled
                policy_pins()
                for exp,task in exps.items():self._allow(task,exp)
                self._check()
            policy_pins()
            for exp,task in exps.items():self._allow(task,exp)
            self._check();policy_pins()
    def _bounded(self,value):
        if len(json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf-8'))>self.max_bytes:raise CoverageUnavailable('Coverage output exceeds bound')
        return value
    def coverage(self):
        with self._context() as (tx,ids,exps,enabled):
            result=dict(enabled=enabled,scope_rows=0,denominator=0,tested=0,pct=None,by_type=[])
            if enabled:
                counts=tx.fetch_all(Query('WITH '+CONTEXT+' SELECT count(*) FROM task_scope ts JOIN context_tasks ctx ON ctx.task_id=ts.task_id',(ids,)))
                if len(counts)!=1:raise CoverageUnavailable('Scope aggregate unavailable')
                result['scope_rows']=_count(counts[0][0])
                rows=tx.fetch_all(Query('WITH '+COVERAGE+' SELECT target.type,count(*) AS total,count(*) FILTER (WHERE target.id IN (SELECT asset_id FROM tested)) AS tested FROM target GROUP BY target.type ORDER BY target.type LIMIT 129',(ids,)))
                if len(rows)>128:raise CoverageUnavailable('Asset type bound exceeded')
                for kind,total,tested in rows:
                    if not isinstance(kind,str) or _count(tested)>_count(total):raise CoverageUnavailable('Invalid coverage aggregate')
                    result['by_type'].append(dict(type=kind,total=total,tested=tested));result['denominator']+=total;result['tested']+=tested
                if result['denominator']:result['pct']=result['tested']/result['denominator']
            return self._bounded(result)
    def untested(self,asset_type='',limit=10,offset=0):
        if not isinstance(asset_type,str) or len(asset_type)>128 or type(limit) is not int or type(offset) is not int:raise ValueError('Invalid coverage page')
        limit=10 if limit<=0 else limit;offset=max(0,offset)
        if limit>self.max_rows or offset>1000000:raise ValueError('Coverage page exceeds bound')
        with self._context() as (tx,ids,exps,enabled):
            where=" WHERE target.id NOT IN (SELECT asset_id FROM tested) AND ($2='' OR target.type=$2)"
            count=tx.fetch_all(Query('WITH '+COVERAGE+' SELECT count(*) FROM target'+where,(ids,asset_type)))
            if len(count)!=1:raise CoverageUnavailable('Untested aggregate unavailable')
            rows=tx.fetch_all(Query('WITH '+COVERAGE+', page AS (SELECT target.id,target.type,target.label FROM target'+where+''' ORDER BY target.id LIMIT $3 OFFSET $4)
SELECT id,type,CASE WHEN sum(octet_length(label)+octet_length(type::text)) OVER () <= $5 THEN label ELSE NULL END FROM page ORDER BY id''',(ids,asset_type,limit,offset,self.max_bytes)))
            if len(rows)>limit:raise CoverageUnavailable('Invalid page size')
            if any(label is None for _,_,label in rows):raise CoverageUnavailable('Coverage page byte bound exceeded')
            result=[dict(id=_positive(id),type=kind,label=label) for id,kind,label in rows]
            self._bounded(result);return result,_count(count[0][0])
    def asset_refs(self,asset_id):
        _positive(asset_id)
        with self._context() as (tx,ids,exps,enabled):
            rows=tx.fetch_all(Query('''WITH '''+CONTEXT+''' SELECT en.id,en.kind,en.state,
CASE WHEN sum(octet_length(en.payload::text)) OVER () <= $5 THEN en.payload ELSE NULL END,ctx.task_id
FROM exploration_anchors ea JOIN exploration_nodes en ON en.id=ea.node_id
JOIN context_tasks ctx ON ctx.exploration_id=en.exploration_id
WHERE ea.asset_id=$2 AND en.kind IN ('intent','fact','finding')
AND (ctx.task_id=$3 OR en.kind<>'intent' OR en.state IN ('done','blocked','exhausted','stopped'))
ORDER BY en.id DESC LIMIT $4''',(ids,asset_id,self.scope.task_id,self.max_rows+1,self.max_bytes)))
            if len(rows)>self.max_rows:raise CoverageUnavailable('Asset references exceed bound')
            result=[]
            for id,kind,state,payload,task in rows:
                if task not in ids or kind not in ('intent','fact','finding') or (task!=self.scope.task_id and kind=='intent' and state not in ('done','blocked','exhausted','stopped')):raise CoverageUnavailable('Foreign or nonterminal source reference')
                if payload is None:raise CoverageUnavailable('Asset reference byte bound exceeded')
                data=value_json(payload)
                if type(data) is not dict:raise CoverageUnavailable('Invalid reference payload')
                summary=next((data[k] for k in ('summary','text') if isinstance(data.get(k),str) and data[k].strip()),'')
                row=dict(id=_positive(id),kind=kind,state=state,summary=summary)
                if task!=self.scope.task_id:row.update(source_task_id=str(task),inherited=True)
                result.append(row)
            return self._bounded(result)
    def _connector(self,kind,value):
        if not callable(self.connector) or self.connector(self._scope,kind,value) is not True:raise CoverageDenied('Connector read not authorized')
        self._check()
    def coverage_graph(self):
        with self._context() as (tx,ids,exps,enabled):
            text_fields=('domain','root_domain','ip','url','service_type','app_name','page_title')
            size="+".join("octet_length(COALESCE(a."+field+",''))" for field in text_fields)
            def bounded(field):
                return "CASE WHEN sum("+size+") OVER () <= $3 THEN COALESCE(a."+field+",'') ELSE NULL END"
            projection=','.join(bounded(field) for field in text_fields[:4])
            tail=','.join(bounded(field) for field in text_fields[4:])
            rows=tx.fetch_all(Query('WITH '+COVERAGE+" SELECT a.id,a.type,COALESCE(a.company_id,0),"+
                projection+",COALESCE(a.port,0),"+tail+",COALESCE(a.status_code,0),(a.id IN (SELECT asset_id FROM tested)) "+
                "FROM assets a JOIN target t ON t.id=a.id ORDER BY a.id LIMIT $2",(ids,self.max_rows+1,self.max_bytes)))
            if any(any(row[i] is None for i in (3,4,5,6,8,9,10)) for row in rows):
                raise CoverageUnavailable('Coverage graph byte bound exceeded')
            remaining=self.max_bytes-sum(len(str(row[i]).encode('utf-8')) for row in rows for i in (3,4,5,6,8,9,10))
            if remaining<0:raise CoverageUnavailable('Coverage graph byte bound exceeded')
            if len(rows)>self.max_rows:raise CoverageUnavailable('Coverage graph exceeds bound')
            nodes=[];bykey={};roots={};subs={};ips={};services={};service_hosts={};companies=set()
            def add(node):
                if node['key'] in bykey:return
                if len(nodes)>=self.max_rows:raise CoverageUnavailable('Coverage connectors exceed bound')
                bykey[node['key']]=node;nodes.append(node)
            def hostport(n):
                host,port=n.get('domain',''),n.get('port',0)
                if not host and n.get('url'):
                    try:
                        parsed=urlsplit(n['url']);host=(parsed.hostname or '').lower()
                        if not port:port=parsed.port or (443 if parsed.scheme.lower()=='https' else 80 if parsed.scheme.lower()=='http' else 0)
                    except ValueError:host=''
                return host or n.get('ip',''),port
            for row in rows:
                id,kind,company,domain,root,ip,url,port,service,app,title,status,tested=row
                if type(tested) is not bool:raise CoverageUnavailable('Invalid tested flag')
                n=dict(key=f'a:{_positive(id)}',kind=kind,asset_id=id,tested=tested,in_scope=True)
                for key,val in dict(company_id=company,domain=domain,root_domain=root,ip=ip,url=url,port=port,service_type=service,app_name=app,page_title=title,status_code=status).items():
                    if val:n[key]=val
                n['label']=(url if kind in ('endpoint','service') and url else app if kind=='app' and app else next((v for v in (url,domain,ip,app,root) if v),n['key']))
                add(n)
                if kind=='root_domain' and domain:roots[domain]=n['key']
                if kind=='subdomain' and domain:subs[domain]=n['key']
                if kind=='ip' and ip:ips[ip]=n['key']
                if kind=='service':
                    host,p=hostport(n)
                    if host:services[(host,p)]=n['key'];service_hosts[host]=n['key']
                if company>0:companies.add(company)
            missing=sorted({n['root_domain'] for n in nodes if n['kind']=='subdomain' and n.get('root_domain') and n['root_domain'] not in roots})
            for root in missing:
                self._connector('root_domain',root)
                found=tx.fetch_all(Query("SELECT id,COALESCE(company_id,0) FROM assets WHERE type='root_domain' AND domain=$1 LIMIT 1",(root,)))
                self._check()
                n=dict(key='r:'+root,kind='root_domain',domain=root,label=root,tested=False,in_scope=False)
                if found:
                    id,company=found[0];n.update(key=f'a:{_positive(id)}',asset_id=id)
                    if company>0:n['company_id']=company;companies.add(company)
                add(n);roots[root]=n['key']
            for company in sorted(companies):
                self._connector('company',company)
                found=tx.fetch_all(Query('SELECT CASE WHEN octet_length(name)<=$2 THEN name ELSE NULL END FROM companies WHERE id=$1',(company,remaining)))
                self._check()
                if found:
                    if found[0][0] is None:raise CoverageUnavailable('Company projection byte bound exceeded')
                    remaining-=len(found[0][0].encode('utf-8'))
                    if remaining<0:raise CoverageUnavailable('Company projection byte bound exceeded')
                    add(dict(key=f'c:{company}',kind='company',company_id=company,label=found[0][0],tested=False,in_scope=False))
            edges=[]
            def first(*keys):return next((k for k in keys if k and k in bykey),'')
            for n in nodes:
                parent='';kind=n['kind']
                if kind=='subdomain':parent=roots.get(n.get('root_domain',''),'')
                elif kind in ('root_domain','app','ip') and n.get('company_id'):parent=f"c:{n['company_id']}"
                elif kind=='service':parent=first(subs.get(n.get('domain','')),ips.get(n.get('ip','')),roots.get(n.get('root_domain','')))
                elif kind=='endpoint':
                    host,port=hostport(n)
                    parent=first(services.get((host,port)),service_hosts.get(host),subs.get(host),subs.get(n.get('domain','')),ips.get(host),ips.get(n.get('ip','')),roots.get(n.get('root_domain','')))
                if parent and parent!=n['key'] and parent in bykey:edges.append(dict(src=n['key'],dst=parent))
            for root in missing:self._connector('root_domain',root)
            for company in sorted(companies):self._connector('company',company)
            return self._bounded(dict(nodes=nodes,edges=edges))
