"""Opt-in task-local guard diagnostics, matching server.getAudit.

No database, global workspace aggregation, sequence/cursor, or source filter is
present upstream. The actual Guard retains at most 2000 chronological entries.
An explicit resolver may select its authorized active task for an absent selector;
None means no task and returns the source empty array. Missing capability is503.
Commands AND reasons are raw diagnostics and require separate explicit permission.
This does not grant tool execution permission or mutate/clear an audit trail.
Entry/attribution snapshots use the existing public Guard methods separately, as
upstream does; no atomic cross-snapshot consistency is claimed. Oversize output
fails closed, never silently truncates. No automatic application registration.
"""
from dataclasses import dataclass
import json
from urllib.parse import parse_qsl, urlsplit
from ..guard import Guard, AuditEntry
from .core import json_response, error_response


class AuditUnavailable(RuntimeError): pass
class AuditDenied(PermissionError): pass


@dataclass(frozen=True)
class AuditBinding:
    task_id: int
    revision: str
    audit_guard: Guard


class AuditService:
    def __init__(self, *, resolve=None, authorize_selection=None,
                 raw_authorize=None, current=None, lifecycle_guard=None):
        callbacks=(resolve,authorize_selection,raw_authorize,current,lifecycle_guard)
        if any(f is not None and not callable(f) for f in callbacks):
            raise ValueError('Explicit audit callbacks required')
        self.resolve,self.authorize_selection,self.raw_authorize,self.current,self.guard=callbacks
        self._callbacks=callbacks

    def register(self, router):
        router.add('GET','/api/audit',lambda request,params:self.handle(request))

    def _resources(self):
        values=(self.resolve,self.authorize_selection,self.raw_authorize,self.current,self.guard)
        if any(a is not b for a,b in zip(values,self._callbacks)) or any(f is None for f in values):
            raise AuditUnavailable()

    def handle(self,request):
        try:
            query=urlsplit(request.target).query
            if request.body or len(query)>256:raise ValueError()
            pairs=parse_qsl(query,keep_blank_values=True,strict_parsing=True,errors='strict',max_num_fields=2)
            if len(pairs)>1 or any(k!='task' for k,v in pairs):raise ValueError()
            selector=pairs[0][1] if pairs else ''
            if selector and (not selector.isascii() or not selector.isdecimal() or selector[0]=='0' or len(selector)>19 or not 0<int(selector)<2**63):raise ValueError()
            self._resources()
            if self.authorize_selection(request,selector) is not True:raise AuditDenied()
            self._resources()
            binding=self.resolve(selector)
            self._resources()
            if binding is None:return json_response(200,[])
            if (type(binding) is not AuditBinding or type(binding.task_id) is not int
                or not 0<binding.task_id<2**63 or type(binding.revision) is not str
                or not binding.revision or len(binding.revision)>256 or '\0' in binding.revision
                or type(binding.audit_guard) is not Guard):raise AuditUnavailable()
            if selector and int(selector)!=binding.task_id:raise AuditUnavailable()
            identity=(binding.task_id,binding.revision,binding.audit_guard)
            def check():
                self._resources()
                if (binding.task_id,binding.revision,binding.audit_guard)!=identity:raise AuditUnavailable()
                if self.current(binding) is not True:raise AuditDenied()
                self._resources()
                if (binding.task_id,binding.revision,binding.audit_guard)!=identity:raise AuditUnavailable()
            def allow():
                check()
                if self.raw_authorize(request,binding) is not True:raise AuditDenied()
                check()
            with self._callbacks[4](binding):
                allow()
                entries=binding.audit_guard.audit()
                if type(entries) is not list or len(entries)>2000:raise AuditUnavailable()
                counts=binding.audit_guard.attributions()
                if type(counts) is not dict or set(counts)-{'ok','error','blocked'}:raise AuditUnavailable()
                if any(type(v) is not int or not 0<=v<2**63 for v in counts.values()):raise AuditUnavailable()
                output=[];total=0
                for entry in entries:
                    if type(entry) is not AuditEntry or type(entry.ts) is not int or not 0<=entry.ts<2**63:raise AuditUnavailable()
                    if entry.action not in ('allow','block'):raise AuditUnavailable()
                    row={'ts':entry.ts,'tool':entry.tool,'action':entry.action}
                    for key in ('tool','action','reason','command'):
                        value=getattr(entry,key)
                        if type(value) is not str or len(value)>65536:raise AuditUnavailable()
                        size=len(value.encode('utf-8'))
                        if size>65536:raise AuditUnavailable()
                        total+=size
                        if total>1_048_576:raise AuditUnavailable()
                        if key in ('reason','command') and value:row[key]=value
                    output.append(row)
                payload={'entries':output,'attributions':counts}
                # JSON escaping expands text. Bound final wire bytes too.
                if len(json.dumps(payload,ensure_ascii=True).encode())>1_048_576:raise AuditUnavailable()
                allow()
            check()
            return json_response(200,payload)
        except AuditDenied:return error_response(403,'Raw task audit access denied')
        except AuditUnavailable:return error_response(503,'Task audit capability unavailable')
        except (ValueError,TypeError,UnicodeError):return error_response(400,'Invalid audit selector')
        except Exception:return error_response(500,'Task audit read failed')
