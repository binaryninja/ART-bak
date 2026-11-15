"""Opt-in offline traffic reads, source server.go traffic GET contracts.

A fixed Traffic/workspace is the authorization scope, NOT a task selector. Host
policy must explicitly authorize whole-workspace metadata (URLs may be secrets)
or raw exchange headers/bodies. No implicit global/task fallback, archive merge,
proxy listener, capture, deletion or default registration. Blob streaming and
legacy file-backed exchanges return 503 until safe bounded file readers exist.
SQLite reads reuse the actual store. Conservative whole-index byte/row preflight
bounds transfers; oversized indexes fail closed rather than silently truncate.
Only one-process writers honoring Traffic.lock are supported. This is not a
sandbox against another process editing the SQLite file or workspace.
"""
from pathlib import Path
from urllib.parse import parse_qs,urlsplit
from datetime import datetime,timezone
from types import MappingProxyType
from ..traffic import Traffic,PageQuery
from .core import json_response,error_response

class TrafficDenied(RuntimeError):pass
class TrafficUnavailable(RuntimeError):pass

class TrafficReadService:
    def __init__(self,store,*,directory,scope,authorize,current,lifecycle_guard,max_rows=10000,max_bytes=2097152):
        if type(store) is not Traffic or not isinstance(scope,str) or not scope or len(scope)>256:raise ValueError('Actual fixed traffic scope required')
        if any(not callable(f) for f in (authorize,current,lifecycle_guard)):raise ValueError('Explicit read authority required')
        if type(max_rows) is not int or not 1<=max_rows<=100000 or type(max_bytes) is not int or not 1024<=max_bytes<=16777216:raise ValueError('Invalid read bounds')
        root=Path(directory).resolve(strict=True)
        if store.directory.resolve(strict=True)!=root:raise ValueError('Exact traffic directory required')
        self.store=store;self._store=store;self._db=store.db;self._lock=store.lock;self._root=root
        path=root/'_index/index.sqlite';st=path.stat();self._file=(st.st_dev,st.st_ino)
        self.scope=scope;self._scope=scope;self.authorize=authorize;self.current=current;self.guard=lifecycle_guard
        self.max_rows,self.max_bytes=max_rows,max_bytes
    def register(self,router):
        for tail,action in (('','list'),('/hosts','hosts'),('/exchange','exchange'),('/blob','blob')):
            router.add('GET','/api/traffic'+tail,lambda req,params,a=action:self.handle(a,req,params))
    def _check(self):
        s=self.store
        if s is not self._store or s.db is not self._db or s.lock is not self._lock or self.scope!=self._scope:raise TrafficDenied()
        if s.directory.resolve(strict=True)!=self._root:raise TrafficDenied()
        st=(self._root/'_index/index.sqlite').stat()
        if (st.st_dev,st.st_ino)!=self._file:raise TrafficDenied()
        if self.current(self._scope) is not True:raise TrafficDenied()
        if s.db is not self._db or s.lock is not self._lock or self.store is not s or self.scope!=self._scope:raise TrafficDenied()
        if s.directory.resolve(strict=True)!=self._root:raise TrafficDenied()
        final=(self._root/'_index/index.sqlite').stat()
        if (final.st_dev,final.st_ino)!=self._file:raise TrafficDenied()
    def _allow(self,request,action,query):
        self._check()
        if self.authorize(request,action,self._scope,MappingProxyType(dict(query))) is not True:raise TrafficDenied()
        self._check()
    def _bounds(self):
        # SQLite length(TEXT) counts characters, so cast before byte accounting.
        fields='id host method url_template url content_type path'.split()
        size='+'.join('length(CAST(COALESCE('+k+",'') AS BLOB))" for k in fields)
        n,size=self._db.execute('SELECT count(*),COALESCE(sum('+size+'),0) FROM exchanges').fetchone()
        if n>self.max_rows or size>self.max_bytes:raise TrafficUnavailable()
    @staticmethod
    def _number(q,key,default,low,high):
        raw=q.get(key,str(default))
        try:value=int(raw)
        except ValueError:raise ValueError('Invalid number') from None
        if str(value)!=raw or not low<=value<=high:raise ValueError('Invalid number')
        return value
    def handle(self,action,request,params=None):
        try:
            if request.body or len(request.target)>16384:raise ValueError('Invalid read request')
            parsed=parse_qs(urlsplit(request.target).query,keep_blank_values=True)
            if any(len(v)!=1 or len(v[0].encode('utf-8'))>4096 for v in parsed.values()):raise ValueError('Invalid selector')
            q={k:v[0] for k,v in parsed.items()}
            allowed={'list':set('host method q body path status resp_min resp_max sort order page size'.split()),'hosts':set(),'exchange':{'id'},'blob':{'hash'}}
            if action not in allowed or set(q)-allowed[action]:raise ValueError('Unsupported selector')
            with self.guard(self._scope):
                self._allow(request,action,q)
                if action=='blob':raise TrafficUnavailable()
                with self._lock:
                    self._check()
                    # One snapshot with authorized in-process writers serialized.
                    self._db.execute('SAVEPOINT traffic_read')
                    try:
                        self._bounds()
                        if action=='list':
                            page=self._number(q,'page',0,0,1000000);size=self._number(q,'size',100,1,500)
                            minimum=self._number(q,'resp_min',-1,-1,2**63-1);maximum=self._number(q,'resp_max',-1,-1,2**63-1)
                            if q.get('body','').strip() and (not self.store.fts or len(q['body'].strip())<3):raise TrafficUnavailable()
                            filters=PageQuery(q.get('host',''),q.get('method',''),q.get('q',''),q.get('body',''),q.get('path',''),q.get('status',''),minimum,maximum,q.get('sort',''),q.get('order',''))
                            rows,total=self.store.page(filters,page,size)
                            out=[]
                            for row in rows:
                                item={k:row[k] for k in ('id','host','method','url','status','content_type','resp_len')}
                                item['ts']=datetime.fromtimestamp(row['ts'],timezone.utc).isoformat().replace('+00:00','Z');out.append(item)
                            result=dict(enabled=False,proxy='',count=self.store.count(),total=total,page=page,size=size,exchanges=out)
                        elif action=='hosts':result={'hosts':self.store.hosts()}
                        else:
                            ident=q.get('id','')
                            if not ident:raise ValueError('Missing id')
                            size=self._db.execute("""SELECT COALESCE(length(CAST(req_head AS BLOB)),0)+COALESCE(length(CAST(req_body AS BLOB)),0)+
COALESCE(length(CAST(resp_head AS BLOB)),0)+COALESCE(length(CAST(resp_body AS BLOB)),0)+COALESCE(length(CAST(req_blob AS BLOB)),0)+COALESCE(length(CAST(resp_blob AS BLOB)),0)
FROM exchange_bodies WHERE id=?""",(ident,)).fetchone()
                            if size is None:
                                if self._db.execute('SELECT 1 FROM exchanges WHERE id=?',(ident,)).fetchone():raise TrafficUnavailable()
                                raise KeyError()
                            if size[0]>self.max_bytes:raise TrafficUnavailable()
                            req,resp=self.store.get(ident);result={'req':req,'resp':resp}
                        self._allow(request,action,q)
                        response=json_response(200,result)
                        if len(response.body)>self.max_bytes:raise TrafficUnavailable()
                        self._db.execute('RELEASE traffic_read')
                    except BaseException:
                        self._db.execute('ROLLBACK TO traffic_read');self._db.execute('RELEASE traffic_read');raise
                self._check();return response
        except TrafficDenied:return error_response(403,'Traffic read not authorized')
        except TrafficUnavailable:return error_response(503,'Bounded offline traffic read unavailable')
        except (ValueError,UnicodeError):return error_response(400,'Invalid traffic selector')
        except KeyError:return error_response(404,'Traffic exchange not found')
        except Exception:return error_response(503,'Traffic read unavailable')
