"""Authenticated LLM profile HTTP slice, without constructing providers.

Only upstream routes are registered; there is no GET profile-by-id endpoint.
GET /api/llm requires a real injected runtime snapshot, never substitutes the
stored active profile. Mutations require an explicit coordinator: validate all
runtime prerequisites before writes, persist using ProfileStore's key-preserving
operations, then invalidate/reapply caches before returning success. The adapter
must raise ProfileApplyPending if persistence succeeded but runtime application
failed; clients are told to reload rather than blindly repeat the mutation.
No coordinator is provided here. Missing adapters, deletion, probes and legacy
runtime configuration return 503. JSON is bounded, UTF-8 and duplicate rejecting.
Unlike Go, public URL credentials are redacted and display placeholders rejected.
Whitespace-only replacement keys are treated as blank (preserve existing key),
not persisted as unusable credentials. Save requests use numeric IDs as upstream;
list DTO IDs are strings. Field omission other than keys retains upstream save
default semantics, not PATCH semantics.
"""
from dataclasses import fields
import json
import math
from typing import Protocol
from urllib.parse import urlsplit
from .core import error_response, json_response
from ..db.profiles import (LLMProfile, RetryRule, RetryOverride, ProfileInvalid,
                           ProfileNotFound, ProfileStorageError, _validate, _public_url)

MAX_BODY = 1 << 20


class ProfileApplyPending(RuntimeError):
    """Adapter confirms durable persistence but runtime apply remains incomplete."""


class ProfileMutationCoordinator(Protocol):
    def save(self, profile: LLMProfile) -> int: ...
    def set_active(self, identifier: int) -> None: ...


def _object(pairs):
    result={}
    for key,value in pairs:
        if key in result: raise ProfileInvalid('Duplicate JSON keys')
        result[key]=value
    return result


def _body(request):
    data=json.loads(request.body.decode('utf-8'),object_pairs_hook=_object,
                    parse_constant=lambda _: (_ for _ in ()).throw(ProfileInvalid('Invalid number')))
    if not isinstance(data,dict): raise ProfileInvalid('Expected object')
    return data


def _identifier(value, *, create=False):
    if type(value) is not int or not (0 if create else 1)<=value<2**63:
        raise ProfileInvalid('Invalid profile identifier')
    return value


def parse_profile(data):
    allowed={f.name for f in fields(LLMProfile)}
    if set(data)-allowed: raise ProfileInvalid('Unsupported fields')
    values=dict(data)
    _identifier(values.get('id',0),create=True)
    # A UI hint is never a replacement key. Missing/blank keys preserve existing
    # credentials; explicit null is invalid rather than an implicit overwrite.
    if 'api_key' in values:
        if not isinstance(values['api_key'],str): raise ProfileInvalid('Invalid key')
        if not values['api_key'].strip(): values['api_key']=''
    retry=values.pop('retry',{})
    if not isinstance(retry,dict) or set(retry)-{'connect','empty','stream'}: raise ProfileInvalid('Invalid retry')
    rules={}
    for key,item in retry.items():
        if not isinstance(item,dict) or set(item)-{'attempts','interval_ms'}: raise ProfileInvalid('Invalid retry')
        rules[key]=RetryRule(**item).clamped()
    values['retry']=RetryOverride(**rules)
    if values.get('streaming') is None: values['streaming']=True
    maximum=values.get('max_tokens',0)
    if type(maximum) is not int: raise ProfileInvalid('Invalid max_tokens')
    values['max_tokens']=max(0,maximum)
    if values.get('format','openai')!='openai' or values.get('max_tokens_field','')!='max_completion_tokens':
        values['max_tokens_field']=''
    profile=LLMProfile(**values)
    _validate(profile)
    return profile


def profile_dto(profile):
    if not isinstance(profile,LLMProfile): raise ProfileStorageError('Invalid profile adapter')
    result=profile.public_dict()
    result['id']=str(profile.id)
    for key in ('base_url','proxy','api_key_hint'):
        if not result[key]: result.pop(key)
    return result


def runtime_dto(snapshot):
    # Strict projection prevents provider keys, headers or private objects from
    # leaking if an adapter accidentally returns more than its public contract.
    names={'configured','provider','model','base_url','proxy','key_set',
           'rate_per_second','rate_per_minute','context_window_k','thinking_type','reasoning_effort'}
    if not isinstance(snapshot,dict) or not names<=snapshot.keys():
        raise ProfileStorageError('Invalid runtime snapshot')
    result={key:snapshot[key] for key in names}
    for key in ('configured','key_set'):
        if type(result[key]) is not bool: raise ProfileStorageError('Invalid runtime snapshot')
    for key in ('provider','model','base_url','proxy','thinking_type','reasoning_effort'):
        if not isinstance(result[key],str): raise ProfileStorageError('Invalid runtime snapshot')
    for key in ('rate_per_second','rate_per_minute','context_window_k'):
        if type(result[key]) not in (int,float) or not math.isfinite(result[key]):
            raise ProfileStorageError('Invalid runtime snapshot')
    for key in ('base_url','proxy'): result[key]=_public_url(result[key])
    return result


class ProfileService:
    def __init__(self,store,*,coordinator:ProfileMutationCoordinator|None=None,runtime_snapshot=None):
        self.store,self.coordinator,self.runtime_snapshot=store,coordinator,runtime_snapshot

    def register(self,router):
        for method,path,action in (
            ('GET','/api/llm/profiles','list'),('POST','/api/llm/profiles','save'),
            ('POST','/api/llm/profiles/active','active'),('DELETE','/api/llm/profiles/{id}','unavailable'),
            ('GET','/api/llm','runtime'),('POST','/api/llm','unavailable'),
            ('POST','/api/llm/test','unavailable'),('POST','/api/llm/models','unavailable')):
            router.add(method,path,lambda req,params,action=action:self._handle(action,req))

    def _handle(self,action,request):
        try:
            if urlsplit(request.target).query: raise ProfileInvalid('Unsupported query parameters')
            if action=='unavailable': return error_response(503,'Profile capability is unavailable')
            if action=='runtime':
                if self.runtime_snapshot is None: return error_response(503,'Runtime snapshot unavailable')
                return json_response(200,runtime_dto(self.runtime_snapshot()))
            if self.store is None: return error_response(503,'Profile storage unavailable')
            if action=='list': return json_response(200,{'profiles':[profile_dto(p) for p in self.store.list()]})
            if len(request.body)>MAX_BODY: return error_response(413,'Request body too large')
            data=_body(request)
            if action=='save': value=parse_profile(data)
            else:
                if set(data)!={'id'}: raise ProfileInvalid('Expected profile id')
                value=_identifier(data['id'])
            if self.coordinator is None: return error_response(503,'Profile runtime mutation coordinator unavailable')
            if action=='save':
                identifier=self.coordinator.save(value)
                if type(identifier) is not int or not 0<identifier<2**63:
                    raise ProfileStorageError('Invalid mutation coordinator result')
                return json_response(200,{'id':identifier})
            self.coordinator.set_active(value)
            return json_response(200,{'ok':True})
        except ProfileApplyPending:
            return json_response(503,{'error':'Profile persisted but runtime application is incomplete; reload before retrying',
                                      'persisted':True,'runtime_applied':False})
        except ProfileNotFound: return error_response(404,'Profile not found')
        except (ValueError,UnicodeError): return error_response(400,'Invalid profile request')
        except Exception: return error_response(500,'Profile operation failed')
