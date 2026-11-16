"""Explicit conversation-only terminal interception. Not execution provenance.

All capabilities are privately issued for one actual ChatAgent turn/dispatch.
Audit correlation remains unavailable; protocol IDs are never DB execution IDs.
Legacy Context/log, task assembly, ask handling and judge mode are untouched.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime,timezone
import json
from threading import get_ident,RLock
from . import Interceptor,digest_input
from .policy_owner import InterceptPolicyOwner
from ..db.intercept_decisions import InterceptDecisionStore,DecisionRecord,DecisionUncertain
from ..chat_transcripts import ChatTranscriptAdapter
from ..transcripts import TranscriptStore
from ..sidequestion import Model
from ..agent.runtime import Action,RuntimeContext,RunStopped,EffectUncertain,Grant,_json as _runtime_json

def _json(v):return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False)
def _action(a):
    if type(a) is not Action or a.kind!='tool' or type(a.name) is not str or type(a.call_id) is not str or not a.call_id or type(a.input_json) is not str:raise RunStopped('invalid_intercept_call')
    return (a.kind,a.name,a.input_json,a.call_id)
class ConversationTerminalIntercept:
    def __init__(self,config_owner,interceptor,decisions,transcript,*,session_id,model,authorize_call,authorize_audit):
        if type(config_owner) is not InterceptPolicyOwner or type(interceptor) is not Interceptor or not any(x is interceptor for x in config_owner.interceptors):raise ValueError('Exact declared interceptor required')
        if type(decisions) is not InterceptDecisionStore or decisions.scope.conversation_id is None or decisions.scope.task_id is not None:raise ValueError('Conversation-only decision scope required')
        if type(transcript) is not ChatTranscriptAdapter or type(transcript.store) is not TranscriptStore:raise ValueError('Canonical transcript required')
        if type(model) is not Model or not model.identity or type(session_id) is not str or not session_id:raise ValueError('Explicit conversation session/model required')
        if any(not callable(x) for x in (authorize_call,authorize_audit)):raise ValueError('Explicit durable grants required')
        if config_owner.store.executor is not decisions.executor or config_owner.workspace!=decisions.scope.workspace:raise ValueError('Exact workspace/executor required')
        if interceptor.reviewer is not None:raise ValueError('Judge mode unsupported')
        config_owner.validate();decisions._pure()
        self.config_owner=config_owner;self.interceptor=interceptor;self.decisions=decisions;self.transcript=transcript
        self.session_id=session_id;self.model=model;self.authorize_call=authorize_call;self.authorize_audit=authorize_audit
        self._pins=(config_owner,interceptor,decisions,transcript,session_id,model,authorize_call,authorize_audit)
        self._model=_json(asdict(model));self._transcript=(transcript.store,transcript.store.root,transcript.store.max_bytes,transcript.agent_id,transcript.recovery_guard)
        self._methods=(interceptor.match,interceptor.is_tool_enabled)
        self._resource_methods=(decisions.create,decisions._pure,config_owner.validate,config_owner._register_call,config_owner._check_call,config_owner._release_call,transcript.begin,transcript.current,transcript.commit)
        self._agent=None;self._session=None;self._lock=RLock();self.unresolved=False
    def _pure(self):
        if self.unresolved:raise EffectUncertain('Durable interception requires reconciliation')
        c,i,d,t,s,m,a,b=self._pins
        if any(x is not y for x,y in zip((self.config_owner,self.interceptor,self.decisions,self.transcript,self.model,self.authorize_call,self.authorize_audit),(c,i,d,t,m,a,b))) or self.session_id!=s or _json(asdict(m))!=self._model:raise RunStopped('intercept_binding_changed')
        ts,root,size,agent,recovery=self._transcript
        if t.store is not ts or ts.root!=root or ts.max_bytes!=size or t.agent_id!=agent or t.recovery_guard is not recovery or i.reviewer is not None or (i.match,i.is_tool_enabled)!=self._methods:raise RunStopped('intercept_binding_changed')
        if (d.create,d._pure,c.validate,c._register_call,c._check_call,c._release_call,t.begin,t.current,t.commit)!=self._resource_methods:raise RunStopped("intercept_method_changed")
        c.validate();d._pure()
    def _bind_agent(self,agent):
        from ..agent.chat import ChatAgent
        self._pure()
        if type(agent) is not ChatAgent or agent.transcript is not self._pins[3]:raise ValueError('Exact canonical ChatAgent required')
        with self._lock:
            if self._agent is not None and self._agent is not agent:raise ValueError('Owner already bound')
            self._agent=agent
    def _preflight(self,agent,session,model):
        self._pure()
        if agent is not self._agent or session!=self._pins[4] or type(model) is not Model or _json(asdict(model))!=self._model:raise RunStopped('intercept_scope_mismatch')
    def _open(self,agent,session,turn,token,model):
        self._preflight(agent,session,model)
        with self._lock:
            if self._session is not None:raise RunStopped('intercept_session_busy')
            if agent._running.get(session) is not token or not turn.created or turn.finalized:raise RunStopped('intercept_turn_unavailable')
            result=_ConversationSession(self,agent,session,turn,token,model)
            self._session=result
            return result

class _ConversationSession:
    def __init__(self,owner,agent,session,turn,token,model):
        self.owner=owner;self.agent=agent;self.session=session;self.turn=turn;self.token=token;self.model=model
        self._thread=get_ident();self._active=True;self._runner=None;self._call=None;self._lease=None;self._uncertain=False
        self._token_pins=(token.event,token.external)
        self._turn=_json(asdict(turn));self._hostpins=(agent.transcript,agent.resolver,agent.gate,agent.tools,agent.limits,agent.clock,agent.terminal_intercept,agent._running)
    def bind(self,runner):
        from ..agent.runtime import BoundedAgent
        self._pure()
        if type(runner) is not BoundedAgent or self._runner is not None:raise RunStopped('intercept_runner_invalid')
        self._runner=runner;self._runner_gate=runner.gate
        self._runner_pins=(runner.provider,runner.tools,runner.limits,runner.clock,runner.settlement,runner._authorize)
    def _pure(self):
        def bindings():
            self.owner._pure()
            a=self.agent
            if not self._active or get_ident()!=self._thread or self.owner._session is not self or self.owner._agent is not a or a._running.get(self.session) is not self.token or _json(asdict(self.turn))!=self._turn:raise RunStopped('intercept_session_stale')
            if any(x is not y for x,y in zip((a.transcript,a.resolver,a.gate,a.tools,a.limits,a.clock,a.terminal_intercept,a._running),self._hostpins)):raise RunStopped('intercept_host_changed')
            if self.token.event is not self._token_pins[0] or self.token.external is not self._token_pins[1]:raise RunStopped('intercept_token_changed')
            if self._runner is not None:
                r=self._runner
                if r.gate is not self._runner_gate or r._terminal_dispatch is not self or (r.provider,r.tools,r.limits,r.clock,r.settlement,r._authorize)!=self._runner_pins:raise RunStopped('intercept_runner_changed')
        bindings()
        if self.token.cancelled:raise RunStopped('cancelled')
        bindings()
    def _issued(self,call):
        self._pure()
        if call is not self._call or call is None:raise RunStopped('intercept_call_unissued')
        context,action,identity=self._call_data
        slot=self._runner._terminal_dispatch_slot
        if type(slot) is not tuple or len(slot)!=6 or slot[0] is not self._slot or slot[1] is not context or slot[2] is not action or slot[3]!=self._thread:raise RunStopped("intercept_dispatch_unissued")
        if type(context) is not RuntimeContext or context.token is not self.token or _action(action)!=identity:raise RunStopped('intercept_call_changed')
        tool=slot[4]
        if (tool.name,tool.description,_runtime_json(tool.input_schema),tool.callback,tool.validate)!=slot[5] or tool.name!=action.name:raise RunStopped("intercept_tool_changed")
    def _check(self,call):
        self._issued(call)
        if self._lease is not None:self.owner._pins[0]._check_call(self._lease,self,call)
        self.agent._check_turn(self.session,self.turn);self.agent._binding(self.model)
        context,action,_=self._call_data;context.check();self._issued(call)
    def _call_grant(self,call):
        self._check(call);context,action,_=self._call_data
        if self.owner._pins[6](self.owner.decisions.scope,action) is not True:raise RunStopped('permission_denied')
        self._issued(call)
    def _audit_grant(self,call,record):
        self._check(call)
        if self.owner._pins[7](self.owner.decisions.scope,record) is not True:raise RunStopped('permission_denied')
        self._call_grant(call)
        return True
    @contextmanager
    def dispatch(self,runner,context,action,slot=None):
        self._pure()
        issued=getattr(runner,"_terminal_dispatch_slot",None)
        if slot is None or type(issued) is not tuple or len(issued)!=6 or issued[0] is not slot or issued[1] is not context or issued[2] is not action or issued[3]!=self._thread:raise RunStopped("intercept_dispatch_unissued")
        self._slot=slot
        if runner is not self._runner or self._call is not None:raise RunStopped('intercept_reentry')
        call=object();self._call=call;self._call_data=(context,action,_action(action));uncertain=False
        try:
            self._issued(call);self._lease=self.owner._pins[0]._register_call(self,call)
            self._call_grant(call)
            ic=self.owner._pins[1]
            enabled=ic.is_tool_enabled(action.name);self._check(call)
            if enabled:
                decision,matched=ic.match(action.name,action.arguments());self._check(call)
                if matched:
                    if decision.action not in ('allow','deny'):raise RunStopped('intercept_ask_unsupported')
                    status='allowed' if decision.action=='allow' else 'denied'
                    audit={'correlation':'unavailable','input_digest':digest_input(action.input_json),'captured_at':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),'initial_action':decision.action,'initial_reason':decision.message,'effective_action':decision.action,'execution_status':'unknown' if status=='allowed' else 'not_executed','rule_name':decision.rule_name}
                    record=DecisionRecord(action.name,action.input_json,decision.message,rule_id=decision.rule_id or None,audit_json=_json(audit),status=status)
                    self.owner._pins[2].create(record,request_authorize=lambda *args:self._audit_grant(call,record))
                    self._call_grant(call)
                    if decision.action=='deny':raise RunStopped('permission_denied')
            # Original normal grant remains independent and is refreshed after SQL.
            runner._authorize(context,action)
            self._call_grant(call)
            self._check(call)
            # Final ordinary authority follows all callback-bearing durable fences.
            runner._authorize(context,action)
            self._issued(call)
            yield
            self._check(call)
        except EffectUncertain:
            uncertain=True;self._uncertain=True;self.owner.unresolved=True
            raise
        except DecisionUncertain:
            uncertain=True;self._uncertain=True;self.owner.unresolved=True
            raise EffectUncertain('Durable interception outcome unconfirmed; do not retry') from None
        finally:
            if self._lease is not None:
                self.owner._pins[0]._release_call(self._lease,self,call,uncertain)
            self._lease=None;self._call=None;self._call_data=None
    def close(self):
        if get_ident()!=self._thread:raise RunStopped('intercept_foreign_thread')
        self._active=False
        if not self._uncertain:self.owner._session=None
