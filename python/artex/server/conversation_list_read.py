"""Original-router-authorized metadata snapshot; no extra SQL or idle inference."""
from copy import deepcopy
from .core import json_response,error_response
from .disclosure import DisclosureResponse
from ..db.conversations import ConversationUnavailable
from .conversation_activity_read import _key,_same


def bind_conversation_list(service,router):
    from .auth import AuthService
    original=router.authorize;owner=getattr(original,'__self__',None)
    method=getattr(original,'__func__',None)
    jwt=(owner.store,owner.codec,owner.key,owner.clock) if type(owner)is AuthService else None
    if not callable(original):raise ValueError('Original router authority required')
    return lambda request,params:read_conversation_list(service,request,router,(original,owner,method,jwt))


def read_conversation_list(service,request,router,binding):
    check=None
    try:
        frozen=deepcopy(request);headers=request.headers
        store,running=service.store,service.running
        original,owner,method,jwt=binding
        missing=object();executor=getattr(store,'executor',missing)
        listing=getattr(store,'list',None);listing_key=_key(listing);running_key=_key(running)
        def pure():
            if request.headers is not headers or request!=frozen or service.store is not store or not _same(service.running,running_key):raise PermissionError()
            if router.authorize is not original or getattr(store,'executor',missing)is not executor or not _same(getattr(store,'list',None),listing_key):raise PermissionError()
            if jwt is not None and (getattr(owner.authorized,'__self__',None)is not owner or getattr(owner.authorized,'__func__',None)is not method or any(a is not b for a,b in zip((owner.store,owner.codec,owner.key,owner.clock),jwt))):raise PermissionError()
        def authority():
            pure()
            if original(deepcopy(frozen))is not True:raise PermissionError()
            pure()
        check=authority;check()
        from urllib.parse import urlsplit
        if urlsplit(request.target).query:raise ValueError()
        if not callable(listing):raise ConversationUnavailable()
        rows=listing();check()
        if rows and not callable(running):raise ConversationUnavailable()
        items=[]
        for row in rows:
            active=running(row.id);check()
            if type(active)is not bool:raise ConversationUnavailable()
            items.append(dict(row.to_dict(),running=active))
        response=json_response(200,{'conversations':items});check()
        return DisclosureResponse(response.status,response.body,response.headers,check)
    except Exception as error:
        if check is not None:
            try:check()
            except Exception:return error_response(403,'Conversation access denied')
        if isinstance(error,PermissionError):return error_response(403,'Conversation access denied')
        if isinstance(error,ConversationUnavailable):return error_response(503,'Conversation capability unavailable')
        if isinstance(error,(ValueError,UnicodeError)):return error_response(400,'Invalid conversation request')
        return error_response(500,'Conversation operation failed')
