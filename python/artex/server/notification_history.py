"""Unregistered GET delivery history; independent raw diagnostics grant required."""
import re
from urllib.parse import urlsplit,parse_qsl
from ..db.notification_history import NotificationHistoryReader,DeliverySelection,HistoryUnavailable
from .core import json_response,error_response
class InvalidRequest(ValueError):pass

def integer(value,default):
    if not re.fullmatch(r'[+-]?[0-9]{1,512}',value):return default
    n=int(value)
    return n if -2**63<=n<2**63 else default

class NotificationHistoryReadService:
    def __init__(self,reader,*,authorize,raw_authorize,current,lifecycle_guard):
        if type(reader) is not NotificationHistoryReader or any(not callable(c) for c in (authorize,raw_authorize,current,lifecycle_guard)):raise ValueError('Explicit diagnostic read authority required')
        reader.validate();self.reader=reader;self.authorize=authorize;self.raw_authorize=raw_authorize;self.current=current;self.guard=lifecycle_guard
        self._pins=(reader,reader.executor,authorize,raw_authorize,current,lifecycle_guard)
    def _check(self):
        r,e,a,raw,c,g=self._pins
        if self.reader is not r or r.executor is not e or self.authorize is not a or self.raw_authorize is not raw or self.current is not c or self.guard is not g:raise HistoryUnavailable()
        r.validate()
    def handle(self,request):
        try:
            if request.method!='GET' or request.path!='/api/notify/deliveries' or request.body:raise InvalidRequest()
            query=urlsplit(request.target).query
            if len(query)>2048:raise InvalidRequest()
            try:pairs=parse_qsl(query,keep_blank_values=True,strict_parsing=True,errors='strict',max_num_fields=6)
            except ValueError:raise InvalidRequest() from None
            keys=[k for k,v in pairs]
            if len(set(keys))!=len(keys) or set(keys)-{'channel_id','state','event_kind','page','page_size'}:raise InvalidRequest()
            q=dict(pairs);page=integer(q.get('page',''),1);size=integer(q.get('page_size',''),50)
            selection=DeliverySelection(integer(q.get('channel_id',''),0),q.get('state',''),q.get('event_kind',''),page if page>0 else 1,size if size>0 else 50)
            try:selection.validate()
            except ValueError:raise InvalidRequest() from None
            def allow():
                self._check()
                if self._pins[4]() is not True:raise PermissionError()
                self._check()
                if self._pins[2](request,selection) is not True:raise PermissionError()
                self._check()
                if self._pins[3](request,selection) is not True:raise PermissionError()
                self._check()
                # Raw grant callback may revoke the selected scope's read grant.
                if self._pins[2](request,selection) is not True:raise PermissionError()
                self._check()
                if self._pins[4]() is not True:raise PermissionError()
                self._check()
            allow()
            with self._pins[5]():
                allow();value=self._pins[0].read(selection);allow()
                response=json_response(200,value);allow()
            allow();return response
        except InvalidRequest:return error_response(400,'Invalid delivery history request')
        except PermissionError:return error_response(403,'Delivery history read denied')
        except Exception:return error_response(500,'Delivery history unavailable')
