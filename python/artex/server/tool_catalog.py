"""Protected catalog metadata routes only; custom/test and reset never execute.

Exact source DTO fields and routes, bounded strict JSON and immutable path key.
Unknown fields (including invented approval flags) are rejected, not discarded.
Exec is opaque data, never interpreted. Catalog enabled/bindings are NOT grants.
Reset needs code-owned defaults and stays503. Read errors propagate rather than
source best-effort usage zeroes that misrepresent a failed aggregate.
"""
import json
from urllib.parse import urlsplit
from dataclasses import replace
from .core import error_response,json_response
from ..db.tool_catalog import CatalogTool,ToolInvalid,ToolMissing,ToolConflict,ToolUnavailable,key,_pairs

class ToolCatalogService:
    def __init__(self,store=None):self.store=store
    def register(self,router):
        for method,path,action in [('GET','/api/tools','list'),('PUT','/api/tools/{key}','update'),
            ('POST','/api/tools/custom','create'),('PUT','/api/tools/custom/{key}','custom_update'),
            ('DELETE','/api/tools/custom/{key}','delete'),('POST','/api/tools/custom/test','unavailable'),
            ('POST','/api/tools/{key}/reset','unavailable')]:
            router.add(method,path,lambda req,params,a=action:self.handle(a,req,params))
    def handle(self,action,request,params):
        try:
            if urlsplit(request.target).query:raise ToolInvalid()
            if action=='unavailable' or self.store is None:raise ToolUnavailable()
            identifier=key(params['key']) if 'key' in params else None
            if action=='list':
                items=self.store.list();counts=self.store.usage_counts()
                return json_response(200,{'tools':[t.to_dict(calls=counts.get(t.key,0)) for t in items]})
            if action=='delete':
                self.store.delete(identifier);return json_response(200,{'deleted':identifier})
            if len(request.body)>1048576:return error_response(413,'Request body too large')
            body=json.loads(request.body.decode('utf-8'),object_pairs_hook=_pairs,parse_constant=lambda _:(_ for _ in ()).throw(ToolInvalid()))
            fields={'description','schema','agents','enabled'}
            if action!='update':fields|={'key','kind','exec','deferred'}
            if not isinstance(body,dict) or set(body)-fields:raise ToolInvalid()
            if action=='create':
                raw=body.get('key')
                if not isinstance(raw,str):raise ToolInvalid()
                identifier=key(raw.strip(),custom=True)
            elif 'key' in body and body['key']!=identifier:raise ToolInvalid('Path key is immutable')
            if action=='update':
                current=self.store.get(identifier)
                if current is None:raise ToolMissing()
                tool=replace(current,description=body.get('description',''),schema=body.get('schema',{}),agents=body.get('agents',[]),enabled=body.get('enabled',False))
            else:
                tool=CatalogTool(identifier,False,body.get('description',''),body.get('schema',{}),body.get('agents',[]),body.get('enabled',False),body.get('kind',''),body.get('exec',{}),body.get('deferred',False))
            if action=='create':self.store.create(tool);return json_response(200,{'key':identifier})
            self.store.update(tool,custom=action=='custom_update');return json_response(200,{'ok':True})
        except ToolConflict:return error_response(409,'Tool key already exists')
        except ToolMissing:return error_response(404,'Tool not found')
        except ToolUnavailable:return error_response(503,'Tool capability unavailable')
        except (ValueError,UnicodeError,TypeError,RecursionError):return error_response(400,'Invalid tool metadata')
        except Exception:return error_response(500,'Tool catalog operation failed')
