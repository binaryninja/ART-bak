"""Explicit fixed-task activity HTTP/SSE reads, no default registration.

Source paths/seq DTO/session cursors and Last-Event-ID reconnect semantics.
SSE reads committed SQL pages, never a lossy event channel or invented progress.
Each connection is finitely bounded; EOF is reconnect, NOT task completion. A
host may inject bounded wait(seconds)->bool for idle polling (False stops);
otherwise this is backlog replay then close. No threads or unbounded sleep.
Inherited sessions/archive reads remain unsupported, not silently broadened.
Gateway must close the iterable on disconnect; no SQL/guard spans a yield.
"""
from dataclasses import replace
from urllib.parse import urlsplit,parse_qs
import json,re
from ..db.task_activity_read import TaskActivityReadStore,ActivitySelection,ActivityDenied,ActivityMissing,ActivityUnavailable,session_filter
from .core import error_response,json_response
from .streaming import StreamingResponse,sse_frame

class TaskActivityService:
    def __init__(self,store,*,authorize,stream_current,stream_wait=None,max_batches=20,max_polls=3):
        if type(store) is not TaskActivityReadStore or not callable(authorize) or not callable(stream_current) or (stream_wait is not None and not callable(stream_wait)):raise ValueError('Explicit scoped read store and request/stream policies required')
        if type(max_batches) is not int or not 1<=max_batches<=100 or type(max_polls) is not int or not 1<=max_polls<=100:raise ValueError('Finite stream bounds required')
        self.store,self.authorize,self.stream_current,self.wait=store,authorize,stream_current,stream_wait
        self.max_batches,self.max_polls=max_batches,max_polls
        self._fixed=(store,store.executor,store.scope,store.guard,authorize,stream_current,stream_wait,max_batches,max_polls)
    def register(self,router):
        router.add('GET','/api/exploration/activity',lambda r,p:self.handle('list',r,p))
        router.add('GET','/api/exploration/activity/history',lambda r,p:self.handle('history',r,p))
        router.add('GET','/api/exploration/activity/stream',lambda r,p:self.handle('stream',r,p))
        router.add('GET','/api/exploration/activity/{seq}',lambda r,p:self.handle('detail',r,p))
    def check(self,request,selection,pins,stream=False):
        s,e,scope,g,a,c,w,b,p=self._fixed
        if self.store is not s or s.executor is not e or s.scope is not scope or s.guard is not g or self.authorize is not a or self.stream_current is not c or self.wait is not w or (self.max_batches,self.max_polls)!=(b,p):raise ActivityUnavailable('Activity service binding changed')
        s.check(pins)
        if a(request,scope,selection) is not True:raise ActivityDenied('Activity request denied')
        s.validate_read(selection,pins)
        if stream and c(request,scope) is not True:raise ActivityDenied('Activity stream ended')
        # Do not accept a replacement policy installed by the last callback.
        if self.authorize is not a or self.stream_current is not c or self.wait is not w or self.store is not s or s.executor is not e or s.scope is not scope or s.guard is not g or (self.max_batches,self.max_polls)!=(b,p):raise ActivityUnavailable('Activity service policy changed')
        s.check(pins)
    @staticmethod
    def number(raw):
        if not isinstance(raw,str) or not re.fullmatch('[0-9]{1,19}',raw) or int(raw)>=2**63:raise ValueError('Invalid cursor')
        return int(raw)
    def parse(self,action,request,params):
        q=parse_qs(urlsplit(request.target).query,keep_blank_values=True,max_num_fields=10)
        allowed={'task','token'}|({'since','limit','intent'} if action=='list' else {'session','before','limit'} if action=='history' else {'since','intent'} if action=='stream' else set())
        if set(q)-allowed or any(len(v)!=1 for v in q.values()) or len(q.get('token',[''])[0])>16384:raise ValueError('Invalid query')
        if self.number(q.get('task',[''])[0])!=self.store.scope.task_id:raise ActivityDenied('Exact task required')
        limit=self.number(q.get('limit',['200' if action=='history' else str(self.store.max_rows)])[0])
        if not 1<=limit<=self.store.max_rows:raise ValueError('Invalid limit')
        since=self.number(q.get('since',['0'])[0]);before=self.number(q.get('before',['0'])[0])
        if action=='stream' and request.header('Last-Event-ID'):since=self.number(request.header('Last-Event-ID'))
        intent=self.number(q['intent'][0]) if 'intent' in q else None
        if intent==0:raise ValueError('Invalid intent')
        session=q.get('session',[''])[0]
        if action=='history':session_filter(session)
        seq=self.number(params['seq']) if action=='detail' else 0
        if action=='detail' and seq==0:raise ValueError('Invalid sequence')
        return ActivitySelection('list' if action=='stream' else action,since,before,limit,intent,session,seq)
    def handle(self,action,request,params):
        try:
            selection=self.parse(action,request,params);pins=self.store.pins()
            with self._fixed[3](self._fixed[2]):self.check(request,selection,pins,action=='stream')
            if action=='stream':
                stream=_ActivityStream(self,request,selection,pins)
                return StreamingResponse(200,stream,headers={'Content-Type':'text/event-stream','Cache-Control':'no-cache','X-Accel-Buffering':'no'},on_close=stream.close,max_chunk_bytes=1048576)
            result=self.store.read(selection)
            response=json_response(200,result)
            if len(response.body)>self.store.max_bytes*6+65536:raise ActivityUnavailable('Response exceeds bound')
            with self._fixed[3](self._fixed[2]):self.check(request,selection,pins)
            return response
        except ValueError:return error_response(400,'Invalid activity request')
        except ActivityDenied:return error_response(403,'Activity read denied')
        except ActivityMissing:return error_response(404,'Local activity scope not found')
        except Exception:return error_response(503,'Activity read unavailable')

class _ActivityStream:
    def __init__(self,service,request,selection,pins):
        self.service,self.request,self.selection,self.pins=service,request,selection,pins
        self.cursor=selection.since;self.batch_selection=None;self.pending=[];self.batches=0;self.polls=0;self.closed=False
    def __iter__(self):return self
    def checked(self):
        with self.service._fixed[3](self.service._fixed[2]):
            self.service.check(self.request,self.selection,self.pins,True)
            if self.batch_selection is not None and self.batch_selection!=self.selection:
                self.service.check(self.request,self.batch_selection,self.pins,True)
    def __next__(self):
        if self.closed:raise StopIteration
        try:
            self.checked()
            while not self.pending:
                if self.batches>=self.service.max_batches:self.close();raise StopIteration
                self.batch_selection=replace(self.selection,since=self.cursor)
                self.checked()
                result=self.service.store.read(self.batch_selection)
                self.batches+=1;self.checked()
                self.pending=result['items']
                if not self.pending:
                    if self.service.wait is None or self.polls>=self.service.max_polls:self.close();raise StopIteration
                    self.polls+=1
                    self.checked()
                    if self.service.wait(1.0) is not True:self.close();raise StopIteration
                    self.checked()
                    return b': ping\n\n'
            item=self.pending.pop(0)
            if type(item.get('seq')) is not int or item['seq']<=self.cursor:raise ActivityUnavailable('Nonmonotonic activity cursor')
            frame=sse_frame(json.dumps(item,ensure_ascii=False,allow_nan=False,separators=(',',':')),id=str(item['seq']),max_bytes=1048576)
            self.checked()
            self.cursor=item['seq']
            return frame
        except BaseException:
            self.close();raise
    def close(self):
        if self.closed:return
        self.closed=True;self.pending.clear();self.batch_selection=None
