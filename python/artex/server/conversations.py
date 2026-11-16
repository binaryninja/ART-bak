"""Authenticated metadata routes; no invented GET-by-ID route or chat execution.

Lists require explicit live running-state resolver, never fabricate idle. Creation
requires agent existence and selected-profile/key availability capabilities.
Default title translated to English. Stricter JSON/ID/query validation than Go.
Deletion/batch deletion fail503 pending complete cleanup/drain orchestration.
"""
import json,re
from urllib.parse import urlsplit
from .core import json_response,error_response
from ..db.conversations import ConversationInvalid,ConversationMissing,ConversationUnavailable,identifier,text

def _pairs(items):
    result={}
    for key,value in items:
        if key in result:raise ConversationInvalid('Duplicate key')
        result[key]=value
    return result

class ConversationService:
    def __init__(self,store=None,*,agent_available=None,profile_available=None,running=None):
        self.store=store;self.agent_available=agent_available;self.profile_available=profile_available;self.running=running
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
            if key is not None and self.store.get(key) is None:raise ConversationMissing()
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
                self._profile(data['llm_profile_id']);self.store.update_profile(key,data['llm_profile_id'])
                return json_response(200,{'ok':True})
            result=self.store.patch(key,data)
            if result is None:raise ConversationMissing()
            return json_response(200,result.to_dict())
        except (ValueError,UnicodeError):return error_response(400,'Invalid conversation request')
        except ConversationMissing:return error_response(404,'Conversation or agent not found')
        except ConversationUnavailable:return error_response(503,'Conversation capability unavailable')
        except Exception:return error_response(500,'Conversation operation failed')
