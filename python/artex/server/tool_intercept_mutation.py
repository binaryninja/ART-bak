"""Standalone explicit global mutations; no route registration or judge enabling."""
from datetime import datetime
from urllib.parse import urlsplit
from ..db.tool_intercept_config import *
from ..intercept.policy_owner import InterceptPolicyOwner
from .core import json_response,error_response
class ToolInterceptMutationService:
    def __init__(self,owner,*,authorize):
        if type(owner) is not InterceptPolicyOwner or not callable(authorize):raise ValueError('Exact policy owner required')
        owner.validate();self.owner=owner;self.authorize=authorize;self._pins=(owner,authorize)
    def _check(self):
        if self.owner is not self._pins[0] or self.authorize is not self._pins[1]:raise PolicyDenied('Service binding changed')
        self._pins[0].validate()
    def handle(self,operation,request,rule_id=None):
        committed=False
        try:
            self._check()
            if urlsplit(request.target).query:raise ValueError('Query unsupported')
            method={'create':'POST','update':'PUT','delete':'DELETE','toggle':'POST','tools':'PUT'}.get(operation)
            if request.method!=method:raise ValueError('Unsupported method')
            body={} if operation=='delete' else request.json_object(262144)
            if operation=='delete' and request.body:raise ValueError('Body unsupported')
            kw={}
            if operation in ('create','update'):
                kw['proposal']=ToolRuleProposal(**body)
            elif operation=='toggle':
                if set(body)-{'enabled'}:raise ValueError('Unknown field')
                kw['enabled']=body.get('enabled',False)
            elif operation=='tools':
                if set(body)-{'enabled_tools'}:raise ValueError('Unknown field')
                tools=body.get('enabled_tools')
                if tools is None:tools=[]
                if type(tools) is not list:raise ValueError('List required')
                kw.update(tools=tuple(tools),expected_tools_digest=request.header('X-Intercept-Tools-Digest'))
            if operation in ('update','delete','toggle'):
                raw=request.header('X-Rule-Updated-At')
                if len(raw)>64:raise ValueError('Invalid timestamp')
                kw.update(rule_id=rule_id,expected_updated_at=datetime.fromisoformat(raw.replace('Z','+00:00')))
            elif rule_id is not None:raise ValueError('Unexpected rule id')
            selection=PolicyMutation(operation,**kw);selection.validate();identity=mutation_identity(selection)
            original=(request.method,request.target,bytes(request.body),tuple(request.headers.items()))
            def allowed(m):
                self._check()
                if mutation_identity(selection)!=identity:raise PolicyDenied("Approved mutation changed")
                if m is not selection or original!=(request.method,request.target,bytes(request.body),tuple(request.headers.items())):raise PolicyDenied('Request changed')
                ok=self._pins[1](request,selection)
                self._check()
                if original!=(request.method,request.target,bytes(request.body),tuple(request.headers.items())):raise PolicyDenied('Request changed')
                return ok is True
            result=self._pins[0].mutate(selection,request_authorize=allowed);committed=True
            response=json_response(200,result)
            self._pins[0]._allow(selection,allowed,identity)
            return response
        except (ValueError,TypeError,UnicodeError):
            if not committed:return error_response(400,'Invalid or unsupported policy mutation')
        except PolicyConflict:return error_response(409,'Policy changed; reload before editing')
        except PolicyDenied:
            if not committed:return error_response(403,'Policy mutation denied')
        except BaseException:pass
        return json_response(503,{'error':'Policy outcome requires reconciliation','retry':False})
