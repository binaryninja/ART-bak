"""Source-shaped conversation async admission and activity polling, no invented SSE.

Explicit host-owned dispatcher persists user activity and registers busy/cancel
before 202. Nonstream ChatAgent runs in its bounded worker; durable transcript
commit precedes terminal result activity. Original text is retained; attachments
and mention preparation are rejected rather than silently dropped. Task /api/chat
is a different main-agent/activity flow and remains unavailable.

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
from ..agent.runtime import Limits,RunResult,RunStopped,BoundedAgent
from ..chat_dispatch import DispatchConflict,DispatchUnavailable,DispatchPersistenceError
from ..sidequestion import Model


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
        router.add('POST','/api/chat',lambda request,params:error_response(503,'Task chat runtime is not configured'))
        router.add('POST','/api/conversations/{id}/messages',lambda request,params:self.handle(request,params['id']))
        router.add('POST','/api/conversations/{id}/stop',lambda request,params:self.handle(request,params['id'],stop=True))
        router.add('GET','/api/conversations/{id}/messages',lambda request,params:self.read(request,params['id']))
        router.add('GET','/api/conversations/{id}/messages/{seq}',lambda request,params:self.read(request,params['id'],params['seq']))

    def running(self,identifier):return self.dispatcher.running(identifier)

    def _activities(self,identifier):
        activities=self.activities(identifier)
        if getattr(activities,'conversation_id',None)!=identifier or not all(callable(getattr(activities,n,None)) for n in ('append_many_and_touch','list','page','detail')):
            raise ChatUnavailable('Parent-bound activity store required')
        return activities

    def read(self,request,raw_id,seq=None):
        try:
            if not re.fullmatch(r'[1-9][0-9]{0,18}',raw_id) or int(raw_id)>=2**63:raise ValueError()
            identifier=int(raw_id)
            conversation=self.store.get(identifier)
            if conversation is None:return error_response(404,'Conversation not found')
            if not isinstance(conversation,Conversation) or conversation.id!=identifier:raise ChatUnavailable()
            if self.authorize(request,deepcopy(conversation)) is not True:return error_response(403,'Conversation access denied')
            activities=self._activities(identifier)
            query=parse_qs(urlsplit(request.target).query,keep_blank_values=True)
            if any(len(v)!=1 for v in query.values()):raise ValueError()
            def number(key,default=0):
                value=query.get(key,[str(default)])[0]
                if not re.fullmatch(r'-?[0-9]+',value) or not -(2**63)<=int(value)<2**63:raise ValueError()
                return int(value)
            if seq is not None:
                if query or not re.fullmatch(r'[1-9][0-9]{0,18}',seq) or int(seq)>=2**63:raise ValueError()
                return json_response(200,{'detail':activities.detail(int(seq))})
            if set(query)-{'since','before','limit'}:raise ValueError()
            limit=number('limit',200)
            if limit>10000:raise ValueError()
            if 'since' in query:
                items,cursor=activities.list(number('since'),max(limit,1000));more=False
            else:
                before=number('before');items,more=activities.page(before,limit)
                cursor=items[-1].id if items else before
            return json_response(200,{'items':[activity_dto(item) for item in items],
                'cursor':cursor,'running':self.running(identifier),'hasMore':more})
        except ChatUnavailable:return error_response(503,'Conversation access or activity storage unavailable')
        except (ValueError,TypeError):return error_response(400,'Invalid message query')
        except Exception:return error_response(500,'Conversation activity read failed')

    def _live(self,identifier,expected=None):
        conversation=self.store.get(identifier)
        if not isinstance(conversation,Conversation) or conversation.id!=identifier:
            raise ChatUnavailable('Conversation unavailable')
        if expected is not None and (conversation.agent_key,conversation.llm_profile_id)!=(expected.agent_key,expected.llm_profile_id):
            raise RunStopped('conversation_changed')
        return conversation

    def handle(self,request,raw_id,*,stop=False):
        try:
            if not isinstance(raw_id,str) or not re.fullmatch(r'[1-9][0-9]{0,18}',raw_id) or int(raw_id)>=2**63:
                return error_response(400,'Invalid conversation identifier')
            identifier=int(raw_id)
            if urlsplit(request.target).query:return error_response(400,'Query parameters unsupported')
            conversation=self.store.get(identifier)
            if conversation is None:return error_response(404,'Conversation not found')
            if not isinstance(conversation,Conversation) or conversation.id!=identifier:raise ChatUnavailable()
            if self.authorize(request,deepcopy(conversation)) is not True:return error_response(403,'Conversation access denied')
            if stop:
                return json_response(200,{'status':'stopping' if self.dispatcher.cancel(identifier) else 'idle'})
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
            if not isinstance(resources.model,Model) or resources.model.streaming or not resources.model.identity:
                raise ValueError('Nonstream identified model required')
            if not isinstance(resources.system,str):raise ValueError('Invalid system prompt')
            resources.system.encode('utf-8')
            binding=resources.resolver(deepcopy(resources.model))
            from ..agent.chat import ChatBinding
            if not isinstance(binding,ChatBinding) or binding.model!=resources.model or not callable(getattr(binding.provider,'complete',None)):
                raise ChatUnavailable('Current model unavailable')
            BoundedAgent(binding.provider,gate=resources.gate,tools=resources.tools,limits=resources.limits)
            # Validate remaining adapter prerequisites before user activity.
            ChatAgent(resources.transcript,resources.resolver,gate=resources.gate,tools=resources.tools,
                      capture=resources.capture,limits=resources.limits,clock=resources.clock)
            def run(token):
                def check():
                    token.check()
                    self._live(identifier,conversation)
                def gate(context,action):
                    check();grant=resources.gate(context,action);check();return grant
                def resolver(model):
                    check();binding=resources.resolver(model);check();return binding
                agent=ChatAgent(resources.transcript,resolver,gate=gate,tools=resources.tools,
                                capture=resources.capture,limits=resources.limits,clock=resources.clock)
                check()
                outcome=agent.run(conversation.session_id,client,message,model=resources.model,system=resources.system,
                                  metadata={'conversation_id':identifier,'agent_key':conversation.agent_key},token=token)
                if not outcome.persisted:raise ChatPersistenceError(outcome.result)
                if outcome.reused:
                    if not outcome.turn.finalized:raise ChatPersistenceError()
                    result=resources.durable_result(conversation.session_id,deepcopy(outcome.turn))
                    if not isinstance(result,RunResult):raise ChatPersistenceError()
                    outcome=replace(outcome,result=result)
                self._live(identifier,conversation)
                return outcome
            self.dispatcher.submit(identifier,client,message,activities=activities,run=run,worker=conversation.agent_key)
            return json_response(202,{'status':'accepted'})
        except (ChatBusy,DispatchConflict):return error_response(409,'Conversation already running or unresolved')
        except (ChatPersistenceError,DispatchPersistenceError):return json_response(503,{'error':'Persistence unconfirmed; reconcile before retrying','persisted':False})
        except (ChatUnavailable,RunStopped,DispatchUnavailable):return error_response(503,'Conversation runtime unavailable or changed')
        except (ValueError,UnicodeError,TypeError):return error_response(400,'Invalid chat request or configuration')
        except Exception:return error_response(500,'Chat operation failed')
