"""Opt-in stored pending/detail and explicitly paginated history reads.

No decisions or execution/audit endpoints, no live-waiter claim. Legacy unpaged
history is unavailable rather than silently substituting a truncated page.
Task-scoped mounting requires exact textual task key; global mounting never
infers task authority. All returned inputs/reasons require a separate raw grant.
"""
from datetime import datetime
from urllib.parse import urlsplit,parse_qsl
import re
from ..db.intercept_history import InterceptHistoryStore,InspectionDenied,InspectionUnavailable
from .core import json_response,error_response
class InterceptInspectionService:
    def __init__(self,store,*,authorize,max_response_bytes=2097152):
        if type(store) is not InterceptHistoryStore or not callable(authorize):raise ValueError('Actual store and explicit request grant required')
        if type(max_response_bytes) is not int or not 1024<=max_response_bytes<=16777216:raise ValueError('Invalid response bound')
        self.store=store;self._store=store;self._executor=store.executor;self._scope=store.scope;self._guard=store.guard
        self.authorize=authorize;self.max_bytes=max_response_bytes
    def _check(self):
        s=self.store
        if s is not self._store or s.executor is not self._executor or s.scope is not self._scope or s.guard is not self._guard:raise InspectionDenied()
        s._check()
        if self.store is not s or s.executor is not self._executor or s.scope is not self._scope or s.guard is not self._guard:raise InspectionDenied()
    def register(self,router):
        router.add('GET','/api/intercept/pending',lambda r,p:self.handle('pending',r))
        router.add('GET','/api/intercept/pending/{id}',lambda r,p:self.handle('one',r,p.get('id')))
        router.add('GET','/api/intercept/history',lambda r,p:self.handle('history',r))
    def handle(self,kind,request,id=None):
        policies=(self.authorize,getattr(self.store,'authorize',None),getattr(self.store,'current',None),getattr(self.store,'raw',None),)
        def check():
            if any(a is not b for a,b in zip((self.authorize,getattr(self.store,'authorize',None),getattr(self.store,'current',None),getattr(self.store,'raw',None),),policies)):raise InspectionDenied()
            self._check()
            if any(a is not b for a,b in zip((self.authorize,getattr(self.store,'authorize',None),getattr(self.store,'current',None),getattr(self.store,'raw',None),),policies)):raise InspectionDenied()

        try:
            if request.body or kind not in ('pending','one','history'):raise ValueError()
            query=urlsplit(request.target).query
            if len(query)>2048:raise ValueError()
            pairs=parse_qsl(query,keep_blank_values=True,max_num_fields=4)
            if len({k for k,v in pairs})!=len(pairs):raise ValueError()
            q=dict(pairs)
            if set(q)-({'page','size','status','decision_source'} if kind=='history' else set()):raise ValueError()
            if kind=='history' and not ('page' in q or 'size' in q):raise InspectionUnavailable()
            for k in ('page','size'):
                if k in q and not re.fullmatch('-?[0-9]{1,7}',q[k]):raise ValueError()
            if kind=='one':
                if not isinstance(id,str) or not re.fullmatch('[1-9][0-9]{0,18}',id):raise ValueError()
                id=int(id)
            page=max(1,int(q.get('page','1')));size=int(q.get('size','20'));size=min(100,20 if size<=0 else size)
            with self._guard(self._scope):
                check()
                if self.authorize(request,self._scope,kind,id) is not True:raise InspectionDenied()
                check()
                rows,total=self.store.read(kind,id=id,page=page,size=size,status=q.get('status',''),decision_source=q.get('decision_source',''))
                for row in rows:
                    for k in ('created_at','decided_at'):
                        v=row[k]
                        if v is None and k=='decided_at':continue
                        if not isinstance(v,datetime) or v.tzinfo is None:raise InspectionUnavailable()
                        row[k]=v.isoformat().replace('+00:00','Z')
                if kind=='one':
                    if not rows:return error_response(404,'Request not found')
                    result=rows[0]
                elif kind=='pending':result={'pending':rows}
                else:result={'items':rows,'total':total,'page':page,'page_size':size}
                check()
                if self.authorize(request,self._scope,kind,id) is not True:raise InspectionDenied()
                check()
                selection=(kind,id,1 if kind=='pending' else page,self.store.max_rows+1 if kind=='pending' else 1 if kind=='one' else size,'pending' if kind=='pending' else q.get('status',''),q.get('decision_source',''))
                self.store.validate_read(selection)
                check();response=json_response(200,result)
                if len(response.body)>self.max_bytes:raise InspectionUnavailable()
                return response
        except InspectionDenied:return error_response(403,'Intercept inspection not authorized')
        except (ValueError,UnicodeError):return error_response(400,'Invalid inspection selector')
        except Exception:return error_response(503,'Intercept inspection unavailable')
