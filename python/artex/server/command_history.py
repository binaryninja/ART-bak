"""Opt-in source command history GETs. Never executes command/input strings.

The legacy ?task selector denotes EXPLORATION id. A fixed scoped service rejects
missing/malformed/foreign selectors rather than source's implicit global fallback.
Global service accepts no task selector: host must supply a separately scoped
service to narrow authority. Raw list bodies require the store's independent raw
grant; stats still require metadata/search grants. No default routes or capture.
"""
from datetime import datetime
from urllib.parse import parse_qsl,urlsplit
import re
from ..db.command_history import CommandHistoryStore,CommandDenied,CommandUnavailable
from .core import json_response,error_response
class CommandHistoryService:
    def __init__(self,store,*,authorize,max_response_bytes=2097152):
        if type(store) is not CommandHistoryStore or not callable(authorize):raise ValueError('Actual history store and request policy required')
        if type(max_response_bytes) is not int or not 1024<=max_response_bytes<=16777216:raise ValueError('Invalid response bound')
        self.store=store;self._store=store;self._executor=store.executor;self._scope=store.scope;self._guard=store.guard
        self.authorize=authorize;self.max_bytes=max_response_bytes
    def _check(self):
        s=self.store
        if s is not self._store or s.executor is not self._executor or s._executor is not self._executor or s.scope is not self._scope or s._scope is not self._scope or s.guard is not self._guard:raise CommandDenied()
        s._check()
        if self.store is not s or s.executor is not self._executor or s.scope is not self._scope or s.guard is not self._guard:raise CommandDenied()
    def register(self,router):
        router.add('GET','/api/commands',lambda req,params:self.handle('list',req))
        router.add('GET','/api/commands/stats',lambda req,params:self.handle('stats',req))
    def handle(self,op,request):
        try:
            if request.body or op not in ('list','stats'):raise ValueError()
            query=urlsplit(request.target).query
            if len(query)>8192:raise ValueError()
            pairs=parse_qsl(query,keep_blank_values=True,max_num_fields=4)
            if len({k for k,v in pairs})!=len(pairs):raise ValueError()
            q=dict(pairs)
            if set(q)-({'task','q','page','size'} if op=='list' else {'task','q'}):raise ValueError()
            if self._scope.exploration_id is not None:
                if not re.fullmatch('[1-9][0-9]{0,18}',q.get('task','')):raise ValueError()
                if int(q['task'])!=self._scope.exploration_id:raise CommandDenied()
            elif 'task' in q:raise CommandDenied()
            page,size=0,50
            if op=='list':
                for key in ('page','size'):
                    if key in q and not re.fullmatch('-?[0-9]{1,7}',q[key]):raise ValueError()
                page=int(q.get('page','0'));size=int(q.get('size','50'))
            keyword=q.get('q','')
            with self._guard(self._scope):
                self._check()
                if self.authorize(request,self._scope,op,keyword) is not True:raise CommandDenied()
                self._check()
                if op=='list':
                    rows,total=self.store.list(keyword,page,size)
                    for row in rows:
                        if not isinstance(row['created_at'],datetime) or row['created_at'].tzinfo is None:raise CommandUnavailable()
                        row['created_at']=row['created_at'].isoformat().replace('+00:00','Z')
                    result={'commands':rows,'total':total}
                else:result={'stats':self.store.stats(keyword)}
                self._check();response=json_response(200,result)
                if len(response.body)>self.max_bytes:raise CommandUnavailable()
                return response
        except CommandDenied:return error_response(403,'Command history scope not authorized')
        except (ValueError,UnicodeError):return error_response(400,'Invalid history selector')
        except Exception:return error_response(503,'Command history unavailable')
