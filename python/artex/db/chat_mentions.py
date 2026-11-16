"""Global catalog search only; no mention expansion, task fallback or execution."""
from dataclasses import dataclass
import base64,json,re
from .core import Query
from .judge_config_read import SPACE

KINDS=frozenset(('finding','company','asset','endpoint','ip','app','root_domain','subdomain','service'))
class MentionUnavailable(RuntimeError):pass
class MentionInvalid(ValueError):pass
@dataclass(frozen=True)
class CatalogScope:
    name:str
    revision:str
@dataclass(frozen=True)
class MentionSelection:
    kind:str=''
    query:str=''
    cursor:str=''
    def validate(self):
        if type(self.kind) is not str or self.kind and self.kind not in KINDS or type(self.query) is not str or len(self.query)>200 or '\0' in self.query or self.query!=self.query.strip(SPACE) or type(self.cursor) is not str or len(self.cursor)>2048:raise MentionInvalid()
        self.query.encode('utf-8');decode_cursor(self)

def decode_cursor(s):
    if not s.cursor:return 0,'',False
    try:
        if not re.fullmatch(r'[A-Za-z0-9_-]+',s.cursor):raise ValueError()
        raw=base64.b64decode(s.cursor+'='*((-len(s.cursor))%4),altchars=b'-_',validate=True)
        def pairs(items):
            out={}
            for k,v in items:
                if k in out:raise ValueError()
                out[k]=v
            return out
        c=json.loads(raw,object_pairs_hook=pairs)
        if type(c) is not dict or set(c)!= {'id','kind','exact','scope','query'} or type(c['id']) is not int or not 0<c['id']<2**63 or type(c['kind']) is not str or c['kind'] not in KINDS or type(c['exact']) is not bool or c['scope']!=s.kind or c['query']!=s.query:raise ValueError()
        return c['id'],c['kind'],c['exact']
    except Exception:raise MentionInvalid('Invalid search cursor') from None

def cursor(item,s):
    value=dict(id=item['id'],kind=item['kind'],exact=str(item['id'])==s.query,scope=s.kind,query=s.query)
    return base64.urlsafe_b64encode(json.dumps(value,separators=(',',':'),ensure_ascii=False).encode()).rstrip(b'=').decode()

SQL="""WITH matches AS (
 (SELECT 'finding' AS kind,id,COALESCE(NULLIF(name,''),vulnclass) AS label,
 concat_ws(' · ',severity,status,left(summary,160)) AS description FROM findings
 WHERE ($1='' OR $1='finding') AND ($2='' OR id::text=$2 OR concat_ws(' ',name,vulnclass,summary) ILIKE $3)
 AND ($4::bigint=0 OR (id::text=$2)<$6 OR ((id::text=$2)=$6 AND (id<$4 OR (id=$4 AND 'finding'>$5))))
 ORDER BY (id::text=$2) DESC,id DESC LIMIT 21)
 UNION ALL
 (SELECT 'company',id,name,nkey FROM companies
 WHERE ($1='' OR $1='company') AND ($2='' OR id::text=$2 OR name ILIKE $3 OR nkey ILIKE $3)
 AND ($4::bigint=0 OR (id::text=$2)<$6 OR ((id::text=$2)=$6 AND (id<$4 OR (id=$4 AND 'company'>$5))))
 ORDER BY (id::text=$2) DESC,id DESC LIMIT 21)
 UNION ALL
 (SELECT type,id,CASE WHEN type='endpoint' THEN concat_ws(' ',NULLIF(method,''),url)
 ELSE COALESCE(NULLIF(app_name,''),NULLIF(url,''),NULLIF(domain,''),NULLIF(ip,''),NULLIF(bundle_id,''),'Asset #'||id::text) END,
 concat_ws(' · ',type,NULLIF(page_title,''),NULLIF(service_name,''),NULLIF(bundle_id,''),NULLIF(ip,''),port::text)
 FROM assets WHERE ($1='' OR $1='asset' OR type=$1)
 AND ($2='' OR id::text=$2 OR concat_ws(' ',domain,root_domain,ip,url,app_name,bundle_id,page_title,service_name,method) ILIKE $3)
 AND ($4::bigint=0 OR (id::text=$2)<$6 OR ((id::text=$2)=$6 AND (id<$4 OR (id=$4 AND type>$5))))
 ORDER BY (id::text=$2) DESC,id DESC LIMIT 21)
), page AS (SELECT kind,id,left(label,160) AS label,left(description,240) AS description
 FROM matches ORDER BY (id::text=$2) DESC,id DESC,kind LIMIT 21)
SELECT CASE WHEN octet_length(kind)<=32 THEN kind ELSE NULL END,id,label,description FROM page
ORDER BY (id::text=$2) DESC,id DESC,kind"""

class ChatMentionReader:
    def __init__(self,executor,scope,*,authorize,asset_authorize,current,lifecycle_guard):
        if type(scope) is not CatalogScope or scope.name!='global-catalog' or type(scope.revision) is not str or not 1<=len(scope.revision.encode())<=256 or any(not callable(x) for x in (authorize,asset_authorize,current,lifecycle_guard)) or not callable(getattr(executor,'fetch_all',None)):raise ValueError('Explicit global catalog and asset grants required')
        self.executor=executor;self.scope=scope;self.authorize=authorize;self.asset_authorize=asset_authorize;self.current=current;self.guard=lifecycle_guard
        self._pins=(executor,scope,scope.name,scope.revision,authorize,asset_authorize,current,lifecycle_guard)
    def validate(self):
        e,s,n,r,a,assets,c,g=self._pins
        if self.executor is not e or self.scope is not s or (s.name,s.revision)!=(n,r) or any(x is not y for x,y in zip((self.authorize,self.asset_authorize,self.current,self.guard),(a,assets,c,g))):raise PermissionError()
    def _allow(self,selection,identity,ids,disclosure):
        e,s,n,r,a,assets,c,g=self._pins
        def check():
            self.validate()
            if type(selection) is not MentionSelection or (selection.kind,selection.query,selection.cursor)!=identity:raise PermissionError()
            selection.validate()
        check()
        if c(s) is not True:raise PermissionError()
        check()
        if a(s,selection) is not True:raise PermissionError()
        check()
        if disclosure is not None and disclosure() is not True:raise PermissionError()
        check()
        if assets(s,selection,ids) is not True:raise PermissionError()
        check()
        if a(s,selection) is not True:raise PermissionError()
        check()
        if c(s) is not True:raise PermissionError()
        check()
    def validate_disclosure(self,selection,ids,*,disclosure=None):
        self.validate();selection.validate();identity=(selection.kind,selection.query,selection.cursor)
        if type(ids) is not tuple or any(type(x) is not int or not 0<x<2**63 for x in ids):raise MentionInvalid()
        with self._pins[7]():self._allow(selection,identity,ids,disclosure)
        self._allow(selection,identity,ids,disclosure)
    def read(self,selection,*,disclosure=None):
        if type(selection) is not MentionSelection:raise MentionInvalid()
        selection.validate();identity=(selection.kind,selection.query,selection.cursor);self.validate()
        after,kind,exact=decode_cursor(selection)
        pattern='%'+selection.query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
        ids=()
        with self._pins[7]():
            self._allow(selection,identity,ids,disclosure)
            rows=self._pins[0].fetch_all(Query(SQL,(selection.kind,selection.query,pattern,after,kind,exact)))
            self._allow(selection,identity,ids,disclosure)
            if type(rows) not in (list,tuple) or len(rows)>21:raise MentionUnavailable()
            items=[]
            for row in rows:
                if type(row) not in (list,tuple) or len(row)!=4:raise MentionUnavailable()
                k,i,label,description=row
                if type(k) is not str or k not in KINDS-{'asset'} or type(i) is not int or not 0<i<2**63 or type(label) is not str or type(description) is not str or len(label)>160 or len(description)>240:raise MentionUnavailable()
                label.encode('utf-8');description.encode('utf-8')
                if selection.kind and (selection.kind=='asset' and k in ('company','finding') or selection.kind!='asset' and k!=selection.kind):raise MentionUnavailable()
                items.append(dict(kind=k,id=i,label=label,description=description))
            keys=[(str(x['id'])!=selection.query,-x['id'],x['kind']) for x in items]
            if keys!=sorted(keys) or len(set(keys))!=len(keys):raise MentionUnavailable()
            ids=tuple(x['id'] for x in items if x['kind'] not in ('company','finding'))
            result={'items':items[:20]}
            if len(items)>20:result['next_cursor']=cursor(items[19],selection)
            self._allow(selection,identity,ids,disclosure)
        self._allow(selection,identity,ids,disclosure)
        return result,ids
