"""Optional GET /api/report?task=ID; no active/global task fallback or route install.

The graph report uses local graph findings and separately granted GLOBAL asset
counts, not task coverage or the global findings export. Markdown is text only;
consumers must sanitize any subsequent HTML rendering. No model/URL fetch.
"""
from datetime import datetime,timezone
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
    def handle(self,request):
        try:
            self._check();s,a,c,p=self._pins
            target=urlsplit(request.target)
            if request.method not in ('GET','HEAD') or request.body or target.path!='/api/report' or len(target.query)>128 or target.fragment:raise ValueError()
            pairs=parse_qsl(target.query,keep_blank_values=True,strict_parsing=True,max_num_fields=2)
            if pairs!=[('task',str(s.scope.task_id))]:raise ValueError()
            selector=(request.method,request.target,request.body)
            def request_check():
                if (request.method,request.target,request.body)!=selector:raise ReportDenied('Report selector changed')
            def allow():
                request_check()
                self._check()
                if a(request,s.scope) is not True:raise ReportDenied()
                self._check();request_check();s.validate_read();self._check();request_check()
                # Refresh request authority after callback-bearing source/current
                # checks. Only pure original-resource checks follow this grant.
                if a(request,s.scope) is not True:raise ReportDenied()
                self._check();request_check()
            allow();snapshot=s.read();allow()
            now=c()
            if type(now) is not datetime or type(now.tzinfo) is not timezone:raise ValueError('Explicit fixed-offset clock required')
            allow()
            body=markdown(snapshot.title,snapshot.goal,now,dict(snapshot.asset_counts),snapshot.findings).encode('utf-8')
            if len(body)>2097152:raise RuntimeError('Report response bound exceeded')
            allow()
            return Response(200,body,{'Content-Type':'text/markdown; charset=utf-8'})
        except ReportDenied:return error_response(403,'Report access denied')
        except ReportMissing:return error_response(404,'Report task unavailable')
        except ValueError:return error_response(400,'Invalid report selector or clock')
        except Exception:return error_response(503,'Report capability unavailable')
