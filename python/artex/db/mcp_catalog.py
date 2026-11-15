"""Persisted MCP catalog only, source db/config.go. NEVER launches/connects.

Private MCPServer DTO includes command/args/env(URL headers)/URL and must not be
serialized or logged. public_dict deliberately redacts all executable/config
values and opaque env/header keys, not just guessed secret keys; edit roundtrips require genuine replacement
values (redaction markers are rejected). enabled/insecure are stored metadata,
not runtime permission or evidence of connection. Cached tool names/descriptions are untrusted and may echo credentials; public
projections retain counts but redact both. Private cached reads are host-only. Errors/repr do not expose configuration.
No tool discovery/cache replacement, visibility granting or automatic seeds.
"""
from dataclasses import dataclass,field
import json
from functools import wraps
from .core import Query,int64
from .tool_catalog import value_json

REDACTED='[redacted]'
COLUMNS="id,name,transport,COALESCE(command,''),args,env,COALESCE(url,''),enabled,insecure"
class MCPInvalid(ValueError):pass
class MCPMissing(LookupError):pass
class MCPUnavailable(RuntimeError):pass

@dataclass(repr=False)
class MCPServer:
    id:int=0
    name:str=''
    transport:str='stdio'
    command:str=''
    args:list=field(default_factory=list)
    env:dict=field(default_factory=dict)
    url:str=''
    enabled:bool=False
    insecure:bool=False
    tools:list=field(default_factory=list)
    def __repr__(self):return f'MCPServer(id={self.id}, configuration=<redacted>)'
    def public_dict(self):
        result=dict(id=self.id,name=self.name,transport=self.transport,args=[REDACTED for _ in self.args],env={f'[redacted:{i}]':REDACTED for i,_ in enumerate(self.env,1)},enabled=self.enabled,insecure=self.insecure)
        if self.command:result['command']=REDACTED
        if self.url:result['url']=REDACTED
        if self.tools:result['tools']=[REDACTED for _ in self.tools]
        return result

def _id(value,zero=False):
    if int64(value,'MCP ID')<(0 if zero else 1):raise MCPInvalid('Invalid MCP ID')
    return value

def _text(value,limit):
    if not isinstance(value,str) or '\0' in value or len(value.encode('utf-8'))>limit:raise MCPInvalid('Invalid bounded MCP text')
    return value

def validate(server):
    if not isinstance(server,MCPServer):raise MCPInvalid('MCP server required')
    _id(server.id,True)
    if not _text(server.name,256).strip() or server.transport not in ('stdio','http','sse'):raise MCPInvalid('Invalid MCP name or transport')
    if type(server.enabled) is not bool or type(server.insecure) is not bool:raise MCPInvalid('Boolean flags required')
    for v in (server.command,server.url):
        _text(v,16384)
        if REDACTED in v:raise MCPInvalid('Redacted placeholders cannot be saved')
    if type(server.args) is not list or len(server.args)>256 or type(server.env) is not dict or len(server.env)>256:raise MCPInvalid('Bounded args/env required')
    for v in server.args:
        _text(v,16384)
        if REDACTED in v:raise MCPInvalid('Redacted placeholders cannot be saved')
    for k,v in server.env.items():
        if not _text(k,256):raise MCPInvalid('Environment/header key required')
        if '[redacted' in k.lower():raise MCPInvalid('Redacted keys cannot be saved')
        _text(v,65536)
        if REDACTED in v:raise MCPInvalid('Redacted placeholders cannot be saved')
    if len(json.dumps([server.args,server.env]).encode('utf-8'))>1048576:raise MCPInvalid('Configuration exceeds bound')
    # Source permits incomplete disabled placeholders. No connection validation,
    # command evaluation, URL resolution or TLS-security changes are performed.
    return server

def _row(row):
    if len(row)!=9:raise MCPUnavailable('Invalid MCP row')
    values=list(row);values[4]=value_json(values[4]);values[5]=value_json(values[5])
    result=MCPServer(*values)
    # Do not reject legacy literal placeholders just to read metadata, but ensure
    # public DTO has the expected container/text shapes.
    if type(result.args) is not list or type(result.env) is not dict:raise MCPUnavailable('Invalid MCP configuration shape')
    return result

def _safe(method):
    @wraps(method)
    def wrapped(*args,**kwargs):
        try:return method(*args,**kwargs)
        except (MCPInvalid,MCPMissing,MCPUnavailable):raise
        except Exception:raise MCPUnavailable('MCP catalog storage operation failed') from None
    return wrapped

class MCPCatalogStore:
    def __init__(self,executor):self.executor=executor
    @_safe
    def list(self,limit=1000):
        if type(limit) is not int or not 1<=limit<=1000:raise MCPInvalid('Invalid list bound')
        rows=self.executor.fetch_all(Query('SELECT '+COLUMNS+' FROM mcp_servers ORDER BY id LIMIT $1',(limit+1,)))
        if len(rows)>limit:raise MCPUnavailable('MCP catalog exceeds bound')
        servers=[_row(r) for r in rows]
        for server in servers:server.tools=[t['name'] for t in self.tools(server.id)]
        return servers
    @_safe
    def get(self,id):
        rows=self.executor.fetch_all(Query('SELECT '+COLUMNS+' FROM mcp_servers WHERE id=$1',(_id(id),)))
        if len(rows)>1:raise MCPUnavailable('Invalid MCP identity')
        return _row(rows[0]) if rows else None
    @_safe
    def tools(self,id):
        rows=self.executor.fetch_all(Query("SELECT tool_name,COALESCE(description,'') FROM mcp_tools_cache WHERE server_id=$1 ORDER BY tool_name LIMIT 1001",(_id(id),)))
        if len(rows)>1000:raise MCPUnavailable('Cached tools exceed bound')
        result=[]
        for name,description in rows:
            _text(name,256);_text(description,65536);result.append(dict(name=name,description=description))
        return result
    @_safe
    def save(self,server):
        validate(server)
        params=(server.name,server.transport,server.command,json.dumps(server.args,separators=(',',':')),json.dumps(server.env,separators=(',',':')),server.url,server.enabled,server.insecure)
        with self.executor.transaction() as tx:
            if server.id:
                rows=tx.fetch_all(Query('SELECT id FROM mcp_servers WHERE id=$1 FOR UPDATE',(server.id,)))
                if rows!=[(server.id,)] and rows!=[[server.id]]:raise MCPMissing('MCP not found')
                if tx.execute(Query("UPDATE mcp_servers SET name=$1,transport=$2,command=NULLIF($3,''),args=$4,env=$5,url=NULLIF($6,''),enabled=$7,insecure=$8 WHERE id=$9",params+(server.id,)))!=1:raise MCPUnavailable('MCP update unconfirmed')
                return server.id
            rows=tx.fetch_all(Query("INSERT INTO mcp_servers(name,transport,command,args,env,url,enabled,insecure) VALUES ($1,$2,NULLIF($3,''),$4,$5,NULLIF($6,''),$7,$8) RETURNING id",params))
            if len(rows)!=1:raise MCPUnavailable('MCP creation unconfirmed')
            return _id(rows[0][0])
    @_safe
    def delete(self,id):
        _id(id)
        with self.executor.transaction() as tx:
            rows=tx.fetch_all(Query('SELECT id FROM mcp_servers WHERE id=$1 FOR UPDATE',(id,)))
            if rows!=[(id,)] and rows!=[[id]]:raise MCPMissing('MCP not found')
            tx.execute(Query("DELETE FROM agent_visibility WHERE resource_kind='mcp' AND resource_id=$1",(id,)))
            if tx.execute(Query('DELETE FROM mcp_servers WHERE id=$1',(id,)))!=1:raise MCPUnavailable('MCP deletion unconfirmed')
