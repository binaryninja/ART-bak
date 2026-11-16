"""Standalone fixed-task traffic metadata list; no default route or blob access."""
from urllib.parse import urlsplit,parse_qsl
from .core import json_response,error_response
from ..db.finding_traffic_read import FindingTrafficReader,FindingTrafficDenied,FindingTrafficMissing

class FindingTrafficReadService:
    def __init__(self,reader,*,authorize):
        if type(reader) is not FindingTrafficReader or not callable(authorize):raise ValueError('Exact reader and request authority required')
        reader.check();self.reader,self.authorize=reader,authorize;self._pins=(reader,authorize,reader._original)
    def _check(self):
        r,a,p=self._pins
        if self.reader is not r or self.authorize is not a or r._original is not p:raise FindingTrafficDenied()
        r.check()
    def handle(self,request):
        try:
            self._check();r,a,p=self._pins;s=r.scope
            target=urlsplit(request.target)
            if request.method not in ('GET','HEAD') or request.body or target.path!='/api/exploration/findings/'+str(s.finding_id)+'/traffic' or target.fragment or len(target.query)>128:raise ValueError()
            query=parse_qsl(target.query,keep_blank_values=True,strict_parsing=True,max_num_fields=2)
            if query not in ([],[('context_task',str(s.task_id))]):raise ValueError()
            if type(request.headers) is not dict:raise ValueError()
            headers=request.headers;values=tuple(headers.items());identity=(request.method,request.target,request.body)
            def allow(scope):
                self._check()
                def exact():
                    if scope is not s or (request.method,request.target,request.body)!=identity or request.headers is not headers or tuple(headers.items())!=values:raise FindingTrafficDenied()
                exact();allowed=a(request,s);self._check();exact();return allowed is True
            result,disclose=r.read(allow)
            response=json_response(200,result);disclose();self._check();return response
        except FindingTrafficDenied:return error_response(403,'Finding traffic metadata denied')
        except FindingTrafficMissing:return error_response(404,'Finding unavailable in configured task')
        except (ValueError,UnicodeError):return error_response(400,'Invalid finding traffic selector')
        except Exception:return error_response(503,'Finding traffic metadata unavailable')
