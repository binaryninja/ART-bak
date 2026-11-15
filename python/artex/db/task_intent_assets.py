"""Source IntentAssets read over the existing authorized direct task contexts.

Read-only anchors, never testing authority. Local intents of every state plus
terminal intents of individually authorized direct sources; no recursive/global
fallback, archived sources or writes. Source default provenance prose translated.
"""
from .core import Query
from .task_coverage import TaskCoverageStore,CoverageDenied,CoverageUnavailable
class TaskIntentAssetsStore:
    def __init__(self,coverage):
        if type(coverage) is not TaskCoverageStore:raise ValueError('Actual scoped context store required')
        self.coverage=coverage;self._coverage=coverage
        self.executor=coverage.executor;self.scope=coverage.scope;self.guard=coverage.guard
        self._pins=(coverage.executor,coverage.scope,coverage.guard,coverage.authorize,coverage.current)
    def _check(self):
        c=self.coverage;p=self._pins
        if c is not self._coverage or any(a is not b for a,b in zip((c.executor,c.scope,c.guard,c.authorize,c.current),p)) or self.executor is not p[0] or self.scope is not p[1] or self.guard is not p[2]:raise CoverageDenied('Intent asset binding changed')
        c._check()
        if any(a is not b for a,b in zip((c.executor,c.scope,c.guard,c.authorize,c.current),p)):raise CoverageDenied('Intent asset binding changed')
    def list(self,*,disclosure=None,with_disclosure_receipt=False):
        if disclosure is not None and not callable(disclosure):raise ValueError("Invalid disclosure policy")
        self._check();c=self.coverage
        with c._context() as (tx,ids,exps,enabled):
            self._check()
            sql="""WITH context AS (
SELECT id AS task_id,exploration_id,(id<>$2) AS inherited FROM tasks WHERE id=ANY($1::bigint[]) AND deleted_at IS NULL), rows AS (
SELECT intent.id AS intent_id,asset.id AS asset_id,asset.type,
CASE asset.type WHEN 'root_domain' THEN COALESCE(asset.domain,'') WHEN 'subdomain' THEN COALESCE(asset.domain,'')
WHEN 'ip' THEN COALESCE(asset.ip,'') WHEN 'app' THEN COALESCE(asset.app_name,'')
WHEN 'service' THEN COALESCE(NULLIF(asset.url,''),NULLIF(concat_ws(':',COALESCE(NULLIF(asset.domain,''),NULLIF(asset.ip,'')),asset.port::text),''),NULLIF(asset.service_name,''),'#'||asset.id::text)
WHEN 'endpoint' THEN COALESCE(NULLIF(asset.url,''),'#'||asset.id::text) ELSE '#'||asset.id::text END AS label,
COALESCE(link.source,'anchor') AS source,COALESCE(NULLIF(link.source_summary,''),'Intent anchors this asset on the blackboard') AS source_summary,
link.source_node_id,context.task_id AS source_task_id,context.inherited
FROM context JOIN exploration_nodes intent ON intent.exploration_id=context.exploration_id AND intent.kind='intent'
JOIN exploration_anchors anchor ON anchor.node_id=intent.id JOIN assets asset ON asset.id=anchor.asset_id
LEFT JOIN task_asset_links link ON link.task_id=context.task_id AND link.asset_id=asset.id
WHERE NOT context.inherited OR intent.state IN ('done','blocked','exhausted','stopped')),
bounded AS (SELECT *,sum(octet_length(type::text)+octet_length(label)+octet_length(source)+octet_length(source_summary)) OVER () AS bytes FROM rows)
SELECT intent_id,asset_id,CASE WHEN bytes<=$3 THEN type ELSE NULL END,
CASE WHEN bytes<=$3 THEN label ELSE NULL END,CASE WHEN bytes<=$3 THEN source ELSE NULL END,
CASE WHEN bytes<=$3 THEN source_summary ELSE NULL END,source_node_id,source_task_id,inherited
FROM bounded ORDER BY inherited,intent_id DESC,asset_id LIMIT $4"""
            rows=tx.fetch_all(Query(sql,(ids,self.scope.task_id,c.max_bytes,c.max_rows+1)))
            self._check()
            for exp,task in exps.items():c._allow(task,exp)
            self._check()
            if len(rows)>c.max_rows:raise CoverageUnavailable('Intent asset bound exceeded')
            out=[]
            for row in rows:
                intent,asset,kind,label,source,summary,node,task,inherited=row
                if any(v is None for v in (kind,label,source,summary)) or task not in ids or type(inherited) is not bool or inherited!=(task!=self.scope.task_id):raise CoverageUnavailable('Invalid intent asset projection')
                item=dict(intent_id=intent,asset_id=asset,type=kind,label=label,source=source,source_summary=summary,source_node_id=node,source_task_id=task,inherited=inherited)
                if node is None:item.pop('source_node_id')
                out.append(item)
            result=c._bounded(out)
            if disclosure is not None and disclosure() is not True:raise CoverageDenied("Disclosure denied")
            self._check()
            for exp,task in exps.items():c._allow(task,exp)
            self._check()
        self._check()
        contexts=tuple(exps.items())
        def final_disclosure():
            # Fresh separate guard acquisition after transaction exit/serialization.
            # The host current/guard fences context relation and generation changes.
            with self.guard(self.scope):
                self._check()
                if disclosure is not None and disclosure() is not True:raise CoverageDenied('Disclosure denied')
                self._check()
                for exp,task in contexts:c._allow(task,exp);self._check()
                if disclosure is not None and disclosure() is not True:raise CoverageDenied('Disclosure denied')
                self._check()
            self._check()
        final_disclosure()
        return (result,final_disclosure) if with_disclosure_receipt else result
