"""Unregistered metadata-only PATCH. No creation, deletion, sending or config edits."""
import json,re
from urllib.parse import urlsplit
from ..db.notification_channel_metadata import (ChannelMetadataStore,proposal,MetadataDenied,MetadataMissing,MetadataUncertain)
from .core import json_response,error_response

def _pairs(pairs):
    value={}
    for key,item in pairs:
        if key in value:raise ValueError('Duplicate field')
        value[key]=item
    return value
class ChannelMetadataService:
    def __init__(self,store,*,authorize):
        if type(store) is not ChannelMetadataStore or not callable(authorize):raise ValueError('Exact store and independent write grant required')
        store.validate();self.store=store;self.authorize=authorize;self._pins=(store,store.executor,store.scope,authorize)
    def _check(self):
        s,e,scope,a=self._pins
        if self.store is not s or s.executor is not e or s.scope is not scope or self.authorize is not a:raise MetadataDenied()
        s.validate()
    def handle(self,request,*,write_authorize=None):
        try:
            m=re.fullmatch('/api/notify/channels/([1-9][0-9]{0,18})',request.path)
            if request.method!='PATCH' or not m or urlsplit(request.target).query or len(request.body)>65536:raise ValueError('Invalid channel metadata request')
            value=json.loads(request.body,object_pairs_hook=_pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('Invalid number')))
            p=proposal(int(m[1]),value)
        except Exception:return error_response(400,'Invalid channel metadata request')
        original=(request.method,request.target,request.body)
        try:
            self._check()
            if write_authorize is not None and not callable(write_authorize):raise ValueError('Explicit final write authority required')
            def final_write(scope,patch):
                self._check()
                if original!=(request.method,request.target,request.body) or patch is not p:raise MetadataDenied()
                allowed=write_authorize(request,scope,p)
                self._check()
                if original!=(request.method,request.target,request.body):raise MetadataDenied()
                return allowed is True
            def authorize(scope,patch):
                self._check()
                if original!=(request.method,request.target,request.body) or patch is not p:raise MetadataDenied()
                allowed=self._pins[3](request,scope,p)
                self._check()
                if original!=(request.method,request.target,request.body):raise MetadataDenied()
                return allowed is True
            id=self._pins[0].patch(p,request_authorize=authorize,write_authorize=final_write if write_authorize is not None else None)
            return json_response(200,{'id':id})
        except MetadataMissing:return error_response(404,'Notification channel not found')
        except MetadataDenied:return error_response(403,'Channel metadata mutation denied')
        except MetadataUncertain:return json_response(503,{'error':'Channel metadata outcome unknown; reconcile before retry','outcome':'unknown','retry':False})
        except Exception:return error_response(500,'Channel metadata unavailable')
