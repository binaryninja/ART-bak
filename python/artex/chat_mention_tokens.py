"""Pure visible-token parsing from server/chat_mentions.go.

Labels are display metadata, not trusted names, record content or authority.
This module does not load records, expand prompts, accept turns or authorize
anything. Existing HTTP mention rejection remains unchanged until a separately
authorized snapshot-expansion path is composed.
"""
from dataclasses import dataclass
import re


class MentionTokenInvalid(ValueError):
    """A recognized mention has an invalid ID or exceeds the unique-ref limit."""


@dataclass(frozen=True)
class MentionRef:
    kind: str
    id: int
    name: str


_KINDS = {
    'Finding': 'finding', 'Asset': 'asset', 'Company': 'company',
    'Endpoint': 'endpoint', 'Application': 'app', 'App': 'app',
    'Domain': 'root_domain', 'Subdomain': 'subdomain', 'Service': 'service',
    '漏洞': 'finding', '资产': 'asset', '企业': 'company', '接口': 'endpoint',
    'IP': 'ip', '应用': 'app', '域名': 'root_domain', '子域名': 'subdomain',
    '服务': 'service',
}
_PREFIX = re.compile(
    r'@\[(Finding|Asset|Company|Endpoint|Application|App|Domain|Subdomain|Service|漏洞|资产|企业|接口|IP|应用|域名|子域名|服务)#([0-9]+)'
)
_BOUNDARY = re.compile(r'[\]\r\n]')
_MAX_ID = str(2**63 - 1)


def parse_chat_mentions(message: str) -> tuple[MentionRef, ...]:
    """Return at most ten unique kind/ID pairs in first-appearance order.

    Unknown labels and malformed token syntax stay plain text, matching Go.
    A syntactically recognized zero/overflow ID rejects the whole parse, even
    after duplicates. The first alias is retained as name; arbitrary text after
    the ID is deliberately ignored. No mutable result or input is retained.
    Python-only non-string and unpaired-surrogate input fail closed. Transport
    must bound message size; this parser adds no source-absent length limit.
    """
    if type(message) is not str:
        raise MentionTokenInvalid('A text message is required')
    try:
        message.encode('utf-8')
    except UnicodeError:
        raise MentionTokenInvalid('Invalid message encoding') from None
    refs = []
    seen = set()
    # A backtracking regex over the full display field would rescan the same
    # unterminated suffix for every nested opener. Search prefixes separately
    # and cache the next display boundary, advancing both scans monotonically.
    position = 0
    boundary = -1
    while (match := _PREFIX.search(message, position)) is not None:
        end = match.end()
        position = end
        if end == len(message):
            continue
        if message[end] == ']':
            position = end + 1
        elif message[end] == ' ':
            if boundary < end:
                stop = _BOUNDARY.search(message, end + 1)
                boundary = len(message) if stop is None else stop.start()
            if boundary == len(message) or message[boundary] != ']':
                continue
            position = boundary + 1
        else:
            continue
        name, digits = match.group(1, 2)
        # ParseInt accepts leading zeros. Normalize before conversion to avoid
        # Python's integer-string digit ceiling and excessive big-int work.
        digits = digits.lstrip('0')
        if not digits or len(digits) > 19 or (len(digits) == 19 and digits > _MAX_ID):
            raise MentionTokenInvalid('Invalid reference ID; select the record again')
        record_id = int(digits)
        kind = _KINDS[name]
        key = (kind, record_id)
        if key in seen:
            continue
        seen.add(key)
        refs.append(MentionRef(kind, record_id, name))
        if len(refs) > 10:
            raise MentionTokenInvalid('A message may reference at most 10 records')
    return tuple(refs)
