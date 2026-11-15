"""Protected standalone finding HTTP contracts from server/server.go.

Global bare lists preserve the 500-row dashboard envelope; paginated lists use
items/total/page/page_size. Unlike Go, filters without pagination are rejected
rather than ignored. Malformed, duplicate or unsupported scope selectors never
fall back to global. JSON is UTF-8, duplicate-key rejecting and bounded to 1MiB.
Metadata PATCH validates all fields before sequential durable setters (as in Go;
this is not an all-fields transaction). Status notifications, live task views,
context provenance, evidence-retaining exports/deletion and retests are explicit
503 until ported. Stored reports are returned by GET, not generated. No network,
filesystem evidence access, target or provider execution occurs here.
"""
from dataclasses import asdict, is_dataclass
from datetime import datetime
import json
import re
from urllib.parse import parse_qs, urlsplit
from .core import Response, Router, Request, error_response, json_response
from ..db.findings import FindingFilter, FindingNotFound, FindingArchiveState, EvidenceConflict

BASE = '/api/exploration/findings'
MAX_BODY = 1 << 20
SEVERITIES = {'critical','high','medium','low'}
STATUSES = {'pending','in_progress','confirmed','resolved','fixed','false_positive','ignored','duplicate','risk_accepted'}
FILTERS = {'severity','status','vulnclass','task_id','q','sort','asset_scope'}


class InvalidFindingRequest(ValueError): pass
class FindingCapabilityUnavailable(RuntimeError): pass


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,19}', value):
        raise InvalidFindingRequest()
    result = int(value)
    if not 0 < result < 2**63: raise InvalidFindingRequest()
    return result


def _query(request, allowed):
    q = parse_qs(urlsplit(request.target).query, keep_blank_values=True, max_num_fields=100)
    if set(q) - allowed or any(len(values) != 1 for values in q.values()):
        raise InvalidFindingRequest()
    return {key: values[0] for key, values in q.items()}


def _filter(q):
    values = {name: q.get(name,'').strip() for name in FILTERS}
    for name in ('severity','status','vulnclass','task_id'):
        if values[name] == 'all': values[name] = ''
    if values['severity'] and values['severity'] not in SEVERITIES: raise InvalidFindingRequest()
    if values['status'] and values['status'] not in STATUSES: raise InvalidFindingRequest()
    if values['sort'] not in ('','time','severity'): raise InvalidFindingRequest()
    if 'task_id' in q and values['task_id'] not in ('all','__unassigned__'):
        # An explicit blank/malformed task scope is never the global default.
        if q['task_id'].strip() != 'all': _id(values['task_id'])
    if values['asset_scope'] not in ('','__none__'): raise FindingCapabilityUnavailable()
    return FindingFilter(severity=values['severity'], status=values['status'],
                         vulnclass=values['vulnclass'], task_id=values['task_id'],
                         query=q.get('q',''), sort=values['sort'], asset_scope=values['asset_scope'])


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise InvalidFindingRequest()
        result[key] = value
    return result


def _mapping(value):
    if isinstance(value, dict): return value
    if hasattr(value,'to_dict'): return value.to_dict()
    if is_dataclass(value): return asdict(value)
    raise TypeError('Finding adapter must return records')


def _label(a):
    if a.get('url'): return ((a.get('method','') + ' ') if a.get('method') else '') + a['url']
    for field in ('domain','ip'):
        if a.get(field): return a[field] + (':' + str(a['port']) if a.get('port',0) else '')
    return next((a[k] for k in ('app_name','bundle_id','service_name','root_domain') if a.get(k)), '#' + str(a['id']))


def finding_dto(value, assets):
    f = _mapping(value)
    stamp = f['created_at']
    if isinstance(stamp,datetime): stamp = stamp.isoformat()
    dto = {key:f.get(key, '') for key in ('vulnclass','severity','summary','evidence')}
    dto.update(id=str(f['id']), finding_id=str(f['id']), ts=stamp,
               status=f.get('status') or 'pending', traffic_count=f.get('traffic_count',0),
               evidence_version=f.get('evidence_version',0), report_evidence_version=f.get('report_evidence_version',0),
               report_stale=bool(f.get('report')) and f.get('evidence_version',0) != f.get('report_evidence_version',0))
    for field in ('name','report','traffic_bindings'):
        if f.get(field): dto[field] = f[field]
    if f.get('task_id') is not None:
        dto['task_id'] = str(f['task_id'])
        if f.get('task_description'): dto['task_description'] = f['task_description']
    anchored = []
    for aid in f.get('asset_ids',[]):
        if aid in assets:
            a = _mapping(assets[aid])
            anchored.append({'id':str(aid),'type':a['type'],'label':_label(a)})
    if anchored: dto['assets'] = anchored
    return dto


class FindingService:
    def __init__(self, store, *, asset_resolver=None):
        self.store, self.asset_resolver = store, asset_resolver

    def register(self, router: Router):
        routes = [('GET',BASE,'list'),('GET',BASE+'/{id}','get'),('PATCH',BASE+'/{id}','patch'),
            ('GET', BASE+'/stats', 'unavailable'),
            ('GET', BASE+'/groups', 'unavailable'),
            ('GET', BASE+'/asset-tree', 'unavailable'),
            ('GET', BASE+'/export', 'unavailable'),
            ('GET', BASE+'/{id}/lineage', 'unavailable'),
            ('GET', BASE+'/{id}/retests', 'unavailable'),
            ('GET', BASE+'/retests/active', 'unavailable'),
            ('POST', BASE+'/{id}/retests', 'unavailable'),
            ('POST', BASE+'/{id}/deepen', 'unavailable'),
            ('DELETE', BASE+'/{id}', 'unavailable'),
        ]
        for method,path,action in routes:
            router.add(method,path,lambda req,params,action=action:self._handle(action,req,params))

    def _assets(self, findings):
        ids = list(dict.fromkeys(aid for f in findings for aid in _mapping(f).get('asset_ids',[])))
        if not ids: return {}
        if self.asset_resolver is None: raise FindingCapabilityUnavailable()
        return self.asset_resolver(ids)

    def _handle(self, action, request, params):
        try:
            ident = _id(params['id']) if 'id' in params else None
            if action == 'unavailable' or self.store is None:
                raise FindingCapabilityUnavailable()
            if action == 'list': return self._list(request)
            q = _query(request, {'context_task'} if action == 'get' else set())
            if 'context_task' in q:
                _id(q['context_task'])
                raise FindingCapabilityUnavailable()
            if action == 'patch': return self._patch(request,ident)
            f = self.store.get(ident)
            if f is None: return error_response(404,'Finding not found')
            return json_response(200,finding_dto(f,self._assets([f])))
        except FindingNotFound:
            return error_response(404,'Finding not found')
        except (FindingArchiveState,EvidenceConflict):
            return error_response(409,'Finding state prevents the requested change')
        except FindingCapabilityUnavailable:
            return error_response(503,'Requested finding capability or adapter is unavailable')
        except (InvalidFindingRequest,ValueError,UnicodeError):
            return error_response(400,'Invalid finding request')
        except NotImplementedError:
            return error_response(503,'Requested finding capability is unavailable')
        except Exception:
            return error_response(500,'Finding operation failed')

    def _list(self, request):
        q = _query(request,FILTERS|{'task','page','limit'})
        if 'task' in q:
            _id(q['task'])
            raise FindingCapabilityUnavailable()
        filt = _filter(q)
        if not {'page','limit'} & q.keys():
            if FILTERS & q.keys(): raise InvalidFindingRequest()
            records = self.store.list(limit=500)
            assets = self._assets(records)
            return json_response(200,[finding_dto(f,assets) for f in records])
        page = _id(q.get('page','1'))
        limit = min(_id(q.get('limit','20')),200)
        if (page-1)*limit >= 2**63: raise InvalidFindingRequest()
        records,total = self.store.list_page(filt,page=page,page_size=limit)
        assets = self._assets(records)
        return json_response(200,{'items':[finding_dto(f,assets) for f in records], 'total':total,'page':page,'page_size':limit})

    def _patch(self, request, ident):
        if len(request.body)>MAX_BODY: return error_response(413,'Request body too large')
        data = json.loads(request.body.decode('utf-8'),object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(InvalidFindingRequest()))
        if not isinstance(data,dict) or not data or set(data)-{'status','severity','name','vulnclass'}:
            raise InvalidFindingRequest()
        if any(not isinstance(v,str) for v in data.values()): raise InvalidFindingRequest()
        for value in data.values(): value.encode('utf-8')
        if 'status' in data and data['status'] not in STATUSES: raise InvalidFindingRequest()
        if 'severity' in data and data['severity'] not in SEVERITIES: raise InvalidFindingRequest()
        if 'status' in data: raise FindingCapabilityUnavailable()  # no lost notifications
        before = self.store.get(ident)
        if before is None: return error_response(404,'Finding not found')
        self._assets([before])  # fail before writes if DTO dependencies absent
        for field,value in data.items():
            affected = getattr(self.store,'set_'+field)(ident,value.strip() if field in ('name','vulnclass') else value)
            if affected == 0: return error_response(404,'Finding not found')
        after = self.store.get(ident)
        if after is None: return error_response(404,'Finding not found')
        return json_response(200,finding_dto(after,self._assets([after])))
