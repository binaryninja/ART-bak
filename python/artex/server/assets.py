"""Authenticated asset list/count/batch-upsert contracts from server/assets.go.

Upstream exposes GET/POST/DELETE /api/assets and GET /api/assets/counts, not
GET /api/assets/{id} or PATCH asset endpoints. Updates use the POST upsert route.
All six asset types use injected guarded persistence; unsupported mutation
fields and deletion return 503 before mutation. Hostname-derived writes require
an offline public-suffix resolver. No DNS lookup,
enrichment, synchronization, provider request or target traffic occurs here.

Safety differences are explicit: malformed/ambiguous scope selectors return 400
instead of silently becoming global; duplicate JSON keys and explicit null task_id
are rejected; combined company/task selectors are rejected;
company-scoped DSL is honored rather than ignored. Global access requires the
store's explicit allow_global configuration. Mutation JSON is UTF-8 only and
limited to 1 MiB (upstream has no local asset-body bound). Errors do not expose
internal SQL, credentials or exception details. Each successful upsert is durable;
per-item failures preserve successful results in the upstream batch envelope.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import json
import re
from urllib.parse import parse_qs, urlsplit
from .core import Request, Response, Router, error_response, json_response
from ..asset_dsl import DSLError, compile_dsl
from ..db.asset_store import AssetInvalid, AssetUnavailable, AssetUnsupported

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
MAX_REQUEST_BYTES = 1 << 20
_TYPES = {'root_domain', 'ip', 'subdomain', 'app', 'service', 'endpoint'}
_IMPLEMENTED_UPSERTS = _TYPES
_INT = re.compile(r'[+-]?[0-9]+\Z')
_STRING_FIELDS = {'type','domain','icp','record_type','ip','app_name','bundle_id','category','description',
                  'app_icp','url','page_title','favicon_mmh3','service_ip','service_name','method'}
_STRING_ARRAYS = {'record_value','bound_domains','technologies'}
_OBJECT_ARRAYS = {'auth','params'}
_POINTER_FIELDS = {'company_id','port','status_code','content_length','task_source_node_id'}
_OPTIONAL = {'domain','root_domain','ip','c_segment','icp','bound_domains','open_ports','record_type','record_value',
             'bundle_id','app_name','category','app_description','app_icp','url','service_type','service_name',
             'favicon_mmh3','page_title','technologies','auth','method','params','extra','task_source','task_source_summary'}
_SCOPE_FIELDS = {'company_id','company_ids','scope','task_ids','in_scope'}


class InvalidAssetRequest(ValueError):
    """Fixed application messages only; never request secrets."""


def _int64(value, label, *, default=None):
    if value is None:
        return default
    if type(value) is not int or not -(2**63) <= value < 2**63:
        raise InvalidAssetRequest(f'{label} must be a signed 64-bit integer')
    return value


def _query_int(value: str, label: str) -> int:
    if not _INT.fullmatch(value):
        raise InvalidAssetRequest(f'Invalid {label}')
    digits = value.lstrip('+-').lstrip('0') or '0'
    if len(digits) > 19:
        raise InvalidAssetRequest(f'Invalid {label}')
    number = int(('-' if value.startswith('-') else '') + digits)
    if not -(2**63) <= number < 2**63:
        raise InvalidAssetRequest(f'Invalid {label}')
    return number


def _query(request: Request, *, counts=False) -> dict:
    try:
        query = parse_qs(urlsplit(request.target).query, keep_blank_values=True, max_num_fields=100)
    except ValueError:
        raise InvalidAssetRequest('Invalid query parameters') from None
    known = {'type','limit','offset','dsl','task_id','company_id'}
    for name in known:
        if len(query.get(name, [])) > 1:
            raise InvalidAssetRequest(f'Duplicate {name} parameter')
    # Do not silently ignore filter names that would imply a narrower scope.
    if any(name in query for name in ('scope','in_scope','task_ids','company_ids')):
        raise InvalidAssetRequest('Unsupported asset scope selector')
    task_id, company_id = 0, None
    for name in ('task_id', 'company_id'):
        if name in query:
            value = _query_int(query[name][0], name)
            if value <= 0:
                raise InvalidAssetRequest(f'{name} must be positive')
            if name == 'task_id': task_id = value
            else: company_id = value
    if task_id and company_id is not None:
        raise InvalidAssetRequest('Choose one task or company scope, not both')
    if counts and any(name in query for name in ('company_id','dsl','type','limit','offset')):
        raise InvalidAssetRequest('Asset counts supports only task_id or the global view')
    typ = query.get('type', [''])[0]
    if typ and typ not in _TYPES:
        raise InvalidAssetRequest('Invalid asset type')
    dsl = query.get('dsl', [''])[0]
    # Validate before any count/query hits the executor.
    compile_dsl(dsl)
    limit = _query_int(query['limit'][0], 'limit') if 'limit' in query else DEFAULT_PAGE_SIZE
    offset = _query_int(query['offset'][0], 'offset') if 'offset' in query else 0
    limit = DEFAULT_PAGE_SIZE if limit <= 0 else min(limit, MAX_PAGE_SIZE)
    offset = max(0, offset)
    if not counts and not typ and not dsl and not task_id and company_id is None:
        typ = 'root_domain'
    return {'dsl': dsl, 'typ': typ, 'task_id': task_id, 'company_id': company_id,
            'limit': limit, 'offset': offset}


def asset_dto(asset: Mapping) -> dict:
    """Go Asset JSON shape: preserve required fields and omit empty optional data."""
    out = {name: asset[name] for name in ('id','type','task_ids','last_seen')}
    if isinstance(out['last_seen'], datetime):
        out['last_seen'] = out['last_seen'].isoformat()
    for name in _POINTER_FIELDS:
        if asset.get(name) is not None:
            out[name] = asset[name]
    for name in _OPTIONAL:
        value = asset.get(name)
        if value is not None and value != '' and value != [] and value != {}:
            out[name] = value
    return out


def _invalid_number(_):
    raise ValueError('Invalid JSON number')


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidAssetRequest('Duplicate JSON object key')
        result[key] = value
    return result


def _parse_insert(body: bytes) -> tuple[int, list[dict]]:
    data = json.loads(body.decode('utf-8'), parse_constant=_invalid_number, object_pairs_hook=_unique_object)
    if not isinstance(data, dict):
        raise InvalidAssetRequest('Expected JSON object')
    if any(field in data for field in _SCOPE_FIELDS):
        raise InvalidAssetRequest('Asset creation supports task_id; company ownership is resolved by the store')
    if 'task_id' in data and data['task_id'] is None:
        raise InvalidAssetRequest('task_id must not be null')
    task_id = _int64(data.get('task_id'), 'task_id', default=0)
    if task_id < 0:
        raise InvalidAssetRequest('task_id must not be negative')
    assets = data.get('assets')
    if assets is None:
        return task_id, []
    if not isinstance(assets, list):
        raise InvalidAssetRequest('assets must be an array')
    normalized = []
    for raw in assets:
        if not isinstance(raw, dict):
            raise InvalidAssetRequest('Each asset must be an object')
        if any(field in raw for field in _SCOPE_FIELDS | {'task_id'}):
            raise InvalidAssetRequest('Per-asset scope selectors are unsupported')
        supported_input_fields = _STRING_FIELDS | _STRING_ARRAYS | _OBJECT_ARRAYS | {'open_ports','port','status_code','content_length'}
        if set(raw) - supported_input_fields:
            raise AssetUnsupported('Unrecognized asset mutation fields are not silently discarded')
        asset = {}
        for field in _STRING_FIELDS:
            value = raw.get(field)
            if value is not None and not isinstance(value, str):
                raise InvalidAssetRequest(f'{field} must be a string')
            if value is not None:
                value.encode('utf-8')
                asset[field] = value
        for field in _STRING_ARRAYS | _OBJECT_ARRAYS | {'open_ports'}:
            value = raw.get(field)
            if value is None:
                continue
            kind = str if field in _STRING_ARRAYS else dict
            if not isinstance(value, list) or any(not isinstance(x, kind) for x in value):
                raise InvalidAssetRequest(f'Invalid {field} array')
            if kind is str:
                for item in value: item.encode('utf-8')
            if field == 'open_ports':
                ports = []
                for item in value:
                    if set(item) - {'port','service'}:
                        raise AssetUnsupported('Unsupported open-port mutation fields')
                    port = _int64(item.get('port'), 'port', default=0)
                    service = item.get('service')
                    if service is None: service = ''
                    if not isinstance(service, str):
                        raise InvalidAssetRequest('Port service must be a string')
                    service.encode('utf-8')
                    ports.append({'port': port, 'service': service})
                value = ports
            asset[field] = value
        for field in ('port','status_code','content_length'):
            if raw.get(field) is not None:
                asset[field] = _int64(raw[field], field)
        normalized.append(asset)
    return task_id, normalized


class AssetService:
    def __init__(self, store):
        self.store = store

    def register(self, router: Router) -> None:
        for method, path, action in (
            ('GET','/api/assets','list'), ('GET','/api/assets/counts','counts'),
            ('POST','/api/assets','insert'), ('DELETE','/api/assets','delete'),
        ):
            router.add(method, path, lambda request, params, action=action: self._handle(action, request))

    def _handle(self, action: str, request: Request) -> Response:
        if self.store is None:
            return error_response(503, 'database unavailable')
        try:
            if action == 'delete':
                return error_response(503, 'Asset deletion and related cleanup are not implemented')
            if action in ('list','counts'):
                params = _query(request, counts=action == 'counts')
                if action == 'counts':
                    return json_response(200, self.store.counts_by_type(task_id=params['task_id']))
                limit, offset = params.pop('limit'), params.pop('offset')
                total = self.store.count(**params)
                assets = self.store.query(**params, limit=limit, offset=offset) if offset < total else []
                return json_response(200, {'count': len(assets), 'total': total,
                                           'assets': [asset_dto(item) for item in assets]})
            return self._insert(request)
        except (InvalidAssetRequest, DSLError):
            return error_response(400, 'Invalid asset request or query')
        except (AssetUnsupported, AssetUnavailable):
            return error_response(503, 'Requested asset capability or required adapter is unavailable')
        except AssetInvalid:
            return error_response(400, 'Invalid asset data or scope')
        except Exception:
            return error_response(500, 'Asset operation failed')

    def _insert(self, request: Request) -> Response:
        if len(request.body) > MAX_REQUEST_BYTES:
            return error_response(413, 'Request body too large')
        try:
            task_id, assets = _parse_insert(request.body)
        except (ValueError, UnicodeError):
            return error_response(400, 'Invalid asset request')
        # Preflight the entire batch before any durable write: a missing offline
        # resolver or unsupported metadata must not cause a partial 503 batch.
        if any(asset.get('type') in _IMPLEMENTED_UPSERTS for asset in assets):
            self.store.check_mutation_available()
        invalid = set()
        for index, asset in enumerate(assets):
            if asset.get('type') in _IMPLEMENTED_UPSERTS:
                try:
                    self.store.validate_upsert(asset, task_id=task_id)
                except AssetInvalid:
                    invalid.add(index)
        results, errors = [], []
        for index, asset in enumerate(assets):
            typ = asset.get('type','')
            if typ not in _TYPES:
                errors.append({'index': index, 'error': 'unknown asset type'})
                continue
            if index in invalid:
                errors.append({'index': index, 'error': 'Invalid asset data'})
                continue
            try:
                identifier = self.store.upsert(asset, task_id=task_id)
            except AssetInvalid:
                errors.append({'index': index, 'error': 'Invalid asset data'})
            except (AssetUnsupported, AssetUnavailable):
                errors.append({'index': index, 'error': 'Asset mutation unavailable'})
            except Exception:
                errors.append({'index': index, 'error': 'Asset mutation failed'})
            else:
                results.append({'index': index, 'id': identifier, 'type': typ})
        # Upstream's nil slices serialize as null, not [], when empty.
        return json_response(200, {'results': results or None, 'errors': errors or None})
