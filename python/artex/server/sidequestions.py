"""Authenticated side-question history/events and explicit lifecycle adapters.

Eleven upstream routes: three parent families times GET/POST/DELETE, plus events
and cancel. Parent resolver(kind,id,intent_id) returns an authorized store after
checking live conversation/task/worker ownership and deletion/archive barriers.
Request resolver(request_id) must authorize its parent and return that SAME scoped
store; no global request lookup or inherited worker selection is guessed.

POST requires explicit coordinator, bounded dispatcher and preparation adapters.
Original questions remain durable/idempotency keys; prepared mention context is
sent only to the model. Dispatcher rejection must durably abort the unstarted
handle before raising; uncertain persistence returns500, never202. No identity
preparer is guessed: callers must reject unsupported mentions rather than drop
their context. Never admit without an executor owning final persistence. Optional cancel
adapter(store,exchange) requests cancellation of that exact generation; True means
accepted, NOT drained. Optional clear adapter(store) must serialize cancel+clear
with coordinator admissions and increment durable generation. Missing adapters503.

Events poll only through injected wait(seconds)->bool (False cancels) and a finite
poll budget. A running stream needs this adapter; terminal snapshots need none.
EOF from budget/cancellation/error is NOT completion; only a terminal snapshot or
cleared event conveys terminal state. No threads, sleeps, providers or tools here.
Stored errors are redacted; snapshots never expose captured model request prompts.
"""
import json,re
from urllib.parse import parse_qs,urlsplit
from .core import Router,json_response,error_response
from .streaming import StreamingResponse
from ..db.sidequestions import Parent,Exchange,SideInvalid,SideBusy,SideParentGone,SideSessionMissing
from ..sidequestion.runtime import SideUnavailable,SideCapacity,SidePersistenceError

class SideHTTPUnavailable(RuntimeError):pass


def _id(raw):
    if not isinstance(raw,str) or not re.fullmatch('[0-9]{1,19}',raw) or not 0<int(raw)<2**63:raise SideInvalid('Invalid ID')
    return int(raw)


def _dto(exchange):
    if not isinstance(exchange,Exchange):raise SideHTTPUnavailable('Invalid exchange')
    value=exchange.to_dict()
    if value.get('error'):value['error']='Side question failed; details are unavailable'
    return value


class SideQuestionHTTPService:
    def __init__(self,parent_resolver=None,*,request_resolver=None,model_available=None,
                 cancel_request=None,clear_history=None,wait=None,max_polls=480,
                 coordinator=None,dispatcher=None,prepare_question=None):
        if type(max_polls) is not int or not 1<=max_polls<=10000:raise ValueError('Invalid poll budget')
        self.parent_resolver=parent_resolver;self.request_resolver=request_resolver
        self.model_available=model_available;self.cancel_request=cancel_request;self.clear_history=clear_history
        self.wait=wait;self.max_polls=max_polls
        self.coordinator=coordinator;self.dispatcher=dispatcher;self.prepare_question=prepare_question
    def register(self,router:Router):
        for path,kind in (('/api/conversations/{id}','conversation'),('/api/tasks/{id}/chat','main'),('/api/tasks/{id}/intents/{iid}','worker')):
            for method in ('GET','POST','DELETE'):
                router.add(method,path+'/side-questions',lambda request,params,kind=kind:self._handle(kind,request,params))
        router.add('GET','/api/side-questions/{requestID}/events',lambda request,params:self._handle('events',request,params))
        router.add('POST','/api/side-questions/{requestID}/cancel',lambda request,params:self._handle('cancel',request,params))
    def _parent(self,kind,params):
        if not callable(self.parent_resolver):raise SideHTTPUnavailable()
        key=_id(params['id']);intent=_id(params['iid']) if kind=='worker' else 0
        store=self.parent_resolver(kind,key,intent)
        if store is None:raise SideParentGone()
        parent=store.parent
        if not isinstance(parent,Parent) or store.key!=parent.key():raise SideHTTPUnavailable()
        if kind=='conversation':valid=parent.conversation_id==key
        else:valid=parent.task_id==key and parent.intent_id==intent and parent.exploration_id>0
        if not valid:raise SideHTTPUnavailable('Parent scope mismatch')
        return store
    @staticmethod
    def _exchange(store,exchange):
        if exchange is not None and (not isinstance(exchange,Exchange) or exchange.session_key!=store.key):raise SideHTTPUnavailable('Exchange scope mismatch')
        return exchange
    def _handle(self,kind,request,params):
        try:
            q=parse_qs(urlsplit(request.target).query,keep_blank_values=True,max_num_fields=2)
            # Reserved EventSource authentication parameter, verified by Router first.
            allowed={'token'} | ({'before'} if request.method=='GET' and kind not in ('events','cancel') else set())
            if any(len(values)!=1 for values in q.values()) or set(q)-allowed:raise SideInvalid()
            if len(q.get('token',[''])[0])>16384:raise SideInvalid()
            if kind in ('events','cancel'):
                key=params['requestID']
                if not re.fullmatch('[A-Za-z0-9_.:-]{1,128}',key):raise SideInvalid()
                if not callable(self.request_resolver):raise SideHTTPUnavailable()
                store=self.request_resolver(key)
                if store is None:raise SideParentGone()
                if not isinstance(store.parent,Parent) or store.key!=store.parent.key():raise SideHTTPUnavailable()
                exchange=self._exchange(store,store.request(key))
                if exchange is None:raise SideSessionMissing()
                if exchange.id!=key:raise SideHTTPUnavailable()
                if kind=='cancel':
                    if not callable(self.cancel_request):raise SideHTTPUnavailable()
                    if self.cancel_request(store,exchange) is not True:raise SideHTTPUnavailable()
                    return json_response(200,{'cancelled':True})
                if exchange.status=='running' and not callable(self.wait):raise SideHTTPUnavailable()
                return StreamingResponse(200,self._events(store,exchange),headers={'Content-Type':'text/event-stream','Cache-Control':'no-cache','X-Accel-Buffering':'no'},max_chunk_bytes=1048576)
            store=self._parent(kind,params)
            if request.method=='POST':return self._submit(store,request)
            if request.method=='DELETE':
                if not callable(self.clear_history) or self.clear_history(store) is not True:raise SideHTTPUnavailable()
                return json_response(200,{'cleared':True})
            before=0
            if 'before' in q:
                raw=q['before'][0]
                before=0 if raw=='0' else _id(raw)
            items=store.history(before,21)
            for exchange in items:self._exchange(store,exchange)
            next_cursor=items[19].ordinal if len(items)>20 else 0
            current=self._exchange(store,store.current())
            snapshot=store.snapshot();meta=None
            if snapshot is not None:
                if snapshot.parent!=store.parent:raise SideHTTPUnavailable()
                if not callable(self.model_available):raise SideHTTPUnavailable()
                available=self.model_available(snapshot.model)
                if type(available) is not bool:raise SideHTTPUnavailable()
                meta={'captured_at':snapshot.captured_at.isoformat(),'model':snapshot.model.to_dict(),
                      'available':available,'reason':'' if available else 'Model configuration unavailable or changed'}
            return json_response(200,{'items':[_dto(e) for e in items[:20]],'current':_dto(current) if current else None,'next_cursor':next_cursor,'snapshot':meta})
        except (SideInvalid,ValueError,UnicodeError):return error_response(400,'Invalid side-question request')
        except (SideParentGone,SideSessionMissing):return error_response(404,'Side question or parent not found')
        except SideCapacity:return error_response(429,'Side-question capacity reached')
        except SidePersistenceError:return error_response(500,'Side-question persistence unconfirmed')
        except SideUnavailable:return error_response(503,'Side-question runtime unavailable')
        except SideBusy:return error_response(409,'Side question busy')
        except SideHTTPUnavailable:return error_response(503,'Side-question capability unavailable')
        except Exception:return error_response(500,'Side-question operation failed')
    def _events(self,store,first):
        sequence=-1;generation=first.generation;current=first
        for index in range(self.max_polls):
            if current is None or current.generation!=generation:
                yield b'event: cleared\ndata: {}\n\n';return
            self._exchange(store,current)
            if current.id!=first.id:raise SideHTTPUnavailable()
            if current.sequence>sequence:
                payload=json.dumps(_dto(current),ensure_ascii=True,allow_nan=False,separators=(',',':'))
                yield f'id: {current.sequence}\nevent: snapshot\ndata: {payload}\n\n'.encode()
                sequence=current.sequence
            if current.status!='running':return
            if self.wait(.25) is not True:return
            if index and index%60==0:yield b': keepalive\n\n'
            refreshed=self.request_resolver(first.id)
            if refreshed is None:
                current=None
            else:
                if refreshed.parent!=store.parent or refreshed.key!=store.key:
                    raise SideHTTPUnavailable('Request authorization scope changed')
                current=refreshed.request(first.id)

    def _submit(self,store,request):
        if len(request.body)>32<<10:return error_response(413,'Request body too large')
        def pairs(values):
            result={}
            for key,value in values:
                if key in result:raise SideInvalid('Duplicate JSON key')
                result[key]=value
            return result
        data=json.loads(request.body,object_pairs_hook=pairs)
        if not isinstance(data,dict) or set(data)!={'question','client_request_id'}:raise SideInvalid()
        question=data['question'];client=data['client_request_id']
        if not isinstance(question,str) or not isinstance(client,str):raise SideInvalid()
        question=question.strip()
        if not question or len(question)>4000 or '\x00' in question or not re.fullmatch('[A-Za-z0-9_.:-]{1,128}',client):raise SideInvalid()
        question.encode('utf-8')
        existing=self._exchange(store,store.existing(client))
        if existing is not None:
            if existing.question!=question:return error_response(409,'Request ID belongs to another question')
            return json_response(200,_dto(existing))
        if (not callable(getattr(self.coordinator,'admit',None))
                or not callable(getattr(self.dispatcher,'dispatch',None)) or not callable(self.prepare_question)
                or getattr(self.dispatcher,'coordinator',None) is not self.coordinator):
            raise SideHTTPUnavailable('Explicit preparation, coordinator and bounded dispatcher required')
        prepared=self.prepare_question(store,question)
        if not isinstance(prepared,str) or not prepared or '\x00' in prepared or len(prepared.encode('utf-8'))>1048576:
            raise SideInvalid('Invalid prepared question')
        try:
            admission=self.coordinator.admit(store,client,question,agent_question=prepared)
        except SideUnavailable:
            return error_response(409,'Main context or model configuration unavailable')
        self._exchange(store,admission.exchange)
        if not admission.created:return json_response(200,_dto(admission.exchange))
        # dispatch() owns abort_unstarted on rejection. Never retry or synthesize
        # a terminal DB update here: submission may have crossed its queue fence.
        accepted=self.dispatcher.dispatch(admission)
        if accepted is not True:raise SideHTTPUnavailable('Dispatcher did not acknowledge ownership')
        return json_response(202,_dto(admission.exchange))
