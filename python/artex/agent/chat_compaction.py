"""Opt-in ChatAgent working-request preparation, never transcript rewriting.

Summary usage is charged immediately to the owning runtime ledger, independently
of provider-record persistence. Main-call usage is returned through its ordinary
path, so no actual provider call is recorded or counted twice. Streaming summaries charge validated cumulative usage increments as observed,
including before a stale/cancel fence. Failed streams may lack final usage;
unknown cost is not estimated as billed usage and no replay is attempted.
These are post-call reported-usage limits, not invoice caps: input cost is only
known afterward. usage_accounted is a trusted host adapter mode, never model input. System/tool policy fields are retained byte-for-byte in the copied
request. Callers must keep required verbatim history policy/skills in the tail.
"""
from copy import deepcopy
from dataclasses import dataclass
import json
from .compaction import Budget, WorkingCompactor
from .runtime import RunStopped


@dataclass(frozen=True)
class ChatCompaction:
    budget:Budget
    max_request_bytes:int=4*1024*1024
    max_summary_bytes:int=65536
    def __post_init__(self):
        if not isinstance(self.budget,Budget):raise ValueError('Explicit context budget required')
        if any(type(v) is not int or v<=0 for v in (self.max_request_bytes,self.max_summary_bytes)):
            raise ValueError('Positive compaction bounds required')
    def prepare(self,context,request,*,provider,gate,current,usage_accounted=False):
        if type(usage_accounted) is not bool:raise ValueError('Explicit accounting mode required')
        if not callable(getattr(context,'charge_auxiliary_usage',None)):
            raise ValueError('Owning runtime usage ledger required')
        class AccountedSummary:
            def complete(inner,ctx,summary_request):
                # Fail closed instead of raising the granted summary output cap
                # above the run's remaining token allowance.
                if ctx.remaining_tokens<summary_request['max_tokens']:
                    raise RunStopped('token_budget')
                result=provider.complete(ctx,summary_request)
                if not usage_accounted:ctx.charge_auxiliary_usage(result[2])
                return result
        static={k:deepcopy(v) for k,v in request.items() if k not in ('messages','max_tokens')}
        static_tokens=(len(json.dumps(static,ensure_ascii=False,allow_nan=False).encode('utf-8'))+2)//3+40
        compacted=WorkingCompactor(AccountedSummary(),gate=gate,current=current,budget=self.budget,
            max_request_bytes=self.max_request_bytes,max_summary_bytes=self.max_summary_bytes).compact(
                context,request['messages'],static_tokens=static_tokens)
        prepared=deepcopy(request)
        prepared['messages']=compacted.messages
        if context.remaining_tokens<=0:raise RunStopped('token_budget')
        prepared['max_tokens']=min(prepared['max_tokens'],context.remaining_tokens)
        return prepared
