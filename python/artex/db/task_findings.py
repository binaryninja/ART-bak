"""Bounded task graph findings and direct-source provenance, never global fallback.

Source task branch of server.findings. Actual TaskCoverageStore supplies exact
parent/source identities and grants. Assets require an additional explicit grant.
Duplicate standalone mappings and oversized source sets refuse rather than guess
which metadata to expose. No mutations, archive reads or target execution.
"""
from datetime import datetime,timezone
import json
from .core import Query,int64
from .task_coverage import TaskCoverageStore,CoverageDenied,CoverageUnavailable
from ..server.findings import _label

class TaskFindingsStore:
    def __init__(self,coverage,*,asset_authorize):
        if type(coverage) is not TaskCoverageStore or not callable(asset_authorize):raise ValueError('Actual context and explicit asset grant required')
        self.coverage=coverage;self.asset_authorize=asset_authorize
        self.executor=coverage.executor;self.scope=coverage.scope;self.guard=coverage.guard
        self._pins=(coverage,coverage.executor,coverage.scope,coverage.guard,coverage.authorize,coverage.current,asset_authorize)
    def _check(self):
        c=self.coverage
        values=(c,self.executor,self.scope,self.guard,c.authorize,c.current,self.asset_authorize)
        if any(a is not b for a,b in zip(values,self._pins)) or c.executor is not self.executor or c.scope is not self.scope or c.guard is not self.guard:raise CoverageDenied('Finding binding changed')
        c._check()
        if any(a is not b for a,b in zip((self.coverage,self.executor,self.scope,self.guard,c.authorize,c.current,self.asset_authorize),self._pins)):raise CoverageDenied('Finding binding changed')
    def read(self,disclosure):
        if not callable(disclosure):raise ValueError('Explicit disclosure grant required')
        self._check();c=self.coverage;identities=();asset_ids=()
        def allow():
            self._check()
            for task,exp in identities:c._allow(task,exp)
            if asset_ids and self._pins[6](self.scope,asset_ids) is not True:raise CoverageDenied('Finding asset read denied')
            self._check()
            if disclosure() is not True:raise CoverageDenied('Finding disclosure denied')
            self._check()
        with c._context() as (tx,ids,exps,_):
            identities=tuple((task,exp) for exp,task in exps.items());allow()
            rows=tx.fetch_all(Query("""WITH selected AS (
SELECT n.id,n.payload,n.created_at,t.id AS task_id,t.description,
f.id AS finding_id,COALESCE(f.status,'pending') AS status,COALESCE(f.asset_ids,'[]'::jsonb) AS asset_ids,
(SELECT count(*) FROM finding_traffic_bindings b WHERE b.finding_id=f.id) AS traffic_count,
count(*) OVER(PARTITION BY t.id) AS owner_count,count(*) OVER(PARTITION BY n.id) AS mappings
FROM tasks t JOIN exploration_nodes n ON n.exploration_id=t.exploration_id AND n.kind='finding'
LEFT JOIN findings f ON f.task_id=t.id AND f.node_id=n.id
WHERE t.id=ANY($1::bigint[]) AND t.deleted_at IS NULL), bounded AS (
SELECT *,sum(octet_length(payload::text)+octet_length(COALESCE(description,''))+octet_length(status)+octet_length(asset_ids::text)) OVER() AS bytes FROM selected)
SELECT id,CASE WHEN bytes<=$2 THEN payload::text END,created_at,task_id,
CASE WHEN bytes<=$2 THEN COALESCE(description,'') END,finding_id,
CASE WHEN bytes<=$2 THEN status END,CASE WHEN bytes<=$2 THEN asset_ids::text END,
traffic_count,owner_count,mappings FROM bounded ORDER BY (task_id<>$3),task_id,id DESC LIMIT $4""",(ids,c.max_bytes,self.scope.task_id,c.max_rows+1)))
            allow()
            if len(rows)>c.max_rows:raise CoverageUnavailable('Finding row bound exceeded')
            output=[];requested=set()
            for node,payload,created,task,description,fid,status,aids,count,owner_count,mappings in rows:
                if task not in ids or mappings!=1 or type(owner_count) is not int or owner_count>200:raise CoverageUnavailable('Ambiguous or oversized finding context')
                if any(v is None for v in (payload,description,status,aids)):raise CoverageUnavailable('Finding byte bound exceeded')
                if int64(node,'node')<=0 or not isinstance(created,datetime) or created.tzinfo is None:raise CoverageUnavailable('Invalid finding identity')
                value=json.loads(payload);anchored=json.loads(aids)
                if type(value) is not dict or type(anchored) is not list or len(anchored)>c.max_rows or any(type(i) is not int or i<=0 for i in anchored):raise CoverageUnavailable('Invalid finding projection')
                if any(type(value.get(k,'')) is not str for k in ('vulnclass','name','severity','summary')):raise CoverageUnavailable('Malformed finding payload')
                if type(count) is not int or count<0:raise CoverageUnavailable('Invalid traffic count')
                item=dict(id=str(node),vulnclass=value.get('vulnclass',''),severity=value.get('severity',''),summary=value.get('summary',''),status=status if fid else 'pending',evidence=json.dumps(value['evidence'],ensure_ascii=False,separators=(',',':')) if 'evidence' in value else '',ts=created.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z'),task_id=str(task),traffic_count=count,evidence_version=0,report_evidence_version=0,report_stale=False)
                if value.get('name'):item['name']=value['name']
                if description:item['task_description']=description
                if fid:
                    if int64(fid,'finding')<=0:raise CoverageUnavailable('Invalid standalone finding')
                    item['finding_id']=str(fid)
                if task!=self.scope.task_id:item.update(source_task_id=str(task),inherited=True)
                requested.update(anchored);output.append((item,anchored))
            if len(requested)>c.max_rows:raise CoverageUnavailable('Asset bound exceeded')
            asset_ids=tuple(sorted(requested));allow();assets={}
            if asset_ids:
                names=('type','url','method','domain','ip','app_name','bundle_id','service_name','root_domain')
                size='+'.join("octet_length(COALESCE("+n+",''))" for n in names)
                fields=','.join("CASE WHEN bytes<=$2 THEN COALESCE("+n+",'') END" for n in names)
                found=tx.fetch_all(Query('WITH bounded AS (SELECT *,sum('+size+') OVER() AS bytes FROM assets WHERE id=ANY($1::bigint[])) SELECT id,port,'+fields+' FROM bounded ORDER BY id LIMIT $3',(list(asset_ids),c.max_bytes,c.max_rows+1)))
                allow()
                if len(found)>c.max_rows:raise CoverageUnavailable('Asset row bound exceeded')
                for aid,port,*values in found:
                    if aid not in requested or any(v is None for v in values):raise CoverageUnavailable('Asset projection unavailable')
                    a=dict(zip(names,values),id=aid,port=port);assets[aid]=dict(id=str(aid),type=a['type'],label=_label(a))
            result=[]
            for item,anchored in output:
                refs=[assets[i] for i in anchored if i in assets]
                if refs:item['assets']=refs
                result.append(item)
            result=c._bounded(result);allow()
        with self._pins[3](self.scope):allow()
        def validate_disclosure():
            with self._pins[3](self.scope):allow()
        return result,validate_disclosure
