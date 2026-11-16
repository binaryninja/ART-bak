"""Conversation history snapshots with original request-bound disclosure receipts.

Activity data is read once. Metadata selection/authority may be observed again;
this is a finite cooperative fence, not an atomic database/network snapshot.
"""
from copy import deepcopy
import re
from urllib.parse import parse_qs,urlsplit
from .core import json_response,error_response
from .disclosure import DisclosureResponse
from ..db.conversations import Conversation
from ..agent.chat import ChatUnavailable


def _key(method):return getattr(method,'__self__',None),getattr(method,'__func__',method)
def _same(method,key):return all(a is b for a,b in zip(_key(method),key))

def read_conversation_activity(service,request,raw_id,seq=None,transport=None,transport_pins=None):
    authority=None
    try:
        from .chat import activity_dto
        frozen=deepcopy(request);headers=request.headers
        store,factory,authorize,dispatcher=service.store,service.activities,service.authorize,service.dispatcher
        getter=store.get;getter_key=_key(getter);running=dispatcher.running;running_key=_key(running)
        missing=object();store_executor=getattr(store,'executor',missing)
        activities=None;activity_keys=None;activity_executor=missing
        def pure():
            if transport_pins is not None:transport_pins()
            if getattr(store,'executor',missing)is not store_executor:raise PermissionError('Conversation executor changed')
            if request.headers is not headers or request!=frozen or any(a is not b for a,b in zip((service.store,service.activities,service.authorize,service.dispatcher),(store,factory,authorize,dispatcher))):
                raise PermissionError('Conversation read binding changed')
            if not _same(store.get,getter_key) or not _same(dispatcher.running,running_key):raise PermissionError('Conversation read method changed')
            if activities is not None:
                if getattr(activities,'executor',missing)is not activity_executor:raise PermissionError('Conversation activity executor changed')
                if activities.conversation_id!=identifier or any(not _same(getattr(activities,n),k) for n,k in activity_keys):raise PermissionError('Conversation activity binding changed')
        if not isinstance(raw_id,str) or not re.fullmatch(r'[1-9][0-9]{0,18}',raw_id) or int(raw_id)>=2**63:raise ValueError()
        identifier=int(raw_id)
        conversation=getter(identifier)
        if conversation is None:
            pure()
            if transport is not None and transport(deepcopy(frozen))is not True:raise PermissionError()
            pure();return error_response(404,'Conversation not found')
        if not isinstance(conversation,Conversation) or conversation.id!=identifier:raise ChatUnavailable()
        expected=(conversation.id,conversation.agent_key,conversation.llm_profile_id)
        def reauthorize():
            pure();current=getter(identifier)
            if not isinstance(current,Conversation) or (current.id,current.agent_key,current.llm_profile_id)!=expected:raise PermissionError('Conversation selection changed')
            if authorize(deepcopy(frozen),deepcopy(current))is not True:raise PermissionError('Conversation read denied')
            if transport is not None and transport(deepcopy(frozen))is not True:raise PermissionError('Conversation transport denied')
            if authorize(deepcopy(frozen),deepcopy(current))is not True:raise PermissionError('Conversation read denied')
            pure()
        authority=reauthorize;authority()
        activities=factory(identifier)
        names=('append_many_and_touch','list','page','detail')
        if getattr(activities,'conversation_id',None)!=identifier or any(not callable(getattr(activities,n,None)) for n in names):
            activities=None;raise ChatUnavailable()
        activity_keys=tuple((n,_key(getattr(activities,n))) for n in names)
        activity_executor=getattr(activities,'executor',missing)
        authority()
        query=parse_qs(urlsplit(request.target).query,keep_blank_values=True)
        if any(len(v)!=1 for v in query.values()):raise ValueError()
        def number(key,default=0):
            value=query.get(key,[str(default)])[0]
            if not re.fullmatch(r'-?[0-9]+',value) or not -(2**63)<=int(value)<2**63:raise ValueError()
            return int(value)
        if seq is not None:
            if query or not re.fullmatch(r'[1-9][0-9]{0,18}',seq) or int(seq)>=2**63:raise ValueError()
            value={'detail':activities.detail(int(seq))}
        else:
            if set(query)-{'since','before','limit'}:raise ValueError()
            limit=number('limit',200)
            if limit>10000:raise ValueError()
            if 'since' in query:
                items,cursor=activities.list(number('since'),max(limit,1000));more=False
            else:
                before=number('before');items,more=activities.page(before,limit)
                cursor=items[-1].id if items else before
            value={'items':[activity_dto(item) for item in items], 'cursor':cursor,
                   'running':running(identifier),'hasMore':more}
        authority();response=json_response(200,value);authority()
        return DisclosureResponse(response.status,response.body,response.headers,authority)
    except Exception as error:
        if authority is not None:
            try:authority()
            except Exception:return error_response(403,'Conversation access denied')
        elif transport is not None:
            try:
                if transport(request)is not True:raise PermissionError()
            except Exception:return error_response(403,'Conversation access denied')
        if isinstance(error,PermissionError):return error_response(403,'Conversation access denied')
        if isinstance(error,ChatUnavailable):return error_response(503,'Conversation access or activity storage unavailable')
        if isinstance(error,(ValueError,TypeError)):return error_response(400,'Invalid message query')
        return error_response(500,'Conversation activity read failed')
