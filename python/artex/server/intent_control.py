"""Explicit HTTP authority for the local pause/resume subset; no routes by default."""
from contextvars import ContextVar
from dataclasses import dataclass
import json,re
from urllib.parse import urlsplit
from ..agent.intent_control import IntentControl,IntentControlUncertain,IntentControlUnavailable
from ..agent.host import AgentHost
from .core import Request,error_response,json_response

_CONTEXT=ContextVar('intent_control_request',default=None)
@dataclass(frozen=True)
class IntentSelector:
    task_id:str
    intent_id:int
    action:str

class IntentControlService:
    def __init__(self,host,*,authorize_selector,authorize_proposal,fixed_task_id=None,timeout=5.):
        import math
        if type(host) is not AgentHost or not callable(authorize_selector) or not callable(authorize_proposal):raise ValueError('Exact host and explicit grants required')
        if fixed_task_id is not None and (type(fixed_task_id) is not str or not re.fullmatch('[1-9][0-9]{0,18}',fixed_task_id) or int(fixed_task_id)>=2**63):raise ValueError('Canonical fixed task required')
        if type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0<=timeout<=30:raise ValueError('Bounded timeout required')
        self.host=host;self.authorize_selector=authorize_selector;self.authorize_proposal=authorize_proposal;self.fixed_task_id=fixed_task_id;self.timeout=timeout
        self.adapter=IntentControl(host,authorize=self._authorize)
        self._pins=(host,host.runner,authorize_selector,authorize_proposal,fixed_task_id,timeout,self.adapter,self.adapter.authorize)
        self._host_methods=(host.control_intent,host.inspect_intent_control)
    def check(self):
        h,r,s,p,f,t,a,grant=self._pins
        if self.host is not h or h.runner is not r or self.authorize_selector is not s or self.authorize_proposal is not p or type(self.fixed_task_id) is not type(f) or self.fixed_task_id!=f or type(self.timeout) is not type(t) or self.timeout!=t or self.adapter is not a or a.authorize!=grant:raise PermissionError('Intent HTTP binding changed')
        for name,method in _METHODS.items():
            if getattr(type(self),name) is not method or getattr(getattr(self,name),'__func__',None) is not method:raise PermissionError('Original service methods required')
        for method,name in zip(self._host_methods,('control_intent','inspect_intent_control')):
            if getattr(h,name)!=method or getattr(method,'__func__',None) is not getattr(AgentHost,name):raise PermissionError('Original host methods required')
        a.validate_bindings()
    def _request_grant(self,state):
        self.check()
        if _CONTEXT.get() is not state or state['service'] is not self:raise PermissionError('Original request context required')
        state['binding']()
        if state['jwt'](state['request']) is not True:raise PermissionError('JWT revoked')
        if self._pins[2](state['request'],state['selector']) is not True:raise PermissionError('Intent request denied')
        self.check();state['binding']()
        self._request_pins(state)
    def _request_pins(self,state):
        self.check();state['binding']()
        selector=state['selector']
        if selector is not state['selector_original'] or type(selector) is not IntentSelector or type(selector.task_id) is not str or type(selector.intent_id) is not int or type(selector.action) is not str or (selector.task_id,selector.intent_id,selector.action)!=state['selector_values']:raise PermissionError('Selector changed')
        req=state['request']
        if type(req) is not Request or type(req.method) is not str or type(req.target) is not str or type(req.body) is not bytes or (req.method,req.target,req.body,tuple(req.headers.items()))!=state['request_values'] or req.headers is not state['headers']:raise PermissionError('Request changed')
    def _authorize(self,proposal):
        state=_CONTEXT.get()
        if state is None:return False
        self._request_grant(state);s=state['selector']
        if str(proposal.task_id)!=s.task_id or proposal.intent_id!=s.intent_id or proposal.action!=s.action:return False
        if self._pins[3](proposal) is not True:return False
        self._request_grant(state)
        self._request_pins(state)
        state['proposal']=proposal
        return True
    def handle(self,request,params,state):
        try:
            if urlsplit(request.target).query:raise ValueError()
            task=params['id'];iid=params['iid']
            if any(not re.fullmatch('[1-9][0-9]{0,18}',v) or int(v)>=2**63 for v in (task,iid)):raise ValueError()
            if self.fixed_task_id is not None and task!=self.fixed_task_id:raise PermissionError()
            if len(request.body)>4096:return error_response(413,'Control body too large')
            def pairs(items):
                out={}
                for k,v in items:
                    if k in out:raise ValueError()
                    out[k]=v
                return out
            value=json.loads(request.body,object_pairs_hook=pairs)
            if type(value) is not dict or set(value)-{'action','reason','mode'} or any(type(v) is not str for v in value.values()):raise ValueError()
            action=value.get('action')
            if action not in ('pause','resume','cancel'):raise ValueError()
            state['selector']=IntentSelector(task,int(iid),action);state['selector_values']=(task,int(iid),action);state['selector_original']=state['selector'];self._request_grant(state)
            if action=='cancel':return error_response(501,'Intent deletion is not supported by this adapter')
            state['attempted']=True
            result=self.host.control_intent(task,int(iid),action,timeout=self.timeout)
            if result.phase=='pending':return json_response(409,{'error':'Intent pause pending','pending':True,'retry':False})
            state['confirmed']=True
            return json_response(200,{'id':result.intent_id,'state':result.state})
        except (ValueError,KeyError,TypeError,json.JSONDecodeError):return error_response(400,'Invalid intent control request')
        except IntentControlUncertain:return json_response(409,{'error':'Intent operation unconfirmed','unknown':True,'retry':False})
        except IntentControlUnavailable:return error_response(409,'Intent control unavailable')
        except PermissionError:return error_response(403,'Intent control denied')
        except Exception:return json_response(409,{'error':'Intent operation unavailable','unknown':bool(state.get('attempted')),'retry':False})

_METHODS={n:getattr(IntentControlService,n) for n in ('check','_request_grant','_request_pins','_authorize','handle')}
