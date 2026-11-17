"""Memory-only, deliberately narrow ZIP plan. Archive bytes are inert data.

No extraction API, filesystem access, codec fallback, encrypted entries, ZIP64,
streaming descriptors, unknown extras, comments, preamble, trailer or multiple
disks. Bounded paired Info-ZIP timestamp/UID-GID extras are checked then ignored.
Names are ASCII or explicitly flagged UTF-8, canonical NFC, collision-free.
Actual bounded raw decompression, not ZipExtFile's declared-length truncation,
proves complete stream length/CRC before any installer can start.
"""
from dataclasses import dataclass
import hashlib
import stat
import struct
import unicodedata
import zlib

from . import parse_skill, skill_rel_path, valid_skill_name

MAX_ARCHIVE_BYTES = 1 << 20
MAX_TOTAL_BYTES = 1 << 20
MAX_FILE_BYTES = 1 << 18
MAX_ENTRIES = 128
MAX_EXTRA_BYTES = 32
_PARSE = parse_skill


class ArchiveLimit(ValueError): pass


@dataclass(frozen=True)
class ArchiveFile:
    path: str
    data: bytes
    sha256: str


@dataclass(frozen=True)
class ArchivePlan:
    name: str
    directories: tuple[str, ...]
    files: tuple[ArchiveFile, ...]
    archive_sha256: str
    total_bytes: int


def _path(value):
    if (type(value) is not str or skill_rel_path(value) != value
            or unicodedata.normalize('NFC', value) != value): raise ValueError()
    parts = value.split('/')
    if len(parts) > 32 or any(len(p.encode('utf-8')) > 255 or p != p.strip()
                             or p.endswith('.') for p in parts): raise ValueError()
    for part in parts:
        stem = part.split('.')[0].casefold()
        if stem in {'con','prn','aux','nul',*(f'com{i}' for i in range(1,10)),*(f'lpt{i}' for i in range(1,10))}:
            raise ValueError()
    return value


def _extras(data, *, central):
    """Only inert UT / new Unix metadata, never filename/feature overrides.

    Each header has at most one of each allowed tag. UT's central form contains
    only mtime, while its flags describe the local timestamps. UID/GID are
    version 1, bounded to 1..4 bytes each. No values are restored or used as
    authority. The returned projection only checks agreement between headers.
    """
    if len(data) > MAX_EXTRA_BYTES: raise ValueError()
    cursor = 0; result = {}
    while cursor < len(data):
        if cursor+4 > len(data): raise ValueError()
        tag,size = struct.unpack_from('<HH',data,cursor)
        cursor += 4
        if tag not in (0x5455,0x7875) or tag in result or cursor+size > len(data): raise ValueError()
        payload = data[cursor:cursor+size]; cursor += size
        if tag == 0x5455:
            if not payload or payload[0] & ~7: raise ValueError()
            flags = payload[0]
            count = (flags & 1) if central else flags.bit_count()
            if len(payload) != 1+4*count: raise ValueError()
            # atime/ctime have no central representation. Validate their local
            # lengths, but intentionally neither interpret nor apply them.
            result[tag] = (flags,payload[1:5] if flags & 1 else b'')
        else:
            if len(payload) < 5 or payload[0] != 1: raise ValueError()
            uid_size = payload[1]
            if not 1 <= uid_size <= 4 or 2+uid_size >= len(payload): raise ValueError()
            gid_size = payload[2+uid_size]
            if not 1 <= gid_size <= 4 or len(payload) != 3+uid_size+gid_size: raise ValueError()
            result[tag] = payload
    return result


def plan_archive(data):
    """Validate a complete standard stored/raw-deflate ZIP before any effects."""
    if type(data) is not bytes: raise ValueError()
    if len(data) > MAX_ARCHIVE_BYTES: raise ArchiveLimit()
    if len(data) < 22 or parse_skill is not _PARSE: raise ValueError()
    sig, disk, central_disk, disk_count, count, size, offset, comment = struct.unpack_from('<4s4H2IH',data,len(data)-22)
    if (sig != b'PK\x05\x06' or disk or central_disk or comment or disk_count != count
            or offset + size != len(data)-22 or count == 0): raise ValueError()
    if count > MAX_ENTRIES: raise ArchiveLimit()
    cursor = offset; local = 0; total = 0; members = []; seen = set(); aliases = {}
    for _ in range(count):
        if cursor + 46 > len(data)-22: raise ValueError()
        fields = struct.unpack_from('<4s6H3I5H2I',data,cursor)
        (sig,made,need,flags,method,mtime,mdate,crc,compressed,expanded,nlen,xlen,clen,
         start_disk,internal,external,local_offset) = fields
        # Producer version is informational; only needed-version gates features.
        if (sig != b'PK\x01\x02' or made >> 8 not in (0,3)
                or need not in (10,20) or flags & ~0x800 or method not in (0,8)
                or xlen > MAX_EXTRA_BYTES or clen or start_disk or internal not in (0,1) or not nlen
                or local_offset != local or cursor+46+nlen+xlen > len(data)-22): raise ValueError()
        if expanded > MAX_FILE_BYTES: raise ArchiveLimit()
        raw_name = data[cursor+46:cursor+46+nlen]
        central_extra = _extras(data[cursor+46+nlen:cursor+46+nlen+xlen],central=True)
        name = raw_name.decode('utf-8' if flags & 0x800 else 'ascii')
        directory = name.endswith('/')
        path = _path(name[:-1] if directory else name)
        if path in seen: raise ValueError()
        seen.add(path)
        parts = path.split('/')
        for index in range(1,len(parts)+1):
            prefix = '/'.join(parts[:index]); alias = unicodedata.normalize('NFC',prefix.casefold())
            if alias in aliases and aliases[alias] != prefix: raise ValueError()
            aliases[alias] = prefix
        unix_mode = external >> 16; kind = stat.S_IFMT(unix_mode)
        if (kind not in (0,stat.S_IFREG,stat.S_IFDIR) or unix_mode & 0o7000
                or external & 0xffff & ~0x30
                or kind == stat.S_IFREG and directory or kind == stat.S_IFDIR and not directory
                or external & 0x10 and not directory): raise ValueError()
        if local+30 > offset: raise ValueError()
        head = struct.unpack_from('<4s5H3I2H',data,local)
        local_xlen = head[-1]
        if (head[:-1] != (b'PK\x03\x04',need,flags,method,mtime,mdate,crc,compressed,expanded,nlen)
                or local_xlen > MAX_EXTRA_BYTES): raise ValueError()
        name_end = local+30+nlen
        begin = name_end+local_xlen; end = begin+compressed
        if end > offset or data[local+30:name_end] != raw_name: raise ValueError()
        local_extra = _extras(data[name_end:begin],central=False)
        # This subset requires paired tags and identical common metadata.
        if local_extra != central_extra: raise ValueError()
        packed = data[begin:end]
        if method == 0:
            content = packed
        else:
            decoder = zlib.decompressobj(-15)
            try: content = decoder.decompress(packed,MAX_FILE_BYTES+1)
            except zlib.error as exc: raise ValueError() from exc
            if len(content) > MAX_FILE_BYTES: raise ArchiveLimit()
            if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail: raise ValueError()
        if len(content) > MAX_FILE_BYTES: raise ArchiveLimit()
        if len(content) != expanded or zlib.crc32(content) != crc: raise ValueError()
        if directory and content: raise ValueError()
        total += len(content)
        if total > MAX_TOTAL_BYTES: raise ArchiveLimit()
        members.append((path,directory,content))
        local = end; cursor += 46+nlen+xlen
    if cursor != len(data)-22 or local != offset: raise ValueError()
    kinds = {path: directory for path,directory,_ in members}
    for path in kinds:
        parts = path.split('/')
        if any(kinds.get('/'.join(parts[:i])) is False for i in range(1,len(parts))): raise ValueError()
    manifests = [path for path,directory,_ in members if not directory and path.split('/')[-1].casefold() == 'skill.md']
    if len(manifests) != 1 or manifests[0].split('/')[-1] != 'SKILL.md': raise ValueError()
    manifest = manifests[0]
    if len(manifest.split('/')) > 2: raise ValueError()
    prefix = manifest[:-len('SKILL.md')]
    selected = []
    for path,directory,content in members:
        if prefix and path == prefix[:-1] and directory: continue
        if prefix and not path.startswith(prefix): raise ValueError()
        relative = path[len(prefix):]
        _path(relative)
        selected.append((relative,directory,content))
    manifest_data = next(content for path,directory,content in selected if path == 'SKILL.md')
    text = manifest_data.decode('utf-8')
    if '\0' in text: raise ValueError()
    try: parsed = _PARSE(text)
    except (AttributeError,TypeError,RecursionError) as exc: raise ValueError() from exc
    if (type(parsed.name) is not str or not valid_skill_name(parsed.name)
            or len(parsed.name.encode('utf-8')) > 255 or unicodedata.normalize('NFC',parsed.name) != parsed.name
            or 'mcps' in parsed.metadata): raise ValueError()
    # Also reject any lone surrogate decoded out of a quoted metadata scalar.
    for value in parsed.metadata.values():
        if type(value) is not str or '\0' in value: raise ValueError()
        value.encode('utf-8')
    dirs = set(); files = []
    for path,directory,content in selected:
        parts = path.split('/')
        dirs.update('/'.join(parts[:i]) for i in range(1,len(parts)))
        if directory: dirs.add(path)
        else: files.append(ArchiveFile(path,content,hashlib.sha256(content).hexdigest()))
    if len(dirs)+len(files) > MAX_ENTRIES: raise ArchiveLimit()
    return ArchivePlan(parsed.name,tuple(sorted(dirs,key=lambda p:(p.count('/'),p))),
                       tuple(sorted(files,key=lambda f:f.path)),hashlib.sha256(data).hexdigest(),total)
