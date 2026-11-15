"""Source-faithful archive metadata inside a caller-owned snapshot transaction.

Pass ArchiveOwnershipMetadata() explicitly as ArchiveSnapshotExporter metadata.
No transaction is opened, no ownership/lifecycle permission inferred. Source
exclusive-host logic considers task_ids and graph anchors, while exclusive asset
candidates additionally include task links and finding references. Company-owned
assets never become exclusive. These intentionally different upstream rules are
preserved, not generalized to deletion authorization. This returns metadata ONLY.

No DNS/public-suffix/network/filesystem calls. URL parsing supports ordinary
absolute/network-path URLs; ambiguous invalid host syntax fails closed rather
than omitting a potentially protected shared host. Bounded result row/JSON byte
limits reject oversized exports, never truncate them or substitute zero counts.
"""
import json,re
from urllib.parse import urlsplit
from .db.core import Query,int64
from .archive_bundle import _json

class MetadataUnavailable(RuntimeError):pass

HOSTS_SQL = "\nWITH current_assets AS (\n  SELECT id FROM assets WHERE $1 = ANY(task_ids)\n  UNION\n  SELECT ea.asset_id\n  FROM exploration_anchors ea\n  JOIN exploration_nodes n ON n.id=ea.node_id\n  WHERE n.exploration_id=$2\n),\nother_assets AS (\n  SELECT DISTINCT a.id\n  FROM assets a\n  WHERE EXISTS (\n    SELECT 1 FROM tasks t\n    WHERE t.id<>$1 AND t.deleted_at IS NULL AND t.id=ANY(a.task_ids)\n  ) OR EXISTS (\n    SELECT 1\n    FROM exploration_anchors ea\n    JOIN exploration_nodes n ON n.id=ea.node_id\n    JOIN tasks t ON t.exploration_id=n.exploration_id\n    WHERE ea.asset_id=a.id AND t.id<>$1 AND t.deleted_at IS NULL\n  )\n)\nSELECT COALESCE(a.domain,''), COALESCE(a.ip,''), COALESCE(a.url,''), true\nFROM assets a JOIN current_assets c ON c.id=a.id\nUNION ALL\nSELECT COALESCE(a.domain,''), COALESCE(a.ip,''), COALESCE(a.url,''), false\nFROM assets a JOIN other_assets o ON o.id=a.id"
EXCLUSIVE_SQL = "WITH candidate AS (SELECT id FROM assets WHERE $1=ANY(task_ids)\nUNION SELECT link.asset_id FROM task_asset_links link WHERE link.task_id=$1\nUNION SELECT anchor.asset_id FROM exploration_anchors anchor\n      JOIN exploration_nodes node ON node.id=anchor.node_id WHERE node.exploration_id=$2\nUNION SELECT value::bigint FROM findings finding\n      CROSS JOIN LATERAL jsonb_array_elements_text(\n        CASE WHEN jsonb_typeof(finding.asset_ids)='array' THEN finding.asset_ids ELSE '[]'::jsonb END\n      ) value WHERE finding.task_id=$1 AND value ~ '^[0-9]+$')\nSELECT asset.id FROM assets asset JOIN candidate ON candidate.id=asset.id\nWHERE asset.company_id IS NULL\nAND NOT EXISTS (SELECT 1 FROM tasks task WHERE task.id<>$1 AND task.deleted_at IS NULL AND task.id=ANY(asset.task_ids))\nAND NOT EXISTS (\n SELECT 1 FROM exploration_anchors anchor JOIN exploration_nodes node ON node.id=anchor.node_id\n JOIN tasks task ON task.exploration_id=node.exploration_id\n WHERE anchor.asset_id=asset.id AND task.id<>$1 AND task.deleted_at IS NULL\n) ORDER BY asset.id"
TOKENS_SQL = "SELECT count(*),COALESCE(sum(input_tokens),0),COALESCE(sum(output_tokens),0),\nCOALESCE(sum(cache_read),0),COALESCE(sum(cache_write),0)\nFROM llm_usage WHERE COALESCE(task_id,'')=$1 OR exploration_id=$2"
AGGREGATES = (('token_profiles', "SELECT COALESCE(jsonb_agg(to_jsonb(x)),'[]'::jsonb) FROM (\nSELECT COALESCE(profile_name,'') profile_name,count(*) calls,1 tasks,\n       COALESCE(sum(input_tokens),0) input_tokens,COALESCE(sum(output_tokens),0) output_tokens,\n       COALESCE(sum(cache_read),0) cache_read_tokens,COALESCE(sum(cache_write),0) cache_write_tokens\nFROM llm_usage WHERE COALESCE(task_id,'')=$1 OR exploration_id=$2\nGROUP BY profile_name ORDER BY sum(input_tokens)+sum(output_tokens) DESC) x", ('task_string', 'exp_id')), ('token_daily', "SELECT COALESCE(jsonb_agg(to_jsonb(x)),'[]'::jsonb) FROM (\nSELECT COALESCE(profile_name,'') profile_name,to_char(ts AT TIME ZONE 'UTC','YYYY-MM-DD') date,\n       COALESCE(sum(input_tokens),0) input_tokens,COALESCE(sum(output_tokens),0) output_tokens,\n       COALESCE(sum(cache_read),0) cache_read_tokens\nFROM llm_usage WHERE COALESCE(task_id,'')=$1 OR exploration_id=$2\nGROUP BY profile_name,date ORDER BY date) x", ('task_string', 'exp_id')), ('skills', "SELECT COALESCE(jsonb_object_agg(name,n),'{}'::jsonb) FROM (SELECT skill name,count(*) n FROM skill_usage WHERE (task_id=$1 OR exploration_id=$2) AND found GROUP BY skill) x", ('task_id', 'exp_id')), ('skill_stats', "SELECT COALESCE(jsonb_agg(to_jsonb(x)),'[]'::jsonb) FROM (\nSELECT skill,count(*) calls,1 tasks,\n       COALESCE(array_agg(DISTINCT agent_key) FILTER (WHERE agent_key IS NOT NULL),ARRAY[]::text[]) agents,\n       max(ts) last_used\nFROM skill_usage WHERE (task_id=$1 OR exploration_id=$2) AND found GROUP BY skill) x", ('task_id', 'exp_id')), ('missing_skill_stats', "SELECT COALESCE(jsonb_agg(to_jsonb(x)),'[]'::jsonb) FROM (\nSELECT skill,count(*) calls,0 tasks,\n       COALESCE(array_agg(DISTINCT agent_key) FILTER (WHERE agent_key IS NOT NULL),ARRAY[]::text[]) agents,\n       max(ts) last_used\nFROM skill_usage WHERE (task_id=$1 OR exploration_id=$2) AND NOT found GROUP BY skill) x", ('task_id', 'exp_id')), ('tools', "SELECT COALESCE(jsonb_object_agg(name,n),'{}'::jsonb) FROM (SELECT tool_key name,count(*) n FROM tool_usage WHERE task_id=$1 OR exploration_id=$2 GROUP BY tool_key) x", ('task_id', 'exp_id')), ('findings', "SELECT COALESCE(jsonb_object_agg(name,n),'{}'::jsonb) FROM (SELECT COALESCE(NULLIF(severity,''),'unknown') name,count(*) n FROM findings WHERE task_id=$1 GROUP BY severity) x", ('task_id',)), ('finding_stats', "SELECT jsonb_build_object(\n 'total',count(*),'pending',count(*) FILTER (WHERE status='pending'),\n 'critical',count(*) FILTER (WHERE severity='critical'),'high',count(*) FILTER (WHERE severity='high'),\n 'medium',count(*) FILTER (WHERE severity='medium'),'low',count(*) FILTER (WHERE severity='low'),\n 'vulnclasses',COALESCE(jsonb_agg(DISTINCT vulnclass) FILTER (WHERE vulnclass<>''),'[]'::jsonb))\nFROM findings WHERE task_id=$1", ('task_id',)))

def _host(raw):
    if not isinstance(raw,str):raise MetadataUnavailable('Invalid archive host field')
    raw=raw.strip()
    if not raw:return ''
    # Go URL.Parse errors are ignored by source; rejecting rather than ignoring
    # invalid input avoids erasing a shared-host protection on parser divergence.
    if any(ord(c)<33 for c in raw) or '\\' in raw:
        raise MetadataUnavailable('Ambiguous archive URL')
    try:
        parsed=urlsplit(raw)
        host=parsed.hostname or ''
        if parsed.netloc:
            parsed.port  # fail malformed numeric port rather than normalize
            if '%' in host:raise ValueError()
        return host.lower()
    except (ValueError,UnicodeError):raise MetadataUnavailable('Invalid archive URL') from None

def _hosts(row):
    result=set()
    for name in ('domain','ip'):
        value=row.get(name,'')
        if value is None:value=''
        if not isinstance(value,str):raise MetadataUnavailable('Invalid asset host field')
        if value.strip():result.add(value.strip().lower())
    value=row.get('url','') or ''
    host=_host(value)
    if host:result.add(host)
    return result

def _id(value):
    if isinstance(value,str) and re.fullmatch('[0-9]+',value):value=int(value)
    try:int64(value,'asset ID')
    except ValueError:raise MetadataUnavailable('Invalid asset identity') from None
    if value<=0:raise MetadataUnavailable('Invalid asset identity')
    return value

class ArchiveOwnershipMetadata:
    def __init__(self,*,max_rows=10000,max_json_bytes=8<<20):
        if type(max_rows) is not int or not 1<=max_rows<=1000000 or type(max_json_bytes) is not int or not 1<=max_json_bytes<=64<<20:raise ValueError('Invalid metadata bounds')
        self.max_rows,self.max_json_bytes=max_rows,max_json_bytes
    def __call__(self,tx,task_id,exp_id,tables):
        _id(task_id);_id(exp_id)
        if not isinstance(tables,dict) or not isinstance(tables.get('assets'),list):raise MetadataUnavailable('Snapshot assets required')
        assets=tables['assets']
        if len(assets)>self.max_rows:raise MetadataUnavailable('Asset metadata row limit exceeded')
        all_hosts=set();asset_ids=set();host_bytes=0
        def account(values):
            nonlocal host_bytes
            for value in values:
                if value is None or not isinstance(value,str):raise MetadataUnavailable('Host metadata byte limit exceeded')
                host_bytes+=len(value.encode('utf-8'))
            if host_bytes>self.max_json_bytes:raise MetadataUnavailable('Host metadata total byte limit exceeded')
        for row in assets:
            if not isinstance(row,dict):raise MetadataUnavailable('Invalid asset snapshot')
            account([row.get(key) or '' for key in ('domain','ip','url')])
            asset_ids.add(_id(row.get('id')));all_hosts.update(_hosts(row))
        candidates=set();protected=set()
        sql=('SELECT CASE WHEN total_bytes<=$4 THEN domain ELSE NULL END, '
             'CASE WHEN total_bytes<=$4 THEN ip ELSE NULL END, '
             'CASE WHEN total_bytes<=$4 THEN url ELSE NULL END, candidate FROM ('
             'SELECT domain,ip,url,candidate, sum(octet_length(domain)+octet_length(ip)+octet_length(url)) OVER () AS total_bytes FROM ('
             'SELECT * FROM ('+HOSTS_SQL+') archive_hosts(domain,ip,url,candidate) LIMIT $3) bounded_hosts) measured_hosts')
        rows=tx.fetch_all(Query(sql,(task_id,exp_id,self.max_rows+1,self.max_json_bytes)))
        if len(rows)>self.max_rows:raise MetadataUnavailable('Host metadata row limit exceeded')
        for row in rows:
            if len(row)!=4 or type(row[3]) is not bool:raise MetadataUnavailable('Invalid host metadata row')
            account(row[:3])
            selected=candidates if row[3] else protected
            selected.update(_hosts(dict(zip(('domain','ip','url'),row[:3]))))
        rows=tx.fetch_all(Query('SELECT * FROM ('+EXCLUSIVE_SQL+') archive_exclusive LIMIT $3',(task_id,exp_id,self.max_rows+1)))
        if len(rows)>self.max_rows:raise MetadataUnavailable('Exclusive asset row limit exceeded')
        exclusive=[]
        for row in rows:
            if len(row)!=1:raise MetadataUnavailable('Invalid exclusive asset row')
            id=_id(row[0])
            if id not in asset_ids:raise MetadataUnavailable('Exclusive asset outside snapshot')
            exclusive.append(id)
        values={'task_id':task_id,'task_string':str(task_id),'exp_id':exp_id}
        row=tx.fetch_one(Query(TOKENS_SQL,(str(task_id),exp_id)))
        if row is None or len(row)!=5 or any(type(value) is not int for value in row):raise MetadataUnavailable('Invalid token aggregate')
        stats={'tokens':dict(zip(('calls','input_tokens','output_tokens','cache_read_tokens','cache_write_tokens'),row))}
        size=0
        for name,inner,selectors in AGGREGATES:
            params=tuple(values[key] for key in selectors);bound=len(params)+1
            sql='SELECT CASE WHEN octet_length(raw)<=$'+str(bound)+' THEN raw ELSE NULL END FROM (SELECT value::text AS raw FROM ('+inner+') aggregate_value(value)) bounded'
            row=tx.fetch_one(Query(sql,params+(self.max_json_bytes,)))
            if row is None or len(row)!=1 or not isinstance(row[0],(str,bytes)):raise MetadataUnavailable('Aggregate unavailable or byte limit exceeded')
            raw=row[0].encode('utf-8') if isinstance(row[0],str) else row[0];size+=len(raw)
            if size>self.max_json_bytes:raise MetadataUnavailable('Aggregate total byte limit exceeded')
            value=_json(raw)
            expected=dict if name in ('skills','tools','findings','finding_stats') else list
            if not isinstance(value,expected):raise MetadataUnavailable('Invalid aggregate shape')
            stats[name]=value
        return dict(hosts=sorted(all_hosts),exclusive_hosts=sorted(candidates-protected),exclusive_asset_ids=exclusive,aggregate_stats=stats)
