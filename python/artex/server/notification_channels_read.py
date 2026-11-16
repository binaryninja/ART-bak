"""Standalone GET channels: explicit operational/config disclosure, no sends."""
from urllib.parse import urlsplit
from ..db.notification_channels_read import NotificationChannelsReader,ChannelReadUnavailable
from .core import json_response,error_response

class NotificationChannelsReadService:
    def __init__(self,reader,*,authorize,config_authorize,current,lifecycle_guard):
        if type(reader) is not NotificationChannelsReader or any(not callable(c) for c in (authorize,config_authorize,current,lifecycle_guard)):raise ValueError('Exact reader and independent operational/config authority required')
        reader.validate();self.reader=reader;self.authorize=authorize;self.config_authorize=config_authorize;self.current=current;self.guard=lifecycle_guard
        self._pins=(reader,reader.executor,authorize,config_authorize,current,lifecycle_guard)
    def _check(self):
        r,e,a,c,current,g=self._pins
        if self.reader is not r or r.executor is not e or self.authorize is not a or self.config_authorize is not c or self.current is not current or self.guard is not g:raise PermissionError()
        r.validate()
    def handle(self,request):
        if request.method!='GET' or request.path!='/api/notify/channels' or request.body or urlsplit(request.target).query:return error_response(400,'Invalid notification channels request')
        original=(request.method,request.target,request.body)
        try:
            r,e,a,c,current,g=self._pins
            def check():
                self._check()
                if original!=(request.method,request.target,request.body):raise PermissionError()
            def allow():
                check()
                if current() is not True:raise PermissionError()
                check()
                if a(request) is not True:raise PermissionError()
                check()
                if c(request) is not True:raise PermissionError()
                check()
                if a(request) is not True:raise PermissionError()
                check()
                if current() is not True:raise PermissionError()
                check()
            allow()
            with g():
                allow();result=r.read();allow()
                response=json_response(200,result);allow()
            allow();return response
        except PermissionError:return error_response(403,'Notification channels read denied')
        except Exception:return error_response(500,'Notification channels unavailable')
