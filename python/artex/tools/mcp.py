"""Explicit Norma-style MCP Tool bridge over an already configured HTTP client.

No initialize/list/refresh/close, process, discovery, credential resolution or
registration occurs. Host supplies reviewed metadata and exact session identity
(empty is explicit stateless operation), plus a guard shared with client/scope
mutation. Descriptions/schema annotations are untrusted, not grants. Client HTTP
transport remains caller-injected and owns deadlines/cancellation while blocked;
checks here are cooperative before/after exchange, not a network sandbox.

Source mcp/mcp.go names mcp__server__tool and concatenates text. Only text content
is supported; images/audio/resources/structuredContent are rejected, not silently
lost. Runtime Tool currently cannot express a nonterminal is_error result, so
remote errors raise RunStopped('mcp_remote_error'). Protocol/transport failures
or unsupported post-call responses raise mcp_outcome_uncertain: effects may have
occurred, never auto-retry. No raw remote error/body enters exception messages.

Schema subset: object/properties/required/additionalProperties boolean; array/items;
scalar type, enum/const, numeric min/max, item/string length bounds and annotations.
No references (even local), regex, formats, combinators, coercion or default filling.
Unknown keywords fail construction. Output/argument/schema bounds are explicit.
"""
from copy import deepcopy
from dataclasses import asdict
import json,math,re
from ..mcphttp import Client,RemoteTool,tool_name
from ..agent.graph_context import ContextScope
from ..agent.runtime import Tool,Action,Grant,RunStopped
from ..db.tool_catalog import CatalogTool

class MCPBridgeInvalid(ValueError):pass


def _json(value,limit):
    try:
        raw=json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':'))
        if len(raw.encode('utf-8'))>limit:raise ValueError()
        return raw
    except (ValueError,TypeError,UnicodeError,RecursionError):raise MCPBridgeInvalid('Invalid or oversized MCP value') from None


def _schema(schema,depth=0,budget=None):
    if budget is None:budget=[512]
    budget[0]-=1
    if depth>16 or budget[0]<0 or type(schema) is not dict:raise MCPBridgeInvalid('Bounded object schema required')
    allowed={'type','properties','required','additionalProperties','items','enum','const','minimum','maximum','minLength','maxLength','minItems','maxItems','description','title','default'}
    if set(schema)-allowed:raise MCPBridgeInvalid('Unsupported MCP schema keyword')
    typ=schema.get('type')
    if typ not in ('object','array','string','integer','number','boolean','null'):raise MCPBridgeInvalid('Explicit supported schema type required')
    for k in ('description','title'):
        if k in schema and type(schema[k]) is not str:raise MCPBridgeInvalid('Invalid schema annotation')
    if 'enum' in schema and (type(schema['enum']) is not list or not 1<=len(schema['enum'])<=128):raise MCPBridgeInvalid('Bounded enum required')
    for k in ('minLength','maxLength','minItems','maxItems'):
        if k in schema and (type(schema[k]) is not int or not 0<=schema[k]<=1048576):raise MCPBridgeInvalid('Invalid schema bound')
    for k in ('minimum','maximum'):
        if k in schema and (type(schema[k]) not in (int,float) or not math.isfinite(schema[k])):raise MCPBridgeInvalid('Invalid numeric bound')
    if typ=='object':
        props=schema.get('properties',{});required=schema.get('required',[])
        if type(props) is not dict or type(required) is not list or any(type(k) is not str for k in props) or any(type(k) is not str or k not in props for k in required) or len(set(required))!=len(required):raise MCPBridgeInvalid('Invalid object schema')
        if type(schema.get('additionalProperties',True)) is not bool:raise MCPBridgeInvalid('Schema-valued additionalProperties unsupported')
        for sub in props.values():_schema(sub,depth+1,budget)
    elif set(schema)&{'properties','required','additionalProperties'}:raise MCPBridgeInvalid('Object keywords require object type')
    if typ=='array':
        if 'items' not in schema:raise MCPBridgeInvalid('Array item schema required')
        _schema(schema['items'],depth+1,budget)
    elif 'items' in schema:raise MCPBridgeInvalid('Items require array type')


def _validate(value,schema,depth=0):
    if depth>32:raise MCPBridgeInvalid('Argument depth exceeded')
    typ=schema['type']
    valid={'object':type(value) is dict,'array':type(value) is list,'string':type(value) is str,'integer':type(value) is int,'number':type(value) in (int,float),'boolean':type(value) is bool,'null':value is None}[typ]
    if not valid:raise MCPBridgeInvalid('MCP argument type mismatch')
    if 'enum' in schema and not any(type(value) is type(v) and value==v for v in schema['enum']):raise MCPBridgeInvalid('MCP enum mismatch')
    if 'const' in schema and (type(value) is not type(schema['const']) or value!=schema['const']):raise MCPBridgeInvalid('MCP const mismatch')
    if typ=='object':
        props=schema.get('properties',{})
        if any(k not in value for k in schema.get('required',[])) or any(type(k) is not str for k in value):raise MCPBridgeInvalid('Missing required MCP argument')
        if schema.get('additionalProperties',True) is False and set(value)-set(props):raise MCPBridgeInvalid('Unknown MCP argument')
        for k,v in value.items():
            if k in props:_validate(v,props[k],depth+1)
    if typ=='array':
        for item in value:_validate(item,schema['items'],depth+1)
    if typ in ('string','array'):
        suffix='Length' if typ=='string' else 'Items'
        if len(value)<schema.get('min'+suffix,0) or len(value)>schema.get('max'+suffix,1048576):raise MCPBridgeInvalid('MCP length bound')
    if typ in ('integer','number') and (value<schema.get('minimum',-math.inf) or value>schema.get('maximum',math.inf)):raise MCPBridgeInvalid('MCP numeric bound')


class MCPTools:
    def __init__(self,client,remote_tools,scope,*,session_id,gate,current,lifecycle_guard,max_argument_bytes=65536,max_output_bytes=65536):
        if type(client) is not Client or type(scope) is not ContextScope:raise MCPBridgeInvalid('Actual client and scope required')
        if any(type(v) is not int or not 0<v<2**63 for v in (scope.task_id,scope.exploration_id,scope.run_id)) or type(scope.revision) is not str or not scope.revision or len(scope.revision)>1024:raise MCPBridgeInvalid('Exact bounded scope required')
        if not all(callable(f) for f in (gate,current,lifecycle_guard)):raise MCPBridgeInvalid('Host gate/current/guard required')
        if type(session_id) is not str or session_id!=client.session_id or len(session_id)>4096:raise MCPBridgeInvalid('Exact existing session required')
        if type(remote_tools) not in (list,tuple) or not 1<=len(remote_tools)<=100:raise MCPBridgeInvalid('Explicit bounded remote metadata required')
        for value in (max_argument_bytes,max_output_bytes):
            if type(value) is not int or not 1<=value<=1048576:raise MCPBridgeInvalid('Invalid MCP byte bound')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,48}',client.server):raise MCPBridgeInvalid('Unsupported MCP server name')
        self.client,self.scope=client,deepcopy(scope)
        self._client,self._scope=client,self.scope
        self._server,self._url,self._transport,self._headers,self._session=client.server,client.url,client.transport,deepcopy(client.headers),session_id
        self.gate,self.current,self.guard=gate,current,lifecycle_guard
        self.max_arguments,self.max_output=max_argument_bytes,max_output_bytes
        self._tools={}
        for remote in remote_tools:
            if type(remote) is not RemoteTool or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',remote.name) or type(remote.description) is not str:raise MCPBridgeInvalid('Invalid remote metadata')
            if len(remote.description.encode('utf-8'))>16384:raise MCPBridgeInvalid('Description exceeds bound')
            schema=deepcopy(remote.input_schema);_json(schema,65536);_schema(schema)
            if schema['type']!='object':raise MCPBridgeInvalid('Tool arguments must be object')
            name=tool_name(client.server,remote.name)
            if name in self._tools:raise MCPBridgeInvalid('Duplicate remote tool')
            self._tools[name]=(remote.name,remote.description,schema)

    def _fence(self,context):
        if not isinstance(context,dict) or not callable(getattr(context,'check',None)):raise RunStopped('scope_mismatch')
        def exact():
            c=self._client
            if self.client is not c or self.scope is not self._scope or c.server!=self._server or c.url!=self._url or c.transport is not self._transport or c.headers!=self._headers or c.session_id!=self._session or c._closed:raise RunStopped('mcp_binding_changed')
            for k,v in asdict(self._scope).items():
                if type(context.get(k)) is not type(v) or context[k]!=v:raise RunStopped('scope_mismatch')
        context.check();exact()
        if self.current(self._scope) is not True:raise RunStopped('stale_context')
        context.check();exact()

    def validate(self,name,args):
        if name not in self._tools:raise MCPBridgeInvalid('Unknown MCP tool')
        _json(args,self.max_arguments);_validate(args,self._tools[name][2]);return True

    def call(self,name,context,args):
        self.validate(name,args);encoded=_json(args,self.max_arguments);args=json.loads(encoded)
        with self.guard(self._scope),self._client._lock:
            self._fence(context)
            grant=self.gate(context,Action('tool',name,encoded));self._fence(context)
            if not isinstance(grant,Grant) or not all(v is True for v in (grant.permission,grant.in_scope,grant.approved)):raise RunStopped('permission_denied')
            try:result=self._client.rpc('tools/call',{'name':self._tools[name][0],'arguments':args})
            except Exception:raise RunStopped('mcp_outcome_uncertain') from None
            self._fence(context)
            try:
                _json(result,self.max_output)
                if type(result) is not dict or set(result)-{'content','isError','_meta'} or type(result.get('content')) is not list or len(result['content'])>256 or type(result.get('isError',False)) is not bool:raise MCPBridgeInvalid('Unsupported response')
                chunks=[]
                for block in result['content']:
                    if type(block) is not dict or set(block)-{'type','text','annotations','_meta'} or block.get('type')!='text' or type(block.get('text')) is not str:raise MCPBridgeInvalid('Unsupported content')
                    chunks.append(block['text'])
                output=''.join(chunks)
            except Exception:raise RunStopped('mcp_outcome_uncertain') from None
            if result.get('isError',False):raise RunStopped('mcp_remote_error')
            self._fence(context)
        self._fence(context)
        return output

    def tools(self):
        return tuple(Tool(name,description,deepcopy(schema),lambda c,a,n=name:self.call(n,c,a),lambda a,n=name:self.validate(n,a)) for name,(_,description,schema) in self._tools.items())

    def catalog(self,*,agents):
        if type(agents) not in (list,tuple) or not 1<=len(agents)<=100 or any(type(a) is not str or not re.fullmatch(r'[a-z][a-z0-9_]{0,127}',a) for a in agents) or len(set(agents))!=len(agents):raise MCPBridgeInvalid('Explicit unique agent bindings required')
        return tuple(CatalogTool(t.name,True,t.description,deepcopy(t.input_schema),list(agents),True,'builtin',{},False) for t in self.tools())
