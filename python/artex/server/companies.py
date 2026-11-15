"""Protected company HTTP contracts from server/assets.go; no external searches.

GET list and detail preserve numeric IDs and company/scope shapes. The list
repository uses one aggregate plus one scope query per company, as upstream.
Empty list serializes null (Go's nil slice); individual scopes serialize arrays.
POST creates metadata with EMPTY initial scope only. Nonempty scope, scope edits,
reattribution and deletion return 503 before writes, never partial creation or
FK-only cleanup. No PATCH/update/search route is invented. The injected store's
fixed scope is preserved; a scoped store cannot create new companies.
Stricter than Go: duplicate JSON keys, unsupported query/body fields, nonpositive
IDs and non-UTF8 JSON are rejected. Body limit matches upstream 2MiB.
"""
import json
import re
from urllib.parse import urlsplit
from .core import error_response,json_response
from ..db.companies import (Company,CompanyWithScope,ScopeRule,CompanyInvalid,
                           CompanyUnavailable,CompanyNameConflict,CompanyNotFound,_GO_SPACE)
MAX_BODY=2 << 20


def _id(raw):
    if not isinstance(raw,str) or not re.fullmatch(r'[0-9]{1,19}',raw):raise CompanyInvalid('Invalid id')
    value=int(raw)
    if not 0<value<2**63:raise CompanyInvalid('Invalid id')
    return value


def _text(value):
    if not isinstance(value,str) or '\x00' in value:raise CompanyInvalid('Invalid text')
    value.encode('utf-8')
    return value


def _pairs(pairs):
    data={}
    for key,value in pairs:
        if key in data:raise CompanyInvalid('Duplicate JSON key')
        data[key]=value
    return data


def _decode(body):
    data=json.loads(body.decode('utf-8'),object_pairs_hook=_pairs,
                    parse_constant=lambda _: (_ for _ in ()).throw(CompanyInvalid('Invalid number')))
    if not isinstance(data,dict):raise CompanyInvalid('Expected object')
    return data


def _scope(value):
    if value is None:return []  # source JSON null equals no initial rules
    if not isinstance(value,list):raise CompanyInvalid('Expected scope array')
    for rule in value:
        if isinstance(rule,str):raw=_text(rule)
        elif isinstance(rule,dict) and not set(rule)-{'kind','value'}:
            _text(rule.get('kind',''));raw=_text(rule.get('value',''))
        else:raise CompanyInvalid('Invalid scope rule')
        if len(raw)>1024:raise CompanyInvalid('Scope rule too long')
    return value


def _company(value, *, full=False):
    if not isinstance(value,CompanyWithScope if full else Company):
        raise RuntimeError('Invalid company adapter result')
    data=value.to_dict()
    # Explicit projection also excludes unexpected metadata from subclasses.
    keys={'id','name','nkey','logo','created_at','updated_at'}
    if full:keys|={'asset_count','scope'}
    return {key:data[key] for key in keys if key in data}


def _rules(values):
    if not isinstance(values,list) or any(not isinstance(v,ScopeRule) for v in values):
        raise RuntimeError('Invalid company scope adapter result')
    return [v.to_dict() for v in values]


class CompanyService:
    def __init__(self,store):self.store=store

    def register(self,router):
        for method,path,action in (
            ('GET','/api/companies','list'),('POST','/api/companies','create'),
            ('GET','/api/companies/{id}','get'),('DELETE','/api/companies/{id}','unavailable'),
            ('POST','/api/companies/{id}/scope','unavailable'),('POST','/api/companies/reattribute','unavailable')):
            router.add(method,path,lambda req,params,action=action:self._handle(action,req,params))

    def _handle(self,action,request,params):
        try:
            if urlsplit(request.target).query:raise CompanyInvalid('Unsupported query')
            identifier=_id(params['id']) if 'id' in params else None
            if len(request.body)>MAX_BODY:return error_response(413,'Request body too large')
            if action=='unavailable':return error_response(503,'Company scope mutation or cleanup is unavailable')
            if self.store is None:return error_response(503,'Company storage unavailable')
            if action=='list':
                rows=self.store.list()
                return json_response(200,[_company(row,full=True) for row in rows] or None)
            if action=='get':
                company=self.store.get(identifier)
                if company is None:return error_response(404,'Company not found')
                if company.id!=identifier:raise RuntimeError('Company lookup identity mismatch')
                return json_response(200,{'company':_company(company),'scope':_rules(self.store.get_scope(identifier))})
            data=_decode(request.body)
            if set(data)-{'name','logo','scope'}:raise CompanyInvalid('Unsupported fields')
            name=_text(data.get('name','')).strip(_GO_SPACE)
            logo=_text(data.get('logo',''))
            if not name:raise CompanyInvalid('Company name required')
            scope=_scope(data.get('scope',[]))
            if scope:raise CompanyUnavailable('Nonempty initial scope requires attribution recomputation')
            identifier=self.store.create(name,logo,scope_inputs=[])
            if type(identifier) is not int or not 0<identifier<2**63:raise RuntimeError('Invalid company creation result')
            return json_response(201,{'id':identifier,'created':True,'scope_added':0,'scope_skipped':0,'scope_invalid':0})
        except CompanyNameConflict:return error_response(409,'Company name already exists')
        except CompanyNotFound:return error_response(404,'Company not found')
        except CompanyUnavailable:return error_response(503,'Company capability unavailable')
        except (ValueError,UnicodeError):return error_response(400,'Invalid company request')
        except Exception:return error_response(500,'Company operation failed')
