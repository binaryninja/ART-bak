"""Bounded read-only persisted archive overview; no package/lifecycle execution."""
import json,re
from datetime import datetime
from .core import Query
from .archives import STATES

class ArchiveCatalogUnavailable(RuntimeError):pass
PHASES=frozenset(('queued','starting','interrupted','failed','ready','stage_files','snapshot_database','snapshot_traffic',
    'compress','compact_traffic','compact_database','verify_package','restore_traffic','restore_files','restore_database',
    'database_restored','consume_package','stage_package_delete','delete_metadata'))
_FIELDS=('id','task_id','state','phase','progress','format_version','sha256','original_size','compressed_size',
 'task_name','task_description','task_goal','original_status','source_task_ids','remaining_timeout_seconds','data_counts',
 'archived_at','requested_at','created_at','updated_at')
_PROJECTION='jsonb_build_object('+','.join("'"+k+"',a."+k for k in _FIELDS)+",'category_id',a.category_id_snapshot,'category_name',a.category_name_snapshot,"+"'aggregate_stats',jsonb_strip_nulls(jsonb_build_object('tokens',a.aggregate_stats->'tokens','skills',a.aggregate_stats->'skills','tools',a.aggregate_stats->'tools','findings',a.aggregate_stats->'findings')))"
_WHERE="WHERE ($1='' OR task_id::text ILIKE '%'||$1||'%' OR task_name ILIKE '%'||$1||'%' OR task_description ILIKE '%'||$1||'%') AND ($2='' OR state=$2) AND ($3::bigint IS NULL OR id=$3)"
_SQL="""WITH filtered AS (SELECT """+','.join(_FIELDS+('category_id_snapshot','category_name_snapshot','aggregate_stats'))+""" FROM task_archives """+_WHERE+"""), total AS (SELECT COUNT(*) AS n FROM filtered),
page AS (SELECT """+_PROJECTION+"""::text AS payload,COALESCE(a.archived_at,a.requested_at) AS stamp,a.id
 FROM filtered a ORDER BY stamp DESC,a.id DESC LIMIT $4 OFFSET $5),
sized AS (SELECT *,SUM(octet_length(payload)) OVER() AS bytes FROM page)
SELECT total.n,CASE WHEN bytes<=1048576 AND octet_length(payload)<=65536 THEN payload ELSE NULL END
FROM total LEFT JOIN sized ON true ORDER BY stamp DESC,id DESC"""

def number(v,minimum=0):
    if type(v) is not int or not minimum<=v<2**63:raise ArchiveCatalogUnavailable()
    return v

def text(v,maximum=16384):
    if type(v) is not str or len(v.encode())>maximum or '\x00' in v:raise ArchiveCatalogUnavailable()
    return v

def counters(v):
    if type(v) is not dict or len(v)>1000:raise ArchiveCatalogUnavailable()
    for k,n in v.items():
        text(k,256)
        if '/' in k or '\\' in k:raise ArchiveCatalogUnavailable()
        number(n)
    return v

def _object(pairs):
    out={}
    for key,value in pairs:
        if key in out:raise ValueError()
        out[key]=value
    return out

def decode(raw):
    if type(raw) is not str or len(raw.encode())>65536:raise ArchiveCatalogUnavailable()
    try:value=json.loads(raw,object_pairs_hook=_object,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
    except (ValueError,TypeError,RecursionError):raise ArchiveCatalogUnavailable() from None
    expected=set(_FIELDS)|{'category_id','category_name','aggregate_stats'}
    if type(value) is not dict or set(value)!=expected:raise ArchiveCatalogUnavailable()
    for k in ('id','task_id','format_version'):number(value[k],1)
    for k in ('progress','original_size','compressed_size','remaining_timeout_seconds'):number(value[k])
    if value['progress']>100 or value['state'] not in STATES or value['phase'] not in PHASES:raise ArchiveCatalogUnavailable()
    for k in ('task_name','task_description','task_goal','category_name'):text(value[k])
    text(value['original_status'],64)
    if type(value['sha256'])is not str or (value['sha256'] and not re.fullmatch('[0-9a-fA-F]{64}',value['sha256'])):raise ArchiveCatalogUnavailable()
    if value['category_id'] is not None:number(value['category_id'],1)
    if type(value['source_task_ids'])is not list or len(value['source_task_ids'])>1000:raise ArchiveCatalogUnavailable()
    for identifier in value['source_task_ids']:number(identifier,1)
    counters(value['data_counts'])
    stats=value['aggregate_stats']
    if type(stats)is not dict or set(stats)-{'tokens','skills','tools','findings'}:raise ArchiveCatalogUnavailable()
    for key,item in stats.items():
        counters(item)
        if key=='tokens' and set(item)-{'calls','input_tokens','output_tokens','cache_read_tokens','cache_write_tokens'}:raise ArchiveCatalogUnavailable()
    for key in ('archived_at','requested_at','created_at','updated_at'):
        v=value[key]
        if key=='archived_at' and v is None:continue
        try:
            parsed=datetime.fromisoformat(text(v,64).replace('Z','+00:00'))
            if parsed.tzinfo is None or parsed.utcoffset() is None:raise ValueError()
        except (ValueError,TypeError,RecursionError):raise ArchiveCatalogUnavailable() from None
    for k in ('category_id','category_name','sha256','archived_at'):
        if value[k] in (None,''):value.pop(k)
    return value

class ArchiveCatalogReader:
    def __init__(self,executor):self.executor=executor;self._executor=executor
    def read(self,*,search='',state='',page=1,size=20,identifier=None):
        text(search,512);text(state,64)
        if state and state not in STATES:raise ValueError('Unknown archive state')
        if type(page)is not int or type(size)is not int:raise ValueError('Integer pagination required')
        page=max(1,page);size=size if 1<=size<=100 else 20
        if page>100000:raise ValueError('Page bound exceeded')
        if identifier is not None:number(identifier,1)
        if self.executor is not self._executor:raise ArchiveCatalogUnavailable()
        rows=self._executor.fetch_all(Query(_SQL,(search.strip(),state,identifier,1 if identifier else size,0 if identifier else (page-1)*size)))
        if self.executor is not self._executor or type(rows)not in (list,tuple) or not 1<=len(rows)<=(1 if identifier else size):raise ArchiveCatalogUnavailable()
        total=None;items=[];bytes_=0
        for row in rows:
            if len(row)!=2:raise ArchiveCatalogUnavailable()
            n=number(row[0])
            if total is not None and total!=n:raise ArchiveCatalogUnavailable()
            total=n
            if row[1] is None:
                if len(rows)!=1 or total>(0 if identifier else (page-1)*size):raise ArchiveCatalogUnavailable()
                continue
            item=decode(row[1]);bytes_+=len(row[1].encode())
            if identifier is not None and item['id']!=identifier:raise ArchiveCatalogUnavailable()
            items.append(item)
        expected=min(1 if identifier else size,max(0,total-(0 if identifier else (page-1)*size)))
        if len(items)!=expected or bytes_>1048576 or len({v['id'] for v in items})!=len(items):raise ArchiveCatalogUnavailable()
        return (items[0] if items else None) if identifier else dict(items=items,total=total,page=page,size=size)
