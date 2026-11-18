"""Authenticated metadata routes; no invented GET-by-ID route or chat execution.

Lists require explicit live running-state resolver, never fabricate idle. Creation
requires agent existence and selected-profile/key availability capabilities.
Default title translated to English. Stricter JSON/ID/query validation than Go.
Deletion/batch deletion fail503 pending complete cleanup/drain orchestration.
"""
import json,re
from urllib.parse import urlsplit
from .core import json_response,error_response
from ..db.conversations import ConversationInvalid,ConversationMissing,ConversationStore,ConversationUnavailable,identifier,text
from ..db.configuration_ownership import require_write
from ..db.transaction_coordination import TransactionCoordinationBinding

_PROFILE_GET = ConversationStore.get
_PROFILE_UPDATE = ConversationStore.update_profile

def _pairs(items):
    result={}
    for key,value in items:
        if key in result:raise ConversationInvalid('Duplicate key')
        result[key]=value
    return result

class ConversationService:
    def __init__(self,store=None,*,agent_available=None,profile_available=None,running=None,
                 transaction_coordination=None):
        self.store=store;self.agent_available=agent_available;self.profile_available=profile_available;self.running=running
        self.transaction_coordination = transaction_coordination
        self._configured_coordination = transaction_coordination
        self._profile_store = store if transaction_coordination is not None else None
        self._profile_binding()

    def _profile_binding(self):
        binding = self._configured_coordination
        if self.transaction_coordination is not binding:
            raise ConversationUnavailable('Conversation coordination option changed')
        if binding is not None:
            store = self._profile_store
            if (type(binding) is not TransactionCoordinationBinding or type(store) is not ConversationStore
                    or self.store is not store or store.executor is not binding.executor):
                raise ConversationUnavailable('Coordinated conversation store binding required')
            for method, original in ((store.get, _PROFILE_GET), (store.update_profile, _PROFILE_UPDATE)):
                if getattr(method, '__self__', None) is not store or getattr(method, '__func__', None) is not original:
                    raise ConversationUnavailable('Original conversation profile methods required')
        return binding

    def _check_profile_writer(self):
        binding = self._profile_binding()
        if binding is not None:
            require_write(binding.executor, 'conversations.update_profile')
        return binding

    def register(self,router):
        for method,path,action in (
            ('GET','/api/conversations','list'),('POST','/api/conversations','create'),
            ('PATCH','/api/conversations/{id}','patch'),('PATCH','/api/conversations/{id}/profile','profile'),
            ('DELETE','/api/conversations/{id}','delete'),('POST','/api/conversations/delete/batch','delete_batch')):
            if action=='list':
                from .conversation_list_read import bind_conversation_list
                router.add(method,path,bind_conversation_list(self,router))
            else:
                router.add(method,path,lambda request,params,action=action:self._handle(action,request,params))
    def _profile(self,value):
        if value is None:return
        identifier(value)
        if not callable(self.profile_available):raise ConversationUnavailable()
        if self.profile_available(value) is not True:raise ConversationInvalid('Profile unavailable')
    def _handle(self,action,request,params):
        try:
            if urlsplit(request.target).query:raise ConversationInvalid('Unsupported query')
            if self.store is None:raise ConversationUnavailable()
            key=None
            if 'id' in params:
                if not re.fullmatch('[0-9]{1,19}',params['id']):raise ConversationInvalid('Invalid ID')
                key=identifier(int(params['id']))
            if action=='list':
                rows=self.store.list()
                if rows and not callable(self.running):raise ConversationUnavailable()
                items=[]
                for row in rows:
                    active=self.running(row.id)
                    if type(active) is not bool:raise ConversationUnavailable()
                    items.append(dict(row.to_dict(),running=active))
                return json_response(200,{'conversations':items})
            if action in ('delete','delete_batch'):raise ConversationUnavailable()
            if action == 'profile':
                binding = self._check_profile_writer()
            if key is not None:
                if action == 'profile' and binding is not None:
                    row = _PROFILE_GET(self._profile_store, key)
                else:
                    row = self.store.get(key)
                if action == 'profile':
                    self._check_profile_writer()
                if row is None:raise ConversationMissing()
            if len(request.body)>64<<10:return error_response(413,'Request body too large')
            data=json.loads(request.body,object_pairs_hook=_pairs,parse_constant=lambda _:(_ for _ in ()).throw(ConversationInvalid()))
            if not isinstance(data,dict):raise ConversationInvalid()
            if action=='create':
                if set(data)-{'agent_key','title','llm_profile_id'}:raise ConversationInvalid()
                agent=text(data.get('agent_key'),120);title=text(data.get('title',''),200,empty=True)
                if not callable(self.agent_available):raise ConversationUnavailable()
                if self.agent_available(agent) is not True:raise ConversationMissing()
                profile=data.get('llm_profile_id');self._profile(profile)
                return json_response(200,self.store.create(agent,title,profile).to_dict())
            if action=='profile':
                if set(data)!={'llm_profile_id'}:raise ConversationInvalid()
                profile = data['llm_profile_id']
                self._profile(profile)
                self._check_profile_writer()
                if binding is None:
                    self.store.update_profile(key, profile)
                else:
                    _PROFILE_UPDATE(self._profile_store, key, profile, transaction_coordination=binding)
                return json_response(200,{'ok':True})
            result=self.store.patch(key,data)
            if result is None:raise ConversationMissing()
            return json_response(200,result.to_dict())
        except (ValueError,UnicodeError):return error_response(400,'Invalid conversation request')
        except ConversationMissing:return error_response(404,'Conversation or agent not found')
        except ConversationUnavailable:return error_response(503,'Conversation capability unavailable')
        except Exception:return error_response(500,'Conversation operation failed')
