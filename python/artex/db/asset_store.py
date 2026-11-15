"""Scoped asset persistence facade; no connection or import-time I/O.

Reads compose existing parameter-bound DSL plans and task membership CTEs. Global
reads/writes require explicit allow_global=True. Task-bound reads hydrate provenance using the upstream bound task_asset_links
query; an optional injected hydrator may override only the provenance fields.

Mutation tranche: all six asset types with upstream SQL merge
semantics and explicit company ownership. Writes require one transaction, the shared
company-scope advisory lock, an injected company resolver and mutation guard.
Resolver and guard MUST use the supplied transaction; the guard must enforce task
existence/scope and configured global/task asset interception before any write.
No public-suffix, DNS, company-scope matcher or interception implementation is
invented here. Hostnames require an injected offline Go-compatible RootDomain
resolver. Explicit company reassignment, arbitrary PATCH,
and deletion are unsupported. Triggers/schema must already exist.
Linked host/IP assets use the same transaction and mutation guard; failures roll
back the entire write instead of upstream best-effort side effects. URL writes
accept only validated ASCII absolute HTTP(S), excluding userinfo and escaped
hosts; this is a deliberate fail-closed subset of Go net/url, not full parity.

Company-filtered reads use the same bounded pagination defaults as other reads
(nonpositive limit becomes 50), rather than the Go company helper's unlimited
mode. Task API provenance is written in the upsert transaction and errors roll
back, instead of the upstream HTTP handler ignoring attribution errors.

No live PostgreSQL/driver/lock behavior was tested. No network scanning, DNS lookup,
filesystem action, or credential disclosure occurs. Stored auth fields are returned
as upstream records; the HTTP boundary must authenticate/authorize access.
"""
from copy import deepcopy
import ipaddress
import json
from typing import Mapping
from urllib.parse import urlsplit
import re

from .assets import asset_query, asset_count, assets_by_ids, decode_asset
from .core import Executor, Query, int64


class AssetInvalid(ValueError): pass
class AssetUnavailable(RuntimeError): pass
class AssetUnsupported(AssetUnavailable): pass

ASSET_TYPES = frozenset(('root_domain', 'ip', 'subdomain', 'app', 'service', 'endpoint'))
_COMPANY_LOCK = 7337741003
_GO_SPACE = '\t\n\v\f\r \u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000'

_ROOT_UPSERT = "\nINSERT INTO assets(type, domain, root_domain, icp, company_id, company_source, task_ids)\nVALUES ('root_domain', $1, $1, $2, $3, 'scope', $4::bigint[])\nON CONFLICT (domain) WHERE type = 'root_domain' DO UPDATE SET\n    icp        = COALESCE(EXCLUDED.icp, assets.icp),\n    company_id = CASE\n        WHEN assets.company_source = 'explicit' AND assets.company_id IS NOT NULL THEN assets.company_id\n        ELSE COALESCE(EXCLUDED.company_id, assets.company_id)\n    END,\n    company_source = CASE\n        WHEN assets.company_source = 'explicit' AND assets.company_id IS NOT NULL THEN 'explicit'\n        WHEN EXCLUDED.company_id IS NOT NULL THEN 'scope'\n        ELSE assets.company_source\n    END,\n    task_ids   = (SELECT ARRAY(SELECT DISTINCT unnest(assets.task_ids || EXCLUDED.task_ids))),\n    extra      = assets.extra || EXCLUDED.extra,\n    last_seen  = now()\nRETURNING id"

_IP_UPSERT = "\nINSERT INTO assets(type, ip, c_segment, bound_domains, open_ports, company_id, company_source, task_ids)\nVALUES ('ip', $1, $2::cidr, $3::text[], $4::jsonb[], $5, 'scope', $6::bigint[])\nON CONFLICT (ip) WHERE type = 'ip' DO UPDATE SET\n    bound_domains = (SELECT ARRAY(SELECT DISTINCT unnest(assets.bound_domains || EXCLUDED.bound_domains))),\n    open_ports    = (\n        SELECT ARRAY(\n            SELECT DISTINCT ON ((elem->>'port')::int) elem\n            FROM unnest(assets.open_ports || EXCLUDED.open_ports) AS elem\n            ORDER BY (elem->>'port')::int,\n                     CASE WHEN elem->>'service' IS NOT NULL AND elem->>'service' <> '' THEN 0 ELSE 1 END\n        )\n    ),\n    c_segment  = COALESCE(assets.c_segment, EXCLUDED.c_segment),\n    company_id = CASE\n        WHEN assets.company_source = 'explicit' AND assets.company_id IS NOT NULL THEN assets.company_id\n        ELSE COALESCE(EXCLUDED.company_id, assets.company_id)\n    END,\n    company_source = CASE\n        WHEN assets.company_source = 'explicit' AND assets.company_id IS NOT NULL THEN 'explicit'\n        WHEN EXCLUDED.company_id IS NOT NULL THEN 'scope'\n        ELSE assets.company_source\n    END,\n    task_ids   = (SELECT ARRAY(SELECT DISTINCT unnest(assets.task_ids || EXCLUDED.task_ids))),\n    last_seen  = now()\nRETURNING id"


_SUBDOMAIN_UPSERT = "\nINSERT INTO assets(type, domain, root_domain, record_type, record_value, ip, c_segment, icp, company_id, company_source, task_ids)\nVALUES ('subdomain', $1, $2, $3, $4::text[], $5, $6::cidr, $7, $8, 'scope', $9::bigint[])\nON CONFLICT (domain, COALESCE(record_type,'')) WHERE type = 'subdomain' DO UPDATE SET\n    ip           = COALESCE(EXCLUDED.ip, assets.ip),\n    c_segment    = COALESCE(EXCLUDED.c_segment, assets.c_segment),\n    icp          = COALESCE(EXCLUDED.icp, assets.icp),\n    company_id   = CASE\n        WHEN assets.company_source = 'explicit' AND assets.company_id IS NOT NULL THEN assets.company_id\n        ELSE COALESCE(EXCLUDED.company_id, assets.company_id)\n    END,\n    company_source = CASE\n        WHEN assets.company_source = 'explicit' AND assets.company_id IS NOT NULL THEN 'explicit'\n        WHEN EXCLUDED.company_id IS NOT NULL THEN 'scope'\n        ELSE assets.company_source\n    END,\n    task_ids     = (SELECT ARRAY(SELECT DISTINCT unnest(assets.task_ids || EXCLUDED.task_ids))),\n    record_value = (SELECT ARRAY(SELECT DISTINCT unnest(assets.record_value || EXCLUDED.record_value))),\n    extra        = assets.extra || EXCLUDED.extra,\n    last_seen    = now()\nRETURNING id"

_APP_BUNDLE_UPSERT = "\nINSERT INTO assets(type, bundle_id, app_name, category, app_description, app_icp, company_id, company_source, task_ids)\nVALUES ('app', $1, $2, $3, $4, $5, $6, $7, $8::bigint[])\nON CONFLICT (bundle_id) WHERE type = 'app' AND bundle_id IS NOT NULL DO UPDATE SET\n    app_name        = COALESCE(EXCLUDED.app_name, assets.app_name),\n    category        = COALESCE(EXCLUDED.category, assets.category),\n    app_description = COALESCE(EXCLUDED.app_description, assets.app_description),\n    app_icp         = COALESCE(EXCLUDED.app_icp, assets.app_icp),\n    company_id      = CASE\n        WHEN EXCLUDED.company_source = 'explicit' THEN EXCLUDED.company_id\n        WHEN assets.company_source = 'explicit' AND assets.company_id IS NOT NULL THEN assets.company_id\n        ELSE COALESCE(EXCLUDED.company_id, assets.company_id)\n    END,\n    company_source  = CASE\n        WHEN EXCLUDED.company_source = 'explicit' THEN 'explicit'\n        WHEN assets.company_source = 'explicit' AND assets.company_id IS NOT NULL THEN 'explicit'\n        WHEN EXCLUDED.company_id IS NOT NULL THEN 'scope'\n        ELSE assets.company_source\n    END,\n    task_ids        = (SELECT ARRAY(SELECT DISTINCT unnest(assets.task_ids || EXCLUDED.task_ids))),\n    last_seen       = now()\nRETURNING id"

_APP_NAME_UPSERT = "\nINSERT INTO assets(type, bundle_id, app_name, category, app_description, app_icp, company_id, company_source, task_ids)\nVALUES ('app', NULL, $1, $2, $3, $4, $5, $6, $7::bigint[])\nON CONFLICT (app_name) WHERE type = 'app' AND bundle_id IS NULL DO UPDATE SET\n    category        = COALESCE(EXCLUDED.category, assets.category),\n    app_description = COALESCE(EXCLUDED.app_description, assets.app_description),\n    app_icp         = COALESCE(EXCLUDED.app_icp, assets.app_icp),\n    company_id      = CASE\n        WHEN EXCLUDED.company_source = 'explicit' THEN EXCLUDED.company_id\n        WHEN assets.company_source = 'explicit' AND assets.company_id IS NOT NULL THEN assets.company_id\n        ELSE COALESCE(EXCLUDED.company_id, assets.company_id)\n    END,\n    company_source  = CASE\n        WHEN EXCLUDED.company_source = 'explicit' THEN 'explicit'\n        WHEN assets.company_source = 'explicit' AND assets.company_id IS NOT NULL THEN 'explicit'\n        WHEN EXCLUDED.company_id IS NOT NULL THEN 'scope'\n        ELSE assets.company_source\n    END,\n    task_ids        = (SELECT ARRAY(SELECT DISTINCT unnest(assets.task_ids || EXCLUDED.task_ids))),\n    last_seen       = now()\nRETURNING id"

_HTTP_UPSERT = "\nINSERT INTO assets(\n    type, url, service_type, service_name, domain, ip, port, root_domain,\n    c_segment, favicon_mmh3, technologies, status_code, content_length, page_title,\n    auth, company_id, company_source, task_ids\n)\nVALUES (\n    'service', $1, 'http', $2, $3, $4, $5, $6,\n    $7::cidr, $8, $9::text[], $10, $11, $12,\n    $13::jsonb[], $14, 'scope', $15::bigint[]\n)\nON CONFLICT (url) WHERE type = 'service' AND service_type = 'http' DO UPDATE SET\n    status_code    = COALESCE(EXCLUDED.status_code,    assets.status_code),\n    content_length = COALESCE(EXCLUDED.content_length, assets.content_length),\n    page_title     = COALESCE(EXCLUDED.page_title,     assets.page_title),\n    favicon_mmh3   = COALESCE(EXCLUDED.favicon_mmh3,   assets.favicon_mmh3),\n    ip             = COALESCE(EXCLUDED.ip,             assets.ip),\n    c_segment      = COALESCE(EXCLUDED.c_segment,      assets.c_segment),\n    technologies   = (SELECT ARRAY(SELECT DISTINCT unnest(assets.technologies || EXCLUDED.technologies))),\n    auth           = (\n        SELECT ARRAY(\n            SELECT DISTINCT ON (elem::text) elem\n            FROM unnest(assets.auth || EXCLUDED.auth) AS elem\n        )\n    ),\n    task_ids       = (SELECT ARRAY(SELECT DISTINCT unnest(assets.task_ids || EXCLUDED.task_ids))),\n    company_id     = CASE\n        WHEN assets.company_source = 'explicit' AND assets.company_id IS NOT NULL THEN assets.company_id\n        ELSE COALESCE(EXCLUDED.company_id, assets.company_id)\n    END,\n    company_source = CASE\n        WHEN assets.company_source = 'explicit' AND assets.company_id IS NOT NULL THEN 'explicit'\n        WHEN EXCLUDED.company_id IS NOT NULL THEN 'scope'\n        ELSE assets.company_source\n    END,\n    last_seen      = now()\nRETURNING id"

_OTHER_UPSERT = "\nINSERT INTO assets(\n    type, service_type, service_name, domain, ip, port, root_domain,\n    c_segment, auth, company_id, company_source, task_ids\n)\nVALUES ('service', 'other', $1, $2, $3, $4, $5, $6::cidr, $7::jsonb[], $8, 'scope', $9::bigint[])\nON CONFLICT (COALESCE(domain,''), COALESCE(ip,''), port, service_name) WHERE type = 'service' AND service_type = 'other' DO UPDATE SET\n    domain     = COALESCE(EXCLUDED.domain,     assets.domain),\n    ip         = COALESCE(EXCLUDED.ip,         assets.ip),\n    c_segment  = COALESCE(EXCLUDED.c_segment,  assets.c_segment),\n    auth       = (\n        SELECT ARRAY(\n            SELECT DISTINCT ON (elem::text) elem\n            FROM unnest(assets.auth || EXCLUDED.auth) AS elem\n        )\n    ),\n    task_ids   = (SELECT ARRAY(SELECT DISTINCT unnest(assets.task_ids || EXCLUDED.task_ids))),\n    company_id = CASE\n        WHEN assets.company_source = 'explicit' AND assets.company_id IS NOT NULL THEN assets.company_id\n        ELSE COALESCE(EXCLUDED.company_id, assets.company_id)\n    END,\n    company_source = CASE\n        WHEN assets.company_source = 'explicit' AND assets.company_id IS NOT NULL THEN 'explicit'\n        WHEN EXCLUDED.company_id IS NOT NULL THEN 'scope'\n        ELSE assets.company_source\n    END,\n    last_seen  = now()\nRETURNING id"

_ENDPOINT_UPSERT = "\nINSERT INTO assets(\n    type, url, method, domain, ip, port, root_domain,\n    params, company_id, company_source, task_ids, c_segment\n)\nVALUES ('endpoint', $1, $2, $3, $4, $5, $6, $7::jsonb[], $8, 'scope', $9::bigint[], $10::cidr)\nON CONFLICT (url, method) WHERE type = 'endpoint' DO UPDATE SET\n    params     = (\n        SELECT ARRAY(\n            SELECT DISTINCT ON ((elem->>'location'), (elem->>'name')) elem\n            FROM unnest(assets.params || EXCLUDED.params) AS elem\n            ORDER BY (elem->>'location'), (elem->>'name'), elem::text DESC\n        )\n    ),\n    ip         = COALESCE(EXCLUDED.ip,        assets.ip),\n    c_segment  = COALESCE(EXCLUDED.c_segment, assets.c_segment),\n    task_ids   = (SELECT ARRAY(SELECT DISTINCT unnest(assets.task_ids || EXCLUDED.task_ids))),\n    company_id = CASE\n        WHEN assets.company_source = 'explicit' AND assets.company_id IS NOT NULL THEN assets.company_id\n        ELSE COALESCE(EXCLUDED.company_id, assets.company_id)\n    END,\n    company_source = CASE\n        WHEN assets.company_source = 'explicit' AND assets.company_id IS NOT NULL THEN 'explicit'\n        WHEN EXCLUDED.company_id IS NOT NULL THEN 'scope'\n        ELSE assets.company_source\n    END,\n    last_seen  = now()\nRETURNING id"

def _positive(value, name):
    try:
        value = int64(value, name)
    except ValueError as error:
        raise AssetInvalid(str(error)) from error
    if value <= 0:
        raise AssetInvalid(name + ' must be positive')
    return value


def _text(value, name):
    if not isinstance(value, str) or '\x00' in value:
        raise AssetInvalid(name + ' must be text without NUL')
    try:
        value.encode('utf-8')
    except UnicodeError as error:
        raise AssetInvalid(name + ' must be valid UTF-8') from error
    return value


def domain_key(value):
    value = _text(value, 'domain').strip(_GO_SPACE)
    # Go strings.ToLower performs simple rune lowering (not contextual sigma).
    value = ''.join(c.lower()[0] for c in value)
    if value.endswith('.'):
        value = value[:-1]
    if not value:
        raise AssetInvalid('domain is required')
    return value


def validate_ip(value):
    value = _text(value, 'ip')
    if '%' in value:
        raise AssetInvalid('ip must be an IPv4/IPv6 address without a zone identifier')
    try:
        return ipaddress.ip_address(value)
    except ValueError as error:
        raise AssetInvalid('ip must be an IPv4/IPv6 address, not a hostname; use subdomain/domain for hostnames') from error


def _array(values):
    # Values remain in one bound SQL parameter, never inserted into SQL source.
    return '{' + ','.join('"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"' for value in values) + '}'


def asset_to_dict(asset):
    """Go Asset's omitempty surface; pointer integers retain zero if present."""
    result = deepcopy(dict(asset))
    for key in list(result):
        if key not in ('id','type','task_ids','last_seen'):
            value = result[key]
            if value is None or value == '' or value == [] or value == {}:
                result.pop(key)
    return result


class AssetStore:
    def __init__(self, executor: Executor, *, allow_global=False,
                 hydrate_task_sources=None, company_resolver=None, mutation_guard=None,
                 root_domain_resolver=None):
        if type(allow_global) is not bool:
            raise AssetInvalid('allow_global must be boolean')
        self.executor = executor
        self.allow_global = allow_global
        self.hydrate_task_sources = hydrate_task_sources or self._hydrate_task_sources
        self.company_resolver = company_resolver
        self.mutation_guard = mutation_guard
        self.root_domain_resolver = root_domain_resolver

    def _filters(self, typ, task_id, in_scope, company_id):
        if not isinstance(typ, str) or (typ and typ not in ASSET_TYPES):
            raise AssetInvalid('Unknown asset type filter')
        try:
            int64(task_id, 'task_id')
        except ValueError as error:
            raise AssetInvalid(str(error)) from error
        if task_id < 0 or (in_scope and task_id <= 0):
            raise AssetInvalid('Task scope requires a positive task_id')
        if type(in_scope) is not bool:
            raise AssetInvalid('in_scope must be boolean')
        if company_id is not None:
            _positive(company_id, 'company_id')
        if task_id == 0 and company_id is None and not self.allow_global:
            raise AssetUnavailable('Global asset access is not enabled')

    @staticmethod
    def _company(query, company_id):
        if company_id is None:
            return query
        # Existing read plans have no user-supplied SQL and bind pagination last.
        before, separator, after = query.sql.rpartition(' ORDER BY last_seen DESC, id DESC')
        if separator:
            return Query(before + f' AND company_id = ${len(query.params)+1}' + separator + after,
                         query.params + (company_id,))
        return Query(query.sql + f' AND company_id = ${len(query.params)+1}', query.params + (company_id,))

    def _hydrate_task_sources(self, task_id, assets):
        if not assets:
            return assets
        by_id = {asset['id']: asset for asset in assets}
        ids = '{' + ','.join(str(_positive(key, 'asset_id')) for key in by_id) + '}'
        rows = self.executor.fetch_all(Query(
            'SELECT asset_id, source, source_summary, source_node_id FROM task_asset_links '
            'WHERE task_id=$1 AND asset_id=ANY($2::bigint[])', (task_id, ids)))
        for row in rows:
            if len(row) != 4 or row[0] not in by_id:
                raise AssetUnavailable('Invalid task asset provenance row')
            source, summary = _text(row[1], 'source'), _text(row[2], 'source summary')
            asset = by_id[row[0]]
            asset.update(task_source=source, task_source_summary=summary)
            if row[3] is not None:
                asset['task_source_node_id'] = _positive(row[3], 'source node_id')
        return assets

    def _decode(self, rows, task_id):
        assets = [decode_asset(row) for row in rows]
        if task_id:
            if self.hydrate_task_sources is None:
                raise AssetUnavailable('Task asset provenance hydrator is not configured')
            original_ids = [asset['id'] for asset in assets]
            hydrated = self.hydrate_task_sources(task_id, deepcopy(assets))
            if not isinstance(hydrated, list) or [asset.get('id') for asset in hydrated] != original_ids:
                raise AssetUnavailable('Provenance hydrator changed asset membership/order')
            # Hydrator supplies provenance only, never changes fields or asset scope.
            for asset, extra in zip(assets, hydrated):
                for field in ('task_source', 'task_source_summary', 'task_source_node_id'):
                    if field in extra:
                        asset[field] = deepcopy(extra[field])
        return [asset_to_dict(asset) for asset in assets]

    def query(self, dsl='', typ='', task_id=0, limit=50, offset=0, *, in_scope=False, company_id=None):
        self._filters(typ, task_id, in_scope, company_id)
        _text(dsl, 'DSL')
        if task_id and self.hydrate_task_sources is None:
            raise AssetUnavailable('Task asset provenance hydrator is not configured')
        try:
            query = asset_query(dsl, typ, task_id, limit, offset, in_scope=in_scope)
        except (ValueError, TypeError) as error:
            raise AssetInvalid(str(error)) from error
        return self._decode(self.executor.fetch_all(self._company(query, company_id)), task_id)

    def count(self, dsl='', typ='', task_id=0, *, in_scope=False, company_id=None):
        self._filters(typ, task_id, in_scope, company_id)
        _text(dsl, 'DSL')
        try:
            query = asset_count(dsl, typ, task_id, in_scope=in_scope)
        except (ValueError, TypeError) as error:
            raise AssetInvalid(str(error)) from error
        row = self.executor.fetch_one(self._company(query, company_id))
        if len(row) != 1 or type(row[0]) is not int or row[0] < 0:
            raise AssetUnavailable('Invalid asset count result')
        return row[0]

    def get(self, asset_id, *, task_id=0, in_scope=False, company_id=None):
        _positive(asset_id, 'asset_id')
        self._filters('', task_id, in_scope, company_id)
        if task_id and self.hydrate_task_sources is None:
            raise AssetUnavailable('Task asset provenance hydrator is not configured')
        query = assets_by_ids([asset_id], task_id=task_id if in_scope else 0)
        if task_id and not in_scope:
            before, after = query.sql.rsplit(' ORDER BY', 1)
            query = Query(before + f' AND ${len(query.params)+1} = ANY(task_ids) ORDER BY' + after,
                          query.params + (task_id,))
        assets = self._decode(self.executor.fetch_all(self._company(query, company_id)), task_id)
        if len(assets) > 1:
            raise AssetUnavailable('Asset lookup returned duplicate rows')
        return assets[0] if assets else None

    def counts_by_type(self, task_id=0, *, in_scope=False, company_id=None):
        self._filters('', task_id, in_scope, company_id)
        plan = self._company(asset_count('', '', task_id, in_scope=in_scope), company_id)
        sql = plan.sql.replace('SELECT count(*) FROM assets WHERE', 'SELECT type, count(*) FROM assets WHERE', 1)
        rows = self.executor.fetch_all(Query(sql + ' GROUP BY type', plan.params))
        counts = {}
        for row in rows:
            if len(row) != 2 or row[0] not in ASSET_TYPES or type(row[1]) is not int or row[1] < 0 or row[0] in counts:
                raise AssetUnavailable('Invalid asset type-count result')
            counts[row[0]] = row[1]
        return counts

    def check_mutation_available(self):
        if (not callable(getattr(self.executor, 'transaction', None))
                or not callable(self.company_resolver) or not callable(self.mutation_guard)):
            raise AssetUnavailable('Asset writes require transaction, company resolver and mutation guard adapters')

    def validate_upsert(self, asset, task_id=0):
        """Pure preflight, allowing HTTP batches to reject unsupported types first."""
        if not isinstance(asset, Mapping):
            raise AssetInvalid('Asset must be an object')
        typ = asset.get('type')
        if not isinstance(typ, str) or any(not isinstance(key, str) for key in asset):
            raise AssetInvalid('Asset type and field names must be strings')
        fields = {'root_domain': {'type','domain','icp'}, 'ip': {'type','ip','bound_domains','open_ports'},
                  'subdomain': {'type','domain','record_type','record_value','icp'},
                  'app': {'type','app_name','bundle_id','category','description','app_icp'},
                  'service': ({'type','url','technologies','status_code','content_length','page_title','favicon_mmh3','auth','ip','service_ip'}
                              if asset.get('url') else {'type','domain','ip','port','service_name','auth'}),
                  'endpoint': {'type','url','method','params','ip','service_ip'}}
        if typ not in fields:
            raise AssetUnsupported('Unknown asset upsert type')
        if set(asset) - fields[typ]:
            raise AssetUnsupported('Unsupported asset mutation fields: ' + ', '.join(sorted(set(asset)-fields[typ])))
        self._filters(typ, task_id, False, None)
        self.check_mutation_available()
        normalized = dict(asset)
        if typ == 'root_domain':
            normalized['domain'] = domain_key(asset.get('domain', ''))
            normalized['icp'] = _text(asset.get('icp', ''), 'icp')
        elif typ == 'ip':
            validate_ip(asset.get('ip', ''))
            normalized['ip'] = asset['ip']
            domains, ports = asset.get('bound_domains', []), asset.get('open_ports', [])
            if not isinstance(domains, (list, tuple)) or not isinstance(ports, (list, tuple)):
                raise AssetInvalid('bound_domains and open_ports must be arrays')
            normalized['bound_domains'] = [_text(value, 'bound domain') for value in domains]
            normalized['open_ports'] = []
            for port in ports:
                if not isinstance(port, Mapping) or set(port) - {'port','service'}:
                    raise AssetInvalid('Each open port must contain port and optional service')
                number = port.get('port')
                if type(number) is not int or not 1 <= number <= 65535:
                    raise AssetInvalid('Open port must be between 1 and 65535')
                item = {'port': number}
                service = _text(port.get('service',''), 'port service')
                if service:
                    item['service'] = service
                normalized['open_ports'].append(item)
        else:
            normalized = self._normalize_extended(asset)
        return normalized

    def upsert(self, asset, task_id=0):
        normalized = self.validate_upsert(asset, task_id)
        with self.executor.transaction() as tx:
            tx.execute(Query('SELECT pg_advisory_xact_lock($1)', (_COMPANY_LOCK,)))
            asset_id = self._write(tx, normalized, task_id)
            if task_id:
                # Preserve API provenance atomically; unlike the Go HTTP caller,
                # do not silently ignore a failed provenance update.
                count = tx.execute(Query("""INSERT INTO task_asset_links(task_id, asset_id, source, source_summary, source_node_id)
SELECT task.id, asset.id, $3, $4, $5
FROM tasks task
JOIN assets asset ON asset.id=$2 AND task.id=ANY(asset.task_ids)
WHERE task.id=$1 AND task.deleted_at IS NULL
ON CONFLICT (task_id, asset_id) DO UPDATE
SET source=EXCLUDED.source,
    source_summary=EXCLUDED.source_summary,
    source_node_id=COALESCE(EXCLUDED.source_node_id, task_asset_links.source_node_id)""",
                    (task_id, asset_id, 'api', 'Registered via asset API', None)))
                if type(count) is not int or count != 1:
                    raise AssetUnavailable('Task asset association disappeared before provenance update')
        return asset_id

    def _write(self, tx, normalized, task_id):
        self.mutation_guard(tx, deepcopy(normalized), task_id)
        if normalized['type'] == 'root_domain':
            domain, icp = normalized['domain'], normalized['icp']
            company = self.company_resolver(tx, domain, '', icp)
            query = Query(_ROOT_UPSERT, (domain, icp or None, company, '{'+str(task_id)+'}' if task_id else '{}'))
        elif normalized['type'] == 'ip':
            address = validate_ip(normalized['ip'])
            # Go To4 handling does not derive a /24 for IPv4-mapped IPv6 text.
            if address.version == 6 and address.ipv4_mapped:
                segment = None
            else:
                segment = str(ipaddress.ip_network(str(address) + ('/24' if address.version == 4 else '/48'), strict=False))
            company = self.company_resolver(tx, '', normalized['ip'], '')
            ports = _array([json.dumps(port, separators=(',', ':'), ensure_ascii=False) for port in normalized['open_ports']])
            query = Query(_IP_UPSERT, (normalized['ip'], segment, _array(normalized['bound_domains']),
                                     ports, company, '{'+str(task_id)+'}' if task_id else '{}'))
        else:
            return self._write_extended(tx, normalized, task_id)
        if company is not None:
            _positive(company, 'resolved company_id')
        row = tx.fetch_one(query)
        if len(row) != 1:
            raise AssetUnavailable('Invalid asset upsert result')
        asset_id = _positive(row[0], 'returned asset_id')
        return asset_id

    def upsert_root_domain(self, domain, icp='', task_id=0):
        return self.upsert({'type':'root_domain','domain':domain,'icp':icp}, task_id)

    def upsert_ip(self, ip, bound_domains=(), open_ports=(), task_id=0):
        return self.upsert({'type':'ip','ip':ip,'bound_domains':bound_domains,'open_ports':open_ports}, task_id)

    def update(self, *args, **kwargs):
        raise AssetUnsupported('Arbitrary asset updates are not implemented; use supported type-specific upserts')

    def delete(self, *args, **kwargs):
        raise AssetUnsupported('Asset deletion and relationship cleanup are not implemented')

    def _root(self, host):
        if not host:
            return ''
        host = domain_key(host)
        try:
            validate_ip(host)
            return host
        except AssetInvalid:
            pass
        if not callable(self.root_domain_resolver):
            raise AssetUnavailable('Hostname writes require an offline Go-compatible public-suffix resolver')
        root = self.root_domain_resolver(host)
        # Adapter must implement Go RootDomain fallback, never return a guessed suffix.
        if not isinstance(root, str) or not root or domain_key(root) != root or not (host == root or host.endswith('.'+root)):
            raise AssetUnavailable('Invalid registrable-domain resolver result')
        return root

    @staticmethod
    def _segment(ip):
        if not ip:
            return None
        address = validate_ip(ip)
        if address.version == 6 and address.ipv4_mapped:
            return None
        return str(ipaddress.ip_network(str(address)+('/24' if address.version == 4 else '/48'), strict=False))

    @staticmethod
    def _json_array(value, field):
        if not isinstance(value, (list, tuple)) or any(not isinstance(item, dict) for item in value):
            raise AssetInvalid(field+' must be an array of objects')
        def check(obj):
            if isinstance(obj, str): _text(obj, field)
            elif isinstance(obj, dict):
                for key, val in obj.items():
                    _text(key, field+' key'); check(val)
            elif isinstance(obj, (list, tuple)):
                for val in obj: check(val)
            elif obj is not None and type(obj) not in (bool, int, float):
                raise AssetInvalid(field+' must contain JSON values')
        try:
            check(value)
            return _array([json.dumps(item, ensure_ascii=False, allow_nan=False, separators=(',', ':')) for item in value])
        except (ValueError, TypeError, RecursionError) as error:
            raise AssetInvalid('Invalid '+field+' JSON') from error

    @staticmethod
    def _url(raw):
        raw = _text(raw, 'url')
        # Restrict to the common absolute HTTP(S) subset rather than silently
        # diverging from Go net/url on opaque URLs, escaped hosts/userinfo, or
        # Unicode paths. Unsupported forms require a future net/url parity port.
        if not raw or any(ord(c) <= 32 or ord(c) >= 127 for c in raw) or re.search(r'%(?![0-9a-fA-F]{2})', raw):
            raise AssetInvalid('URL must be an ASCII absolute HTTP(S) URL with valid escapes')
        try:
            parsed = urlsplit(raw)
            if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username is not None or '%' in parsed.netloc or '\\' in raw:
                raise ValueError()
            port = parsed.port
            if port is not None and not 1 <= port <= 65535: raise ValueError()
        except ValueError as error:
            raise AssetInvalid('Unsupported or malformed absolute HTTP(S) URL') from error
        scheme, rest = raw.split('://',1) if '://' in raw else ('','')
        if not scheme: raise AssetInvalid('URL must contain an authority')
        end = min([pos for delim in '/?#' if (pos := rest.find(delim)) >= 0] or [len(rest)])
        canonical = scheme.lower()+'://'+rest[:end].lower()+rest[end:]
        # Match Go normalizeURL, including its fragment-sensitive trailing test.
        if canonical.endswith('/') and parsed.path == '/' and not parsed.query:
            canonical = canonical[:-1]
        return canonical, parsed.hostname.lower(), port or (443 if parsed.scheme == 'https' else 80), parsed.scheme.upper()

    def _normalize_extended(self, asset):
        n = deepcopy(dict(asset)); typ = n['type']
        def text(field):
            n[field] = _text(n.get(field,''), field)
            return n[field]
        def strings(field):
            values = n.get(field, [])
            if not isinstance(values, (tuple,list)): raise AssetInvalid(field+' must be an array')
            n[field] = [_text(value,field) for value in values]
        if typ == 'app':
            for field in ('app_name','bundle_id','category','description','app_icp'): text(field)
            if not n['app_name']: raise AssetInvalid('app_name is required')
            return n
        if typ == 'subdomain':
            n['domain'] = domain_key(n.get('domain',''))
            text('record_type'); text('icp'); strings('record_value')
            n['root_domain'] = self._root(n['domain'])
            n['ips'] = []
            if n['record_type'] in ('A','AAAA'):
                for value in n['record_value']:
                    for candidate in value.split(','):
                        candidate = candidate.strip(_GO_SPACE)
                        try: validate_ip(candidate)
                        except AssetInvalid: continue
                        n['ips'].append(candidate)
            return n
        ip = text('service_ip') if 'service_ip' in n else ''
        n['ip'] = ip or text('ip')
        if n['ip']: validate_ip(n['ip'])
        if typ == 'endpoint' or n.get('url'):
            n['url'], n['domain'], n['port'], n['service_name'] = self._url(n.get('url',''))
            if typ == 'endpoint':
                method = text('method')
                if not method: raise AssetInvalid('method is required')
                n['method'] = ''.join(c.upper() if len(c.upper()) == 1 else c for c in method)
                n['_json'] = self._json_array(n.get('params',[]),'params')
            else:
                strings('technologies'); text('page_title'); text('favicon_mmh3')
                for field in ('status_code','content_length'):
                    value = n.get(field)
                    if value is not None:
                        try: int64(value,field)
                        except ValueError as error: raise AssetInvalid(str(error)) from error
                        if value < 0: raise AssetInvalid(field+' must not be negative')
                    n[field] = value
                n['_json'] = self._json_array(n.get('auth',[]),'auth')
        else:
            domain = text('domain')
            n['domain'] = domain_key(domain) if domain else ''
            if not n['domain'] and not n['ip']: raise AssetInvalid('domain or ip is required')
            port = n.get('port')
            if type(port) is not int or not 1 <= port <= 65535: raise AssetInvalid('port must be between 1 and 65535')
            name = text('service_name').strip(_GO_SPACE)
            if not name: raise AssetInvalid('service_name is required')
            n['service_name'] = ''.join(c.lower()[0] for c in name)
            n['_json'] = self._json_array(n.get('auth',[]),'auth')
        n['root_domain'] = self._root(n['domain'])
        return n

    def _write_extended(self, tx, n, task_id):
        typ = n['type']; tasks = '{'+str(task_id)+'}' if task_id else '{}'
        root, ip = n.get('root_domain',''), n.get('ip','')
        icp = n.get('app_icp', n.get('icp',''))
        company = self.company_resolver(tx,root,ip,icp)
        def linked(asset):
            return self._write(tx, self.validate_upsert(asset, task_id), task_id)
        if typ == 'subdomain':
            linked({'type':'root_domain','domain':root})
            for address in n['ips']:
                linked({'type':'ip','ip':address,'bound_domains':[n['domain']]})
            ip = next(iter(n['ips']), '')
            if company is None and ip: company = self.company_resolver(tx,'',ip,'')
            sql = _SUBDOMAIN_UPSERT
            params = (n['domain'],root,n['record_type'],_array(n['record_value']),ip or None,self._segment(ip),icp or None,company,tasks)
        elif typ == 'app':
            tail = (n['app_name'],n['category'] or None,n['description'] or None,icp or None,company,'scope',tasks)
            sql, params = (_APP_BUNDLE_UPSERT,(n['bundle_id'],)+tail) if n['bundle_id'] else (_APP_NAME_UPSERT,tail)
        elif typ == 'endpoint':
            sql = _ENDPOINT_UPSERT
            params = (n['url'],n['method'],n['domain'] or None,ip or None,n['port'] or None,root or None,n['_json'],company,tasks,self._segment(ip))
        elif n.get('url'):
            sql = _HTTP_UPSERT
            params = (n['url'],n['service_name'],n['domain'] or None,ip or None,n['port'] or None,root,self._segment(ip),n['favicon_mmh3'] or None,_array(n['technologies']),n['status_code'],n['content_length'],n['page_title'] or None,n['_json'],company,tasks)
        else:
            sql = _OTHER_UPSERT
            params = (n['service_name'],n['domain'] or None,ip or None,n['port'],root or None,self._segment(ip),n['_json'],company,tasks)
        if company is not None: _positive(company,'resolved company_id')
        row = tx.fetch_one(Query(sql,params))
        if not row or len(row) != 1: raise AssetUnavailable('Invalid asset upsert result')
        asset_id = _positive(row[0],'returned asset_id')
        if typ in ('service','endpoint'):
            if root: linked({'type':'root_domain','domain':root})
            if n['domain'] and n['domain'] != root:
                try: validate_ip(n['domain'])
                except AssetInvalid: linked({'type':'subdomain','domain':n['domain']})
            if ip:
                secondary = {'type':'ip','ip':ip}
                if typ == 'service':
                    secondary.update(bound_domains=[n['domain']] if n['domain'] else [],open_ports=[{'port':n['port'],'service':n['service_name']}])
                linked(secondary)
        return asset_id
