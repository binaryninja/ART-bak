"""Host-configured read-only filesystem, command, HTTP and MCP adapters."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shlex
import ssl
from urllib.parse import quote,urlsplit
from ..agent.runtime import Action,Grant,Tool
from ..agent.tool_ledger import ToolLedger
from ..agent.graph_context import ContextScope
from ..db.tool_catalog import CatalogTool,_structure
from ..tools.filesystem import ScopedFilesystem
from ..tools.http import ScopedHTTP
from ..tools.process import BashTool,PosixRunner
from ..tools.registry import ToolRegistry
from ..tools.mcp import MCPTools,_schema,_validate
from ..mcphttp import Client,HTTPResponse,tool_name
from .definitions import instance_origin

EMPTY={'type':'object','properties':{},'additionalProperties':False}
SEARCH={'type':'object','properties':{'query':{'type':'string','maxLength':200}},'additionalProperties':False}
EXECUTE={'type':'object','properties':{'name':{'type':'string','maxLength':128},'arguments':{'type':'object'}},'required':['name','arguments'],'additionalProperties':False}

def row(name,description,schema,*,deferred=False):return CatalogTool(name,True,description,deepcopy(schema),['worker'],True,'builtin',{},deferred)
def valid(schema,args):_validate(args,schema);return True
def executable(path):
    p=Path(path)
    if not p.is_absolute() or p.resolve()!=p or not p.is_file():raise ValueError('Canonical executable or script required')
    return hashlib.sha256(p.read_bytes()).hexdigest()

class ExtensionHost:
    def __init__(self,root,allowed_origins,manifest=None):
        self.root=Path(root);self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
        if not self.root.is_absolute() or self.root.resolve()!=self.root:raise ValueError('Canonical artifact root required')
        self.allowed=frozenset(instance_origin(x) for x in allowed_origins)
        self.specs={};self.hashes={};self.secrets=[];self._headers={}
        if manifest is not None:
            p=Path(manifest)
            if not p.is_absolute() or p.resolve()!=p or p.stat().st_size>1048576:raise ValueError('Canonical bounded extension manifest required')
            data=json.loads(p.read_text())
            if not isinstance(data,dict) or set(data)!={'version','tools'} or data['version']!=1 or not isinstance(data['tools'],list) or len(data['tools'])>64:raise ValueError('Invalid extension manifest')
            for spec in data['tools']:self._admit(spec)
        with ScopedFilesystem(str(self.root)) as fs:self.files=tuple(t for t in fs.tools() if t.name in ('Read','LS'))
        self.catalog=tuple(row(t.name,t.description,t.input_schema) for t in self.files)+(
            row('SearchExtraTools','Find configured read-only tools and their exact argument schemas.',SEARCH),
            row('ExecuteExtraTool','Execute one configured read-only tool by its discovered name.',EXECUTE),
        )+tuple(row(name,s['description'],s['schema'],deferred=True) for name,s in self.specs.items())
        self.revision=hashlib.sha256(json.dumps([str(self.root),self.specs,self.hashes],sort_keys=True).encode()).hexdigest()

    def _admit(self,value):
        spec=deepcopy(value);name=spec.get('name','');kind=spec.get('kind')
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,63}',name) or name in self.specs or kind not in ('command','script','http','mcp') or spec.get('read_only') is not True:raise ValueError('Named read-only extension required')
        if not isinstance(spec.get('description'),str) or len(spec['description'])>4096:raise ValueError('Bounded description required')
        _schema(spec.get('schema'));schema=spec['schema']
        if schema['type']!='object' or schema.get('additionalProperties') is not False or set(schema.get('properties',{}))&{'origin','host'}:raise ValueError('Closed input schema required')
        origins=spec.get('allowed_origins')
        if not isinstance(origins,list) or not origins or not set(origins)<=self.allowed:raise ValueError('Extension target scope exceeds host scope')
        run_for=spec.get('run_for',[])
        from .definitions import DEFINITIONS
        if not isinstance(run_for,list) or set(run_for)-set(DEFINITIONS):raise ValueError('Unknown automatic assessment binding')
        if run_for:_validate(spec.get('arguments',{}),schema)
        if kind in ('command','script'):
            argv=spec.get('argv')
            if not isinstance(argv,list) or not 1<=len(argv)<=64 or any(not isinstance(x,str) or '\0' in x or len(x)>4096 for x in argv):raise ValueError('Explicit bounded argv required')
            self.hashes[argv[0]]=executable(argv[0])
            if kind=='script':
                if len(argv)<2:raise ValueError('Explicit interpreter and script required')
                self.hashes[argv[1]]=executable(argv[1])
            for arg in argv:
                if set(re.findall(r'\{([A-Za-z_][A-Za-z0-9_]*)\}',arg))-set(schema.get('properties',{}))-{'origin','host'}:raise ValueError('Unknown argument placeholder')
            env=spec.get('environment',{})
            if not isinstance(env,dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in env.items()):raise ValueError('Explicit process environment required')
        elif kind=='http':
            if spec.get('method','GET')!='GET' or not isinstance(spec.get('path'),str) or not spec['path'].startswith('/') or spec['path'].startswith('//'):raise ValueError('Named GET path required')
            path=urlsplit(spec['path'])
            if path.scheme or path.netloc or path.fragment or '{' in path.path or '}' in path.path or spec.get('authentication','none') not in ('none','assessment'):raise ValueError('Fixed HTTP path and declared authentication required')
        else:
            p=urlsplit(spec.get('url',''))
            if p.scheme!='https' or not p.hostname or p.username or p.password or p.fragment:raise ValueError('Explicit HTTPS MCP endpoint required')
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,48}',spec.get('server','')) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',spec.get('remote_tool','')):raise ValueError('Explicit MCP tool required')
            _schema(spec.get('remote_schema'))
            if not isinstance(spec.get('target_parameter'),str) or spec['target_parameter'] not in spec['remote_schema'].get('properties',{}):raise ValueError('MCP target binding required')
            if spec['target_parameter'] in schema.get('properties',{}):raise ValueError('MCP target is host-owned')
            for envname in spec.get('headers_env',{}).values():
                if not isinstance(envname,str) or envname not in os.environ:raise ValueError('Missing MCP credential environment')
                self.secrets.append(os.environ[envname])
            self._headers[name]={header:os.environ[envname] for header,envname in spec.get('headers_env',{}).items()}
        self.specs[name]=spec

    def current(self):
        return all(executable(path)==digest for path,digest in self.hashes.items())

    def required(self,definition,origin,enabled):
        required={name:deepcopy(spec.get('arguments',{})) for name,spec in self.specs.items() if definition in spec.get('run_for',[]) and origin in spec['allowed_origins']}
        if required and not (set(required)|{'ExecuteExtraTool'})<=set(enabled):raise PermissionError('Required assessment extension is disabled')
        return required

    def redact(self,text):
        for value in self.secrets:
            if value:text=text.replace(value,'[redacted]')
        return text

    def import_text(self,text,*,redact):
        from .discovery import discover
        candidates=discover(text)
        try:document=json.loads(text)
        except ValueError:document=None
        if isinstance(document,dict) and 'url' in document:
            document.pop('username',None);document.pop('password',None);text=json.dumps(document,indent=2)
        else:text=re.sub(r'(?im)^\s*(?:user(?:name)?|password|pass)\s*[:=].*$','[credential stored privately]',text)
        for candidate in candidates:
            if candidate['password']:text=text.replace(candidate['password'],'[credential stored privately]')
        data=redact(text).encode();name=hashlib.sha256(data).hexdigest()+'.txt'
        path=self.root/name
        try:fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        except FileExistsError:return name
        with os.fdopen(fd,'wb') as file:file.write(data);file.flush();os.fsync(file.fileno())
        return name

    def bind(self,runtime,scope,record,check,guard,events):
        from .discovery import DiscoveryDispatcher
        def owned():
            if not self.current():raise PermissionError('Extension executable changed')
            return True
        def authorize_read(context,path):check(context);return True
        observer=DiscoveryDispatcher(runtime).read_observer(authorize=authorize_read)
        def filesystem(name,context,args):
            with guard():
                check(context)
                with ScopedFilesystem(str(self.root),max_bytes=262144,read_observer=observer) as fs:
                    tool=next(t for t in fs.tools() if t.name==name);result=tool.callback(context,args)
                check(context);return runtime.redact(result)
        def execute_spec(name,context,args):
            spec=self.specs[name]
            with guard():
                check(context);owned()
                if record['origin'] not in spec['allowed_origins']:raise PermissionError('Extension outside assessment scope')
                params=dict(args,origin=record['origin'],host=record['origin'].removeprefix('https://'))
                def expand(value,encode=lambda x:str(x)):
                    return re.sub(r'\{([A-Za-z_][A-Za-z0-9_]*)\}',lambda m:encode(params[m.group(1)]),value)
                if spec['kind'] in ('command','script'):
                    argv=[expand(a) for a in spec['argv']];command=shlex.join(argv)
                    def gate(ctx,launch):
                        check(ctx);owned();allowed=launch.command==command and launch.cwd==str(self.root)
                        return Grant(allowed,allowed,allowed)
                    shell=BashTool(str(self.root),shell='/bin/bash',environment=spec.get('environment',{}),gate=gate,runner=PosixRunner(),max_timeout_ms=min(spec.get('timeout_ms',30000),120000))
                    result=shell.run(context,{'command':command,'timeout_ms':min(spec.get('timeout_ms',30000),120000)})
                elif spec['kind']=='http':
                    path=expand(spec['path'],lambda value:quote(str(value),safe=''))
                    if spec.get('authentication')=='assessment':result=runtime.inventory(record).read_registered(context,path)
                    else:
                        http=ScopedHTTP(record['origin'],authorized_origins=self.allowed,allow_path=lambda method,p:method=='GET' and p==path,max_bytes=65536)
                        response=http.request(context,'GET',path,{'Accept':'application/json,text/plain','Accept-Encoding':'identity'})
                        result=json.dumps({'status':response['status'],'body_sha256':response['body_sha256'],'body':response['body'][:65536].decode('utf-8','replace'),'truncated':len(response['body'])>65536})
                else:result=self._mcp(spec,scope,context,params,check,guard)
                check(context);return runtime.redact(result)
        enabled={r.key for r in runtime.catalog}
        handlers=tuple(Tool(name,s['description'],deepcopy(s['schema']),lambda c,a,n=name:execute_spec(n,c,a),lambda a,s=s:valid(s['schema'],a)) for name,s in self.specs.items() if name in enabled)
        catalog=tuple(replace(r,deferred=True) for r in runtime.catalog if r.key in self.specs)
        registry=ToolRegistry('worker',handlers=handlers,current=lambda revision:revision==self.revision and owned(),lifecycle_guard=lambda _:guard(),authorize=lambda c,a:runtime.authorize(c,a))
        registry.reload(catalog,self.revision)
        def search(context,args):
            with guard():
                check(context);q=args.get('query','').lower()
                return json.dumps({'tools':[dict(name=n,description=s['description'],input_schema=s['schema'],kind=s['kind']) for n,s in self.specs.items() if n in enabled and record['origin'] in s['allowed_origins'] and (q in n.lower() or q in s['description'].lower())]})
        def execute(context,args):
            name=args['name']
            if name not in self.specs or name not in enabled:raise ValueError('Unknown configured tool')
            with guard():
                check(context);registry.unlock(name,self.revision)
                tool=next(t for t in registry.tools() if t.name==name)
                parent=context.get('_tool_action')
                if not isinstance(parent,Action):raise PermissionError('Owned tool invocation required')
                action=Action('tool',name,json.dumps(args['arguments']),parent.call_id+':extra:'+name)
                return ToolLedger.invoke(context,action,lambda:tool.callback(context,args['arguments']),events)
        return tuple(Tool(t.name,t.description,t.input_schema,lambda c,a,n=t.name:filesystem(n,c,a),t.validate) for t in self.files)+(
            Tool('SearchExtraTools','Find configured read-only tools and their exact argument schemas.',SEARCH,search,lambda a:valid(SEARCH,a)),
            Tool('ExecuteExtraTool','Execute one configured read-only tool by its discovered name.',EXECUTE,execute,lambda a:valid(EXECUTE,a)),
        )+handlers

    def _mcp(self,spec,scope,context,params,check,guard):
        endpoint=spec['url'];parsed=urlsplit(endpoint)
        def transport(request):
            # The MCP server is explicitly configured infrastructure. Its tool
            # receives the admitted target; it cannot redirect its own session.
            if request.url!=endpoint or request.method not in ('POST','DELETE'):raise PermissionError('MCP endpoint changed')
            cleanup=request.method=='DELETE'
            if not cleanup:check(context)
            conn=http.client.HTTPSConnection(parsed.hostname,parsed.port or 443,timeout=5 if cleanup else min(20,context.remaining_seconds()),context=ssl.create_default_context())
            try:
                conn.request(request.method,parsed.path+('?' + parsed.query if parsed.query else ''),body=request.body,headers=dict(request.headers))
                response=conn.getresponse();body=response.read(1048577)
                if len(body)>1048576:raise ValueError('MCP response limit')
                if not cleanup:check(context)
                return HTTPResponse(response.status,dict(response.getheaders()),body)
            finally:conn.close()
        headers=dict(self._headers[spec['name']])
        client=Client(spec['server'],endpoint,transport=transport,headers=headers)
        try:
            client.initialize();remote=next((t for t in client.tools() if t.name==spec['remote_tool']),None)
            if remote is None or _structure(remote.input_schema)!=_structure(spec['remote_schema']):raise PermissionError('MCP schema changed')
            cs=ContextScope(scope.task_id,scope.exploration_id,scope.run_id,scope.revision)
            bridge=MCPTools(client,[remote],cs,session_id=client.session_id,gate=lambda c,a:(check(c),Grant(True,True,True))[1],current=lambda _:True,lifecycle_guard=lambda _:guard())
            arguments={k:v for k,v in params.items() if k not in ('origin','host')};arguments[spec['target_parameter']]=params['origin']
            return bridge.call(tool_name(spec['server'],spec['remote_tool']),context,arguments)
        finally:client.close()
