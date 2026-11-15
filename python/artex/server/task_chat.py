"""Source POST /api/chat + task-main status/stop, no autonomous task resume.

Explicit resolve_task(optional_id) chooses a live task or current selection;
authorize(request,task_id) is required independently of model availability.
Dispatcher.prepare resolves the newest segment when seg is omitted/negative,
as Go does. Scope is frozen before admission, not silently changed while queued.
Task activities/SSE remain the host's task sink/stream responsibility; no invented
conversation route or private filesystem path is exposed. Unsupported mentions
and attachments reject before persistence. No rule-mode simulated success.
"""
import json,re,uuid
from urllib.parse import urlsplit,parse_qs
from .core import json_response,error_response
from ..chat_dispatch import DispatchConflict,DispatchUnavailable,DispatchPersistenceError
from ..agent.chat import ChatBusy,ChatUnavailable
from ..agent.runtime import RunStopped
from ..transcripts import TranscriptConflict


def _identifier(value):
    if type(value) is int:value=str(value)
    if not isinstance(value,str) or not re.fullmatch(r'[1-9][0-9]{0,18}',value) or int(value)>=2**63:
        raise ValueError('Invalid task identifier')
    return int(value)


def _object(pairs):
    result={}
    for k,v in pairs:
        if k in result:raise ValueError('Duplicate field')
        result[k]=v
    return result


class TaskChatService:
    def __init__(self,*,resolve_task,authorize,dispatcher,max_body_bytes=1048576):
        if not callable(resolve_task) or not callable(authorize) or not all(callable(getattr(dispatcher,n,None)) for n in ('prepare','submit','running','cancel')):
            raise ValueError('Explicit task resolution, authorization and bounded dispatcher required')
        if type(max_body_bytes) is not int or not 1<=max_body_bytes<=1048576:raise ValueError('Invalid body bound')
        self.resolve_task,self.authorize,self.dispatcher,self.maximum=resolve_task,authorize,dispatcher,max_body_bytes
    def register(self,router):
        router.add('POST','/api/chat',lambda request,params:self.handle(request))
        router.add('GET','/api/tasks/{id}/chat/status',lambda request,params:self.handle(request,params['id'],'status'))
        router.add('POST','/api/tasks/{id}/chat/stop',lambda request,params:self.handle(request,params['id'],'stop'))
    def handle(self,request,path_id=None,action='send'):
        try:
            query=parse_qs(urlsplit(request.target).query,keep_blank_values=True)
            if action=='send':
                if set(query)-{'task'} or any(len(v)!=1 for v in query.values()):raise ValueError()
                task=query.get('task',[''])[0]
                requested=_identifier(task) if task else None
            else:
                if query:raise ValueError()
                requested=_identifier(path_id)
            resolved=self.resolve_task(requested)
            if resolved is None:return error_response(404,'No active task' if requested is None else 'Task not found')
            task_id=_identifier(resolved)
            if requested is not None and task_id!=requested:raise ChatUnavailable('Task scope mismatch')
            if self.authorize(request,task_id) is not True:return error_response(403,'Task access denied')
            if action=='status':return json_response(200,{'running':self.dispatcher.running(task_id)})
            if action=='stop':return json_response(200,{'status':'stopping' if self.dispatcher.cancel(task_id) else 'idle'})
            if len(request.body)>self.maximum:return error_response(413,'Request body too large')
            data=json.loads(request.body.decode('utf-8'),object_pairs_hook=_object,
                            parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
            if not isinstance(data,dict) or set(data)-{'message','attachments','seg'}:raise ValueError()
            message=data.get('message')
            if not isinstance(message,str) or not message.strip() or '\x00' in message:raise ValueError()
            if '@[' in message:return error_response(503,'Mention preparation unavailable')
            if data.get('attachments',[])!=[]:return error_response(503,'Attachment preparation unavailable')
            segment=data.get('seg')
            if segment is not None and (type(segment) is not int or not -(2**31)<=segment<2**31):raise ValueError()
            if segment is not None and segment<0:segment=None
            client=request.header('Idempotency-Key') or uuid.uuid4().hex
            if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',client):raise ValueError()
            scope=self.dispatcher.prepare(task_id,segment=segment)
            self.dispatcher.submit(scope,client,message)
            return json_response(202,{'status':'accepted','mode':'llm'})
        except (ChatBusy,DispatchConflict,TranscriptConflict):return error_response(409,'Task chat running or requires reconciliation')
        except DispatchPersistenceError:return json_response(503,{'error':'Task chat persistence uncertain; reconcile before retry','persisted':False})
        except (ChatUnavailable,DispatchUnavailable,RunStopped):return error_response(503,'Task chat runtime unavailable or changed')
        except (ValueError,UnicodeError,TypeError):return error_response(400,'Invalid task chat request')
        except Exception:return error_response(500,'Task chat operation failed')
