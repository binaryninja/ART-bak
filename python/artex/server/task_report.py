"""Optional GET /api/report?task=ID; no active/global task fallback or route install.

The graph report uses local graph findings and separately granted GLOBAL asset
counts, not task coverage or the global findings export. Markdown is text only;
consumers must sanitize any subsequent HTML rendering. No model/URL fetch.
"""
from datetime import datetime,timezone
from dataclasses import dataclass
from weakref import WeakKeyDictionary
from threading import RLock
from .auth import AuthService

_PREPARED=WeakKeyDictionary()
_PREPARED_LOCK=RLock()
_AUTHORIZED=AuthService.authorized

@dataclass(frozen=True,eq=False)
class PreparedReport:
    response:object

def _issued(service,response,validate=None):
    receipt=PreparedReport(response)
    with _PREPARED_LOCK:_PREPARED[receipt]=(service,response,(response.status,response.body,tuple(response.headers.items())),validate)
    return receipt

from urllib.parse import urlsplit,parse_qsl
from .core import Response,error_response
from ..db.task_report import TaskReportStore,ReportDenied,ReportMissing
from ..report import markdown

class TaskReportService:
    def __init__(self,store,*,authorize,clock=lambda:datetime.now(timezone.utc)):
        if type(store) is not TaskReportStore or not callable(authorize) or not callable(clock):raise ValueError('Exact report store and request authority required')
        store.check()
        self.store,self.authorize,self.clock=store,authorize,clock
        self._pins=(store,authorize,clock,store._pins)
        self._original_pins=self._pins
    def register(self,router):router.add('GET','/api/report',lambda req,params:self.handle(req))
    def _check(self):
        s,a,c,p=self._original_pins
        if self._pins is not self._original_pins or self.store is not s or self.authorize is not a or self.clock is not c or s._pins is not p:raise ReportDenied('Report service changed')
        s.check()
    def handle(self,request):return self.prepare(request).response
    def validate_disclosure(self,prepared):
        self._check()
        with _PREPARED_LOCK:record=_PREPARED.get(prepared) if type(prepared) is PreparedReport else None
        if record is None or record[0] is not self:raise ReportDenied('Original prepared report required')
        _,response,values,check=record
        def exact():
            self._check()
            if prepared.response is not response or (response.status,response.body,tuple(response.headers.items()))!=values:raise ReportDenied('Prepared report changed')
        exact()
        if check is not None:check()
        exact();return True
    def prepare(self,request,*,jwt_authorize=None):
        try:
            auth_owner=getattr(jwt_authorize,'__self__',None)
            if jwt_authorize is not None and (type(auth_owner) is not AuthService or getattr(jwt_authorize,'__func__',None) is not _AUTHORIZED or AuthService.authorized is not _AUTHORIZED):
                raise ReportDenied('Original JWT authority required')
            auth_pins=None if auth_owner is None else (auth_owner.store,auth_owner.codec,auth_owner.key,auth_owner.clock)
            def jwt(validate=True):
                if auth_owner is None:return
                def pins():
                    if AuthService.authorized is not _AUTHORIZED or getattr(auth_owner.authorized,'__func__',None) is not _AUTHORIZED or any(x is not y for x,y in zip((auth_owner.store,auth_owner.codec,auth_owner.key,auth_owner.clock),auth_pins)):
                        raise ReportDenied('JWT authority changed')
                pins()
                if validate and jwt_authorize(request) is not True:raise ReportDenied('JWT access denied')
                pins()

            self._check();s,a,c,p=self._pins
            target=urlsplit(request.target)
            if request.method not in ('GET','HEAD') or request.body or target.path!='/api/report' or len(target.query)>128 or target.fragment:raise ValueError()
            pairs=parse_qsl(target.query,keep_blank_values=True,strict_parsing=True,max_num_fields=2)
            if pairs!=[('task',str(s.scope.task_id))]:raise ValueError()
            if type(request.headers) is not dict:raise ValueError()
            headers=request.headers;header_values=tuple(headers.items())
            selector=(request.method,request.target,request.body)
            def request_check():
                if request.headers is not headers or tuple(headers.items())!=header_values or (request.method,request.target,request.body)!=selector:raise ReportDenied('Report selector changed')
            def allow():
                request_check();jwt()
                self._check()
                if a(request,s.scope) is not True:raise ReportDenied()
                self._check();request_check();s.validate_read();self._check();request_check()
                # Refresh request authority after callback-bearing source/current
                # checks. Only pure original-resource checks follow this grant.
                if a(request,s.scope) is not True:raise ReportDenied()
                self._check();request_check();jwt();self._check();request_check()
                # Temporal JWT observation precedes the final data-grant refresh.
                # No repeated-clock fixed point is claimed for mutating callbacks.
                s.validate_read();self._check();request_check()
                if a(request,s.scope) is not True:raise ReportDenied()
                self._check();request_check();jwt(False)
            allow();snapshot=s.read();allow()
            now=c()
            if type(now) is not datetime or type(now.tzinfo) is not timezone:raise ValueError('Explicit fixed-offset clock required')
            allow()
            body=markdown(snapshot.title,snapshot.goal,now,dict(snapshot.asset_counts),snapshot.findings).encode('utf-8')
            if len(body)>2097152:raise RuntimeError('Report response bound exceeded')
            allow()
            return _issued(self,Response(200,body,{'Content-Type':'text/markdown; charset=utf-8'}),allow)
        except ReportDenied:return _issued(self,error_response(403,'Report access denied'))
        except ReportMissing:return _issued(self,error_response(404,'Report task unavailable'))
        except ValueError:return _issued(self,error_response(400,'Invalid report selector or clock'))
        except Exception:return _issued(self,error_response(503,'Report capability unavailable'))
