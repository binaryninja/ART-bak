"""Source-shaped conversation async admission and activity polling, no invented SSE.

Explicit host-owned dispatcher persists user activity and registers busy/cancel
before 202. ChatAgent runs in its bounded worker; durable transcript
commit precedes terminal result activity. Original text is retained; attachments
and mention preparation are rejected rather than silently dropped. Task /api/chat
is a different main-agent/activity flow registered by TaskChatService only.

No inferred roots, tools, providers, dispatcher or activity storage. A supplied
Idempotency-Key enables process-local receipt deduplication; absent keys get a
new unique request ID, matching ordinary frontend new-message behavior. Durable
turn idempotency belongs to ChatTranscriptAdapter. Dispatcher's single-process
and crash-recovery limitations apply. POST disconnect does not cancel an admitted
run; stop is explicit, and dispatcher.close owns cooperative cancellation/drain.
"""
from dataclasses import dataclass, field, replace
from copy import deepcopy
import json
import re
from urllib.parse import urlsplit, parse_qs
import uuid
from .core import json_response,error_response
from ..db.conversations import Conversation
from ..agent.chat import ChatAgent,ChatBusy,ChatUnavailable,ChatPersistenceError
from ..agent.runtime import Limits,RunResult,RunStopped,BoundedAgent,EffectUncertain
from ..chat_dispatch import DispatchConflict,DispatchUnavailable,DispatchPersistenceError
from ..sidequestion import Model
from ..agent.chat_compaction import ChatCompaction


@dataclass(frozen=True)
class ChatResources:
    transcript: object=field(repr=False)
    model: Model
    resolver: object=field(repr=False)
    gate: object=field(repr=False)
    durable_result: object=field(repr=False)
    tools: tuple=field(default=(),repr=False)
    system: str=field(default='',repr=False)
    capture: object=field(default=None,repr=False)
    limits: Limits=Limits()
    clock: object=field(default=None,repr=False)
    compaction: ChatCompaction|None=None


def _object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('duplicate field')
        result[key]=value
    return result


def activity_dto(item):
    value=item.to_dict()
    value['seq']=value.pop('id');value['ts']=value.pop('created_at')
    for key in ('worker','kind','summary'):value.setdefault(key,'')
    if 'node_id' in value:value['intent_id']=str(value.pop('node_id'))
    if 'source_task_id' in value:value['source_task_id']=str(value['source_task_id'])
    return value


class ChatService:
    def __init__(self,conversation_store,authorized_resolver,*,dispatcher,activities, authorize_conversation, max_body_bytes=1048576):
        if not callable(getattr(conversation_store,'get',None)) or not callable(authorized_resolver):
            raise ValueError('Explicit conversation store and authorized runtime resolver required')
        if type(max_body_bytes) is not int or not 1<=max_body_bytes<=1048576:
            raise ValueError('Invalid body bound')
        self.store,self.resolve,self.maximum=conversation_store,authorized_resolver,max_body_bytes
        if not all(callable(getattr(dispatcher,n,None)) for n in ('submit','cancel','running')) or not callable(activities):
            raise ValueError('Explicit bounded dispatcher and activity store factory required')
        if not callable(authorize_conversation):raise ValueError('Explicit conversation authorization required')
        self.authorize=authorize_conversation
        self.dispatcher,self.activities=dispatcher,activities

    def register(self,router):
        original=router.authorize
        if not callable(original):raise ValueError('Original router authority required')
        from .auth import AuthService
        owner=getattr(original,'__self__',None)
        jwt=(owner.store,owner.codec,owner.key,owner.clock) if type(owner)is AuthService else None
        method=getattr(original,'__func__',None)
        def transport_pins():
            if router.authorize is not original:raise PermissionError('Router authority changed')
            if jwt is not None and (getattr(owner.authorized,'__self__',None)is not owner or getattr(owner.authorized,'__func__',None)is not method or any(a is not b for a,b in zip((owner.store,owner.codec,owner.key,owner.clock),jwt))):raise PermissionError('JWT authority changed')
        def transport(request):
            transport_pins()
            if original(request)is not True:raise PermissionError('Router access denied')
            transport_pins();return True
        router.add('POST','/api/conversations/{id}/messages',lambda request,params:self.handle(request,params['id'],_transport=transport,_transport_pins=transport_pins))
        router.add('POST','/api/conversations/{id}/stop',lambda request,params:self.handle(request,params['id'],stop=True,_transport=transport,_transport_pins=transport_pins))
        router.add('GET','/api/conversations/{id}/messages',lambda request,params:self.read(request,params['id'],_transport=transport,_transport_pins=transport_pins))
        router.add('GET','/api/conversations/{id}/messages/{seq}',lambda request,params:self.read(request,params['id'],params['seq'],_transport=transport,_transport_pins=transport_pins))

    def running(self,identifier):return self.dispatcher.running(identifier)

    def _activities(self,identifier):
        activities=self.activities(identifier)
        if getattr(activities,'conversation_id',None)!=identifier or not all(callable(getattr(activities,n,None)) for n in ('append_many_and_touch','list','page','detail')):
            raise ChatUnavailable('Parent-bound activity store required')
        return activities

    def read(self,request,raw_id,seq=None,*,_transport=None,_transport_pins=None):
        from .conversation_activity_read import read_conversation_activity
        return read_conversation_activity(self,request,raw_id,seq,_transport,_transport_pins)

    def _live(self,identifier,expected=None):
        conversation=self.store.get(identifier)
        if not isinstance(conversation,Conversation) or conversation.id!=identifier:
            raise ChatUnavailable('Conversation unavailable')
        if expected is not None and (conversation.agent_key,conversation.llm_profile_id)!=(expected.agent_key,expected.llm_profile_id):
            raise RunStopped('conversation_changed')
        return conversation

    def handle(self,request,raw_id,*,stop=False,_transport=None,_transport_pins=None):
        try:
            frozen=deepcopy(request);headers=request.headers
            original=(self.store,self.resolve,self.authorize,self.dispatcher,self.activities)
            get=self.store.get;missing=object();executor=getattr(self.store,'executor',missing)
            def pure():
                if _transport_pins is not None:_transport_pins()
                if getattr(self.store,'executor',missing)is not executor:raise PermissionError('Conversation executor changed')
                if request.headers is not headers or request!=frozen or any(a is not b for a,b in zip((self.store,self.resolve,self.authorize,self.dispatcher,self.activities),original)):
                    raise PermissionError('Conversation request changed')
                current=original[0].get
                if getattr(current,'__self__',None)is not getattr(get,'__self__',None) or getattr(current,'__func__',current)is not getattr(get,'__func__',get):raise PermissionError('Conversation selector changed')
            if not isinstance(raw_id,str) or not re.fullmatch(r'[1-9][0-9]{0,18}',raw_id) or int(raw_id)>=2**63:
                return error_response(400,'Invalid conversation identifier')
            identifier=int(raw_id)
            if urlsplit(request.target).query:return error_response(400,'Query parameters unsupported')
            conversation=self.store.get(identifier)
            if conversation is None:return error_response(404,'Conversation not found')
            if not isinstance(conversation,Conversation) or conversation.id!=identifier:raise ChatUnavailable()
            if self.authorize(request,deepcopy(conversation)) is not True:return error_response(403,'Conversation access denied')
            expected=(conversation.id,conversation.agent_key,conversation.llm_profile_id)
            uncertain=[False];admitted=[False]
            def check_authority():
                if uncertain[0]:raise PermissionError('Conversation effect requires reconciliation')
                pure();current=get(identifier)
                if not isinstance(current,Conversation) or (current.id,current.agent_key,current.llm_profile_id)!=expected:
                    raise PermissionError('Conversation selection changed')
                if original[2](deepcopy(frozen),deepcopy(current))is not True:raise PermissionError('Conversation access denied')
                if _transport is not None and _transport(deepcopy(frozen))is not True:raise PermissionError('Router access denied')
                if original[2](deepcopy(frozen),deepcopy(current))is not True:raise PermissionError('Conversation access denied')
                pure();return True
            def admission():
                try:return check_authority()
                except Exception:
                    # After durable queue admission, any observed authority loss
                    # is sticky, including checks in outer streaming fences.
                    if admitted[0]:uncertain[0]=True
                    raise
            admission()
            if stop:
                return json_response(200,{'status':'stopping' if original[3].cancel(identifier) else 'idle'})
            resources=self.resolve(request,deepcopy(conversation))
            if not isinstance(resources,ChatResources):raise ChatUnavailable()
            if len(request.body)>self.maximum:return error_response(413,'Request body too large')
            data=json.loads(request.body.decode('utf-8'),object_pairs_hook=_object,
                            parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
            if not isinstance(data,dict) or set(data)-{'message','attachments'}:raise ValueError()
            if 'attachments' in data and data['attachments']!=[]:
                return error_response(503,'Attachments require an explicit preparation adapter')
            message=data.get('message')
            if not isinstance(message,str) or not message.strip() or '\x00' in message:raise ValueError()
            if '@[' in message:return error_response(503,'Mentions require an explicit preparation adapter')
            client=request.header('Idempotency-Key') or uuid.uuid4().hex
            if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',client):
                return error_response(400,'A valid Idempotency-Key header is required')
            if not callable(resources.durable_result):raise ChatUnavailable()
            if not callable(resources.gate) or not callable(resources.resolver):raise ChatUnavailable()
            activities=self._activities(identifier)
            # Pure preflight before any user activity; ChatAgent.run's own
            # validation is too late for asynchronous admission.
            if not isinstance(resources.limits,Limits) or (resources.clock is not None and not callable(resources.clock)):
                raise ValueError('Invalid runtime limits or clock')
            if not isinstance(resources.model,Model) or not resources.model.identity:
                raise ValueError('Identified model required')
            if not isinstance(resources.system,str):raise ValueError('Invalid system prompt')
            if resources.compaction is not None:
                if not isinstance(resources.compaction,ChatCompaction):raise ValueError('Invalid compaction configuration')
                if resources.compaction.budget.context_window!=(resources.model.window_tokens or 200000):
                    raise ValueError('Compaction window must match identified model')
            resources.system.encode('utf-8')
            binding=resources.resolver(deepcopy(resources.model))
            from ..agent.chat import ChatBinding
            if not isinstance(binding,ChatBinding) or binding.model!=resources.model or not callable(getattr(binding.provider,'stream' if resources.model.streaming else 'complete',None)):
                raise ChatUnavailable('Current model unavailable')
            from ..agent.streaming import StreamingCompletion
            preflight_provider=StreamingCompletion(binding.provider,check=lambda:None) if resources.model.streaming else binding.provider
            BoundedAgent(preflight_provider,gate=resources.gate,tools=resources.tools,limits=resources.limits)
            # Validate remaining adapter prerequisites before user activity.
            ChatAgent(resources.transcript,resources.resolver,gate=resources.gate,tools=resources.tools,
                      capture=resources.capture,limits=resources.limits,clock=resources.clock,compaction=resources.compaction)
            def run(token):
                admitted[0]=True
                def check():
                    token.check();admission();token.check()
                def gate(context,action):
                    check();grant=resources.gate(context,action);check();return grant
                def post_effect():
                    try:admission()
                    except Exception:
                        uncertain[0]=True
                        raise EffectUncertain('Conversation authority changed after invocation') from None
                class CheckedProvider:
                    def __init__(self,provider):self.provider=provider
                    def complete(self,context,payload):
                        callback=self.provider.complete;check()
                        try:result=callback(context,payload)
                        except BaseException:post_effect();raise
                        post_effect();return result
                    def stream(self,context,payload):
                        iterator=None
                        try:
                            callback=self.provider.stream;check()
                            try:iterator=iter(callback(context,payload))
                            except BaseException:post_effect();raise
                            post_effect()
                            while True:
                                try:admission()
                                except Exception:
                                    # Prior stream effects remain uncertain even
                                    # if cleanup later restores the grant.
                                    uncertain[0]=True
                                    raise
                                token.check()
                                try:event=next(iterator)
                                except StopIteration:post_effect();return
                                except BaseException:post_effect();raise
                                post_effect();yield event
                        finally:
                            if iterator is not None and callable(getattr(iterator,'close',None)):iterator.close()
                def resolver(model):
                    check();binding=resources.resolver(model);check()
                    return replace(binding,provider=CheckedProvider(binding.provider))
                methods={name:getattr(resources.transcript,name) for name in ('begin','current','commit')}
                def transcript_call(name,args):
                    def pins():
                        for key,original_method in methods.items():
                            current=getattr(resources.transcript,key)
                            if getattr(current,'__self__',None)is not getattr(original_method,'__self__',None) or getattr(current,'__func__',current)is not getattr(original_method,'__func__',original_method):
                                raise PermissionError('Transcript authority changed')
                    admission();pins();result=methods[name](*args);admission();pins();return result
                class CheckedTranscript:
                    def begin(self,*args):return transcript_call('begin',args)
                    def current(self,*args):return transcript_call('current',args)
                    def commit(self,*args):return transcript_call('commit',args)
                def checked_tool(tool):
                    callback=tool.callback
                    def invoke(context,arguments):
                        check()
                        try:result=callback(context,arguments)
                        except BaseException:
                            post_effect();raise
                        post_effect();return result
                    return replace(tool,callback=invoke)
                tools=tuple(checked_tool(tool) for tool in resources.tools)
                agent=ChatAgent(CheckedTranscript(),resolver,gate=gate,tools=tools,
                                capture=resources.capture,limits=resources.limits,clock=resources.clock,compaction=resources.compaction)
                check()
                outcome=agent.run(conversation.session_id,client,message,model=resources.model,system=resources.system,
                                  metadata={'conversation_id':identifier,'agent_key':conversation.agent_key},token=token)
                if not outcome.persisted:raise ChatPersistenceError(outcome.result)
                if outcome.reused:
                    if not outcome.turn.finalized:raise ChatPersistenceError()
                    admission();result=resources.durable_result(conversation.session_id,deepcopy(outcome.turn));admission()
                    if not isinstance(result,RunResult):raise ChatPersistenceError()
                    outcome=replace(outcome,result=result)
                admission()
                return outcome
            admission()
            original[3].submit(identifier,client,message,activities=activities,run=run,worker=conversation.agent_key,authorize=admission)
            return json_response(202,{'status':'accepted'})
        except PermissionError:return error_response(403,'Conversation access denied')
        except (ChatBusy,DispatchConflict):return error_response(409,'Conversation already running or unresolved')
        except (ChatPersistenceError,DispatchPersistenceError):return json_response(503,{'error':'Persistence unconfirmed; reconcile before retrying','persisted':False})
        except (ChatUnavailable,RunStopped,DispatchUnavailable):return error_response(503,'Conversation runtime unavailable or changed')
        except (ValueError,UnicodeError,TypeError):return error_response(400,'Invalid chat request or configuration')
        except Exception:return error_response(500,'Chat operation failed')
