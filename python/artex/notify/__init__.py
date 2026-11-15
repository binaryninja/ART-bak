"""Offline notification primitives; delivery adapters are intentionally not wired.

Ported from notify/*.go. All human-facing text is English. No function opens a
socket. URL validation alone is NOT sufficient SSRF protection: future transports
must validate resolved addresses at dial time and constrain redirects.
"""
from dataclasses import dataclass, field
import base64
import hashlib
import hmac
import ipaddress
import json
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

EVENT_FINDING_CREATED = 'finding_created'
EVENT_FINDING_STATUS_CHANGED = 'finding_status_changed'
INIT_KIND = 'dingtalk'
MASKED_PREFIX = '__masked__'
RANKS = {'low': 1, 'medium': 2, 'high': 3, 'critical': 4}

@dataclass
class Snapshot:
    kind: str = ''
    finding_id: int = 0
    task_id: int = 0
    vulnclass: str = ''
    name: str = ''
    severity: str = ''
    summary: str = ''
    asset_ids: list[int] = field(default_factory=list)
    from_status: str = ''
    to_status: str = ''

@dataclass
class Item:
    finding_id: int = 0
    name: str = ''
    vulnclass: str = ''
    severity: str = ''
    summary: str = ''
    assets: list[str] = field(default_factory=list)
    detail_url: str = ''
    from_status: str = ''
    to_status: str = ''

    def title(self):
        return self.name or self.vulnclass or '(unnamed finding)'

    def is_status_change(self):
        return bool(self.from_status or self.to_status)

@dataclass
class Message:
    items: list[Item] = field(default_factory=list)
    batch: bool = False
    window_minutes: int = 0
    home_url: str = ''

@dataclass
class Filter:
    min_severity: str = ''
    task_ids: list[int] = field(default_factory=list)
    asset_ids: list[int] = field(default_factory=list)
    vulnclass_include: list[str] = field(default_factory=list)
    vulnclass_exclude: list[str] = field(default_factory=list)
    on_status_change: bool = False

    def validate(self):
        if not valid_min_severity(self.min_severity):
            raise ValueError('Minimum severity must be low, medium, high, critical, or empty')


def parse_filter(raw):
    """Malformed configurations fail open, as the upstream contract requires."""
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return Filter()
        out = {}
        for key, value in data.items():
            if key not in Filter.__dataclass_fields__ or value is None:
                continue
            if key == 'min_severity':
                valid = isinstance(value, str)
            elif key == 'on_status_change':
                valid = type(value) is bool
            else:
                typ = int if key in ('task_ids', 'asset_ids') else str
                valid = isinstance(value, list) and all(type(x) is typ for x in value)
            if not valid:
                return Filter()
            out[key] = value
        return Filter(**out)
    except (ValueError, TypeError, UnicodeError):
        return Filter()


def severity_rank(value):
    return RANKS.get(value, 0)


def valid_min_severity(value):
    return value == '' or value in RANKS


def at_least(value, minimum):
    return not minimum or severity_rank(value) >= severity_rank(minimum)


def severity_label(value):
    return {'low': '🔵 Low', 'medium': '🟡 Medium', 'high': '🟠 High', 'critical': '🔴 Critical'}.get(value, value)


def status_label(value):
    return {'pending': 'Pending', 'in_progress': 'In progress', 'confirmed': 'Confirmed',
            'resolved': 'Resolved', 'fixed': 'Fixed', 'false_positive': 'False positive',
            'ignored': 'Ignored', 'duplicate': 'Duplicate', 'risk_accepted': 'Risk accepted'}.get(value, value)


def match(f, snapshot):
    if snapshot.kind == EVENT_FINDING_STATUS_CHANGED and not f.on_status_change:
        return False
    if not at_least(snapshot.severity, f.min_severity):
        return False
    if f.task_ids and snapshot.task_id not in f.task_ids:
        return False
    if f.asset_ids and not set(f.asset_ids).intersection(snapshot.asset_ids):
        return False
    def contains(words):
        return any(word.strip() and word.strip().lower() in snapshot.vulnclass.lower() for word in words)
    return not contains(f.vulnclass_exclude) and (not f.vulnclass_include or contains(f.vulnclass_include))


@dataclass(frozen=True)
class ChannelSpec:
    secret_keys: tuple[str, ...]
    destination_keys: tuple[str, ...]
    default_rate_per_min: int

CHANNELS = {
    'dingtalk': ChannelSpec(('webhook', 'secret'), ('webhook',), 20),
    'feishu': ChannelSpec(('webhook', 'secret'), ('webhook',), 100),
    'wecom': ChannelSpec(('webhook',), ('webhook',), 20),
    'webhook': ChannelSpec(('url', 'headers'), ('url',), 0),
    'telegram': ChannelSpec(('bot_token',), ('base_url',), 20),
    'email': ChannelSpec(('password',), ('host', 'port', 'tls'), 60),
}


def kinds():
    return sorted(CHANNELS)


def is_masked(value):
    return isinstance(value, str) and value.startswith(MASKED_PREFIX)


def masked_value(secret):
    return MASKED_PREFIX if len(secret) <= 6 else MASKED_PREFIX + ':…' + secret[-6:]


def mask_config(kind, config):
    if kind not in CHANNELS:
        return {}
    return {k: (masked_value(v) if isinstance(v, str) else MASKED_PREFIX)
            if k in CHANNELS[kind].secret_keys else v for k, v in config.items()}


def merge_config(stored, incoming):
    out = dict(stored)
    for key, value in incoming.items():
        if is_masked(value):
            continue
        if isinstance(value, str) and not value.strip():
            out.pop(key, None)
        else:
            out[key] = value
    return out


class DestinationChangedWithoutCredentials(ValueError):
    def __init__(self, changed, missing):
        self.changed, self.missing = changed, missing
        super().__init__('Destination changed (' + ', '.join(changed) +
                         '); explicitly replace or clear credentials: ' + ', '.join(missing))


def prepare_config_update(kind, stored, incoming):
    if kind not in CHANNELS:
        raise ValueError('Unregistered notification channel')
    spec = CHANNELS[kind]
    for key in spec.secret_keys:
        if key in incoming and not isinstance(incoming[key], str):
            if MASKED_PREFIX in json.dumps(incoming[key]):
                raise ValueError('Nested masked placeholder in credential field: ' + key)
    def blank(v):
        return v is None or isinstance(v, str) and not v.strip()
    def same(a, b):
        if blank(a) and blank(b):
            return True
        # Python considers True == 1, whereas JSON (and the Go port) does not.
        if isinstance(a, bool) != isinstance(b, bool):
            return False
        return a == b
    changed = [k for k in spec.destination_keys if k in incoming and not is_masked(incoming[k])
               and not same(incoming[k], stored.get(k))]
    missing = [k for k in spec.secret_keys if k not in incoming or is_masked(incoming[k])]
    if changed and missing:
        raise DestinationChangedWithoutCredentials(changed, missing)
    return merge_config(stored, incoming)


def truncate_bytes(text, maximum):
    raw = text.encode('utf-8')
    if maximum <= 0 or len(raw) <= maximum:
        return text
    suffix = '…' if maximum >= 3 else ''
    return raw[:maximum - len(suffix.encode())].decode('utf-8', errors='ignore') + suffix


def truncate_runes(text, maximum):
    if maximum <= 0 or len(text) <= maximum:
        return text
    return text[:maximum] if maximum == 1 else text[:maximum - 1] + '…'


def one_line(text, maximum=0):
    return truncate_runes(' '.join(text.split()), maximum)


def truncate_html(text, maximum):
    if maximum <= 0 or len(text) <= maximum:
        return text
    out = truncate_runes(text, maximum)
    for opener, closer in (('<', '>'), ('&', ';')):
        pos = out.rfind(opener)
        if pos >= 0 and closer not in out[pos:]:
            out = out[:pos]
    return out


def pack_item_count(items, max_size, reserve, footer, size, render):
    if max_size <= 0:
        return len(items)
    budget, used = max(0, max_size - reserve - size(footer)), 0
    for i, item in enumerate(items):
        used += size(render(item, i))
        if used > budget and i > 0:
            return i
    return len(items)


def markdown_escape(text):
    return ''.join('\\' + c if c in '\\`*_[]()!#>|~' else c for c in text)


def markdown_text(text, maximum=0):
    return markdown_escape(one_line(text, maximum))


def ding_talk_signed_url(hook, secret, timestamp_ms):
    if not secret:
        return hook
    try:
        parts = urlsplit(hook)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        ts = str(timestamp_ms)
        query['timestamp'] = ts
        query['sign'] = base64.b64encode(hmac.new(secret.encode(), (ts + '\n' + secret).encode(), hashlib.sha256).digest()).decode()
        return urlunsplit(parts._replace(query=urlencode(sorted(query.items()))))
    except ValueError:
        raise ValueError('Invalid webhook URL') from None


def feishu_sign(timestamp, secret):
    return base64.b64encode(hmac.new((str(timestamp) + '\n' + secret).encode(), b'', hashlib.sha256).digest()).decode()


def is_blocked_dial_ip(value):
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return True
    ip = getattr(ip, 'ipv4_mapped', None) or ip
    return ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast


def validate_http_url(raw, *, allow_local_targets=False):
    """Static checks only; does not resolve names or replace dial-time checks."""
    try:
        parts = urlsplit(raw)
        if parts.scheme not in ('http', 'https') or not parts.hostname:
            raise ValueError()
        parts.port  # Validate syntax without exposing the credential-bearing URL.
        try:
            ip = ipaddress.ip_address(parts.hostname)
        except ValueError:
            return
        if not allow_local_targets and is_blocked_dial_ip(str(ip)):
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError('Invalid or blocked HTTP destination') from None


def asset_line(assets, limit=3):
    if limit <= 0 or len(assets) <= limit:
        return ', '.join(assets)
    return ', '.join(assets[:limit]) + f' ({len(assets)} total)'


def markdown_title(message):
    if message.batch:
        return f'Finding digest · {len(message.items)} total'
    if not message.items:
        return 'Finding notification'
    item = message.items[0]
    return f'[{severity_label(item.severity)}] {one_line(item.title())}'


def _markdown_item(item, index=None):
    line = (f'{index + 1}. ' if index is not None else '')
    line += f'**{severity_label(item.severity)} · {markdown_text(item.title())}**'
    if index is not None:
        extras = []
        if item.assets:
            extras.append(markdown_text(asset_line(item.assets)))
        if item.summary:
            extras.append(markdown_text(item.summary, 60))
        return line + (' — ' + ' · '.join(extras) if extras else '') + '\n'
    lines = [line]
    if item.is_status_change():
        lines.append(f'**Status change**: {markdown_text(status_label(item.from_status))} → {markdown_text(status_label(item.to_status))}')
    if item.vulnclass and item.vulnclass != item.title():
        lines.append('**Type**: ' + markdown_text(item.vulnclass))
    if item.assets:
        lines.append('**Assets**: ' + markdown_text(asset_line(item.assets)))
    if item.summary and one_line(item.summary):
        lines.append('**Summary**: ' + markdown_text(item.summary, 120))
    if item.detail_url:
        lines.append(f'[View details]({item.detail_url})')
    return '\n'.join(lines) + '\n'


def markdown_body(message, max_bytes=0):
    """Return (rendered text, delivered prefix count), not the input batch size.

    Detail/home URLs must be trusted administrator-created platform links.
    Arbitrary target data is flattened and escaped before interpolation.
    """
    if not message.batch:
        if not message.items:
            return '', 0
        return truncate_bytes(_markdown_item(message.items[0]), max_bytes), 1
    footer = f'\n[View all in platform]({message.home_url})\n' if message.home_url else ''
    kept = pack_item_count(message.items, max_bytes, 320, footer,
                           lambda s: len(s.encode('utf-8')), _markdown_item)
    items = message.items[:kept]
    total = len(message.items)
    intro = f'**{total} new findings'
    if message.window_minutes > 0:
        intro += f' in the last {message.window_minutes} minutes'
    intro += '**'
    if kept < total:
        intro += f' (showing {kept}; remaining {total - kept} continue in the next message)'
    counts = [f'{severity_label(sev)} {sum(i.severity == sev for i in items)}'
              for sev in ('critical','high','medium','low') if any(i.severity == sev for i in items)]
    if counts:
        intro += '\n' + ' · '.join(counts)
    body = intro + '\n\n' + ''.join(_markdown_item(item, idx) for idx, item in enumerate(items)) + footer
    return truncate_bytes(body, max_bytes), kept
