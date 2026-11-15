"""Opt-in MCP metadata routes; never connects, discovers, launches or refreshes.

Source wire routes/fields retained for list/save/delete and cached tools. Unlike
Go, creation never auto-discovers enabled servers; refresh is NOT registered.
Stored command/args/env/header/URL values are opaque credential-bearing data.
Metadata redacts them, including opaque env/header keys. Cached tool labels and
descriptions also default to redacted placeholders, retaining counts only; redacted public records cannot be blindly saved back.

All routes require explicit authorize(request,action,id,proposal). Writes also
require mutation_authorize, idle(id)->True, and synchronous synchronize(action,id)
->True after commit. DELETE additionally requires delete_authorize because it
removes server config, cached tools (FK cascade) and agent_visibility links.
The shared guard/current authority must fence every catalog consumer/config edit
and confirm no active MCP client can use changed metadata. No default policy.
Sync failure returns committed/reconciliation; never retries writes or launches
clients. enabled/insecure storage is NOT runtime permission or a TLS override.
"""
from dataclasses import asdict
from copy import deepcopy
import json,re
from urllib.parse import urlsplit
from .core import json_response,error_response
from ..db.mcp_catalog import MCPCatalogStore,MCPServer,MCPInvalid,MCPMissing,MCPUnavailable,validate,REDACTED

class Denied(RuntimeError):pass
class Conflict(RuntimeError):pass
class Unavailable(RuntimeError):pass

def _pairs(pairs):
    result={}
    for k,v in pairs:
        if k in result:raise MCPInvalid('Duplicate JSON key')
        result[k]=v
    return result

def parse_server(body):
    obj=json.loads(body.decode('utf-8'),object_pairs_hook=_pairs,parse_constant=lambda _:(_ for _ in ()).throw(MCPInvalid()))
    if type(obj) is not dict or set(obj)-set(MCPServer.__dataclass_fields__):raise MCPInvalid('Invalid MCP fields')
    obj.pop('tools',None) # discovery cache is not user writable through save
    return validate(MCPServer(**obj))

def _cached_metadata(tools):
    # Remote servers can put literal, partial, or encoded credentials anywhere
    # in tool labels/descriptions. Do not guess token syntax or substring-scrub.
    # Public route retains cache count only, with explicit unreadable placeholders.
    # A future safe detail projection needs a separate explicit host contract.
    return [dict(name=REDACTED,description=REDACTED) for _ in tools]

class MCPCatalogService:
    def __init__(self,store,*,authorize,current,lifecycle_guard,mutation_authorize=None,delete_authorize=None,idle=None,synchronize=None):
        if not isinstance(store,MCPCatalogStore) or any(not callable(f) for f in (authorize,current,lifecycle_guard)):raise ValueError('Explicit catalog and host authority required')
        if any(f is not None and not callable(f) for f in (mutation_authorize,delete_authorize,idle,synchronize)):raise ValueError('Invalid lifecycle policy')
        self.store,self._executor=store,store.executor
        self.authorize,self.current,self.guard=authorize,current,lifecycle_guard
        self.mutate,self.delete,self.idle,self.sync=mutation_authorize,delete_authorize,idle,synchronize
    def register(self,router):
        for method,path,action in (('GET','/api/mcp','list'),('POST','/api/mcp','save'),('DELETE','/api/mcp/{id}','delete'),('GET','/api/mcp/{id}/tools','tools')):
            router.add(method,path,lambda r,p,a=action:self.handle(a,r,p))
    def _check(self):
        if self.store.executor is not self._executor:raise Unavailable()
        if self.current() is not True:raise Conflict()
        if self.store.executor is not self._executor:raise Unavailable()
    def _policy(self,fn,*args):
        if not callable(fn):raise Unavailable()
        if fn(*args) is not True:raise Denied()
    def handle(self,action,request,params):
        committed=False;attempted=False
        try:
            if action not in ('list','save','delete','tools') or urlsplit(request.target).query:raise MCPInvalid()
            if len(request.body)>1048576:return error_response(413,'Request body too large')
            id=None;server=None;encoded=None
            if action=='save':
                server=parse_server(request.body);id=server.id
                encoded=json.dumps(asdict(server),allow_nan=False).encode('utf-8')
            elif request.body:raise MCPInvalid()
            if action in ('delete','tools'):
                raw=params.get('id')
                if not isinstance(raw,str) or not re.fullmatch(r'[1-9][0-9]{0,18}',raw) or int(raw)>=2**63:raise MCPInvalid()
                id=int(raw)
            mutation=action in ('save','delete')
            if mutation and any(not callable(f) for f in (self.mutate,self.idle,self.sync)):raise Unavailable()
            if action=='delete' and not callable(self.delete):raise Unavailable()
            with self.guard():
                self._check();self._policy(self.authorize,request,action,id,deepcopy(server));self._check()
                if mutation:
                    self._policy(self.mutate,request,action,id,deepcopy(server));self._check()
                    if action=='delete':self._policy(self.delete,request,action,id,None);self._check()
                    if self.idle(id) is not True:raise Conflict()
                    self._check()
                if action=='list':
                    output={'servers':[row.public_dict() for row in self.store.list()]}
                elif action=='tools':
                    row=self.store.get(id)
                    if row is None:raise MCPMissing()
                    output={'tools':_cached_metadata(self.store.tools(id))}
                elif action=='save':
                    attempted=True;id=self.store.save(parse_server(encoded));committed=True
                    if self.sync(action,id) is not True:raise Unavailable()
                    output={'id':id}
                else:
                    attempted=True;self.store.delete(id);committed=True
                    if self.sync(action,id) is not True:raise Unavailable()
                    output={'deleted':id}
                self._check();response=json_response(200,output)
                if len(response.body)>2097152:raise Unavailable()
                return response
        except Exception as error:
            if committed:return json_response(503,dict(error='MCP metadata committed; lifecycle synchronization needs reconciliation',committed=True,needs_reconciliation=True))
            if attempted and not isinstance(error,(MCPMissing,MCPInvalid)):return json_response(503,dict(error='MCP metadata write outcome uncertain; reconcile before retrying',committed=None,needs_reconciliation=True))
            if isinstance(error,Denied):return error_response(403,'MCP catalog access denied')
            if isinstance(error,Conflict):return error_response(409,'MCP catalog lifecycle changed or client active')
            if isinstance(error,(Unavailable,MCPUnavailable)):return error_response(503,'MCP catalog capability unavailable')
            if isinstance(error,MCPMissing):return error_response(404,'MCP server not found')
            if isinstance(error,(ValueError,TypeError,UnicodeError)):return error_response(400,'Invalid MCP catalog request')
            return error_response(500,'MCP catalog operation failed')
