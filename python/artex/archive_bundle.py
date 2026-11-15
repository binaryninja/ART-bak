"""Offline ARTEX task archive .tar.zst format, not lifecycle orchestration.

Source server/task_archive_package.go, db/task_archives{,_restore}.go and
 evidence/store.go: manifest.json snapshot v1..3, optional v2+ streamed
 database/llm_records.ndjson, files/tasks/<id>, files/transcripts/exp<id>-*,
 evidence/blobs/<hash[:2]>/<sha256>.bin. SHA256 covers COMPRESSED package bytes.

Caller supplies explicit snapshot and byte payloads; no live tree traversal,
symlinks, DB exports, restore/extraction, deletion, pruning or ready-state writes.
Unknown compatible manifest/table fields survive. This bounded in-memory subset
uses existing zstandard package (no installer), level10/checksum like Go. Local
publish is create-only, atomic and fsynced; failure after link may be uncertain.
Validation establishes package structure/checksums, not relational restore safety,
authorized provenance, completeness of omitted tables, or database compatibility.
"""
from dataclasses import dataclass
import hashlib,io,json,os,re,tarfile,uuid

class BundleInvalid(ValueError):pass
class BundleUnavailable(RuntimeError):pass

@dataclass(frozen=True)
class Bundle:
    manifest:dict
    files:dict
    sha256:str
    original_size:int
    compressed_size:int


def _json(raw):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise BundleInvalid('Duplicate JSON key')
            result[key]=value
        return result
    try:return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
    except (ValueError,UnicodeError,RecursionError):raise BundleInvalid('Invalid archive JSON') from None

def _path(name):
    if not isinstance(name,str) or not name or '\\' in name or '\0' in name or ':' in name or name.startswith('/') or any(part in ('','.','..') for part in name.split('/')):raise BundleInvalid('Unsafe archive member path')
    if len(name.encode('utf-8'))>4096:raise BundleInvalid('Archive member path too long')
    return name

def _limits(max_bytes,max_entries):
    if type(max_bytes) is not int or not 1<=max_bytes<=1024**3 or type(max_entries) is not int or not 1<=max_entries<=1000000:raise BundleInvalid('Invalid archive limits')

def _codec():
    try:import zstandard;return zstandard
    except ImportError:raise BundleUnavailable('Explicit zstandard dependency required') from None

def _validate(manifest,files):
    if not isinstance(manifest,dict) or type(manifest.get('format_version')) is not int or manifest['format_version'] not in (1,2,3):raise BundleInvalid('Unsupported archive format')
    for name in ('task_id','exploration_id'):
        if type(manifest.get(name)) is not int or not 0<manifest[name]<2**63:raise BundleInvalid('Invalid archive owner')
    tables=manifest.get('tables')
    if not isinstance(tables,dict) or any(not isinstance(value,list) for value in tables.values()):raise BundleInvalid('Invalid archive tables')
    if not isinstance(manifest.get('data_counts',{}),dict):raise BundleInvalid('Invalid archive data counts')
    streams=manifest.get('streamed_tables') or {}
    if not isinstance(streams,dict):raise BundleInvalid('Invalid streamed table metadata')
    if streams and (manifest['format_version']<2 or streams!={'llm_records':'database/llm_records.ndjson'}):raise BundleInvalid('Invalid streamed table metadata')
    if streams:
        raw=files.get('database/llm_records.ndjson')
        if raw is None:raise BundleInvalid('Missing streamed table')
        count=0
        for line in raw.splitlines():
            if not line.strip():continue
            if not isinstance(_json(line),dict):raise BundleInvalid('Streamed record must be an object')
            count+=1
        expected=manifest.get('data_counts',{}).get('llm_records')
        if expected is not None and (type(expected) is not int or expected!=count):raise BundleInvalid('Streamed record count mismatch')
    elif 'database/llm_records.ndjson' in files:raise BundleInvalid('Unreferenced streamed table')
    for item in tables.get('traffic_evidence_snapshots',[]):
        if not isinstance(item,dict):raise BundleInvalid('Invalid evidence snapshot')
        for side in ('req','resp'):
            digest=item.get(side+'_hash');length=item.get(side+'_len')
            if not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest) or type(length) is not int or length<0:raise BundleInvalid('Invalid evidence identity')
            raw=files.get(f'evidence/blobs/{digest[:2]}/{digest}.bin')
            if raw is None or len(raw)!=length or hashlib.sha256(raw).hexdigest()!=digest:raise BundleInvalid('Missing or corrupt evidence blob')
    task_prefix=f"files/tasks/{manifest['task_id']}/";transcript_prefix=f"files/transcripts/exp{manifest['exploration_id']}-"
    for name,raw in files.items():
        _path(name)
        if not (name.startswith(task_prefix) or name.startswith(transcript_prefix) or name=='database/llm_records.ndjson' or re.fullmatch(r'evidence/blobs/[a-f0-9]{2}/[a-f0-9]{64}\.bin',name)):raise BundleInvalid('Unsupported archive payload path')
        if name.startswith('evidence/'):
            digest=name.rsplit('/',1)[1][:-4]
            if name.split('/')[2]!=digest[:2] or hashlib.sha256(raw).hexdigest()!=digest:raise BundleInvalid('Corrupt evidence blob')


def build_bundle(manifest,files,*,max_bytes=64<<20,max_entries=10000):
    _limits(max_bytes,max_entries)
    if not isinstance(files,dict) or any(not isinstance(raw,bytes) for raw in files.values()):raise BundleInvalid('Explicit byte payload mapping required')
    try:encoded=json.dumps(manifest,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf-8')+b'\n'
    except (TypeError,ValueError,UnicodeError):raise BundleInvalid('Invalid archive manifest') from None
    manifest=_json(encoded);_validate(manifest,files)
    if len(files)+1>max_entries or len(encoded)+sum(map(len,files.values()))>max_bytes:raise BundleInvalid('Archive exceeds limits')
    buffer=io.BytesIO()
    with tarfile.open(fileobj=buffer,mode='w',format=tarfile.PAX_FORMAT) as archive:
        for name,raw in sorted(dict(files,**{'manifest.json':encoded}).items()):
            header=tarfile.TarInfo(name);header.size=len(raw);header.mode=0o600;header.mtime=0
            archive.addfile(header,io.BytesIO(raw))
    raw=buffer.getvalue()
    if len(raw)>max_bytes:raise BundleInvalid('Archive tar exceeds limit')
    compressed=_codec().ZstdCompressor(level=10,write_checksum=True).compress(raw)
    if len(compressed)>max_bytes:raise BundleInvalid('Compressed archive exceeds limit')
    return compressed


def read_bundle(data,*,expected_sha=None,max_bytes=64<<20,max_entries=10000):
    _limits(max_bytes,max_entries)
    if not isinstance(data,bytes) or len(data)>max_bytes:raise BundleInvalid('Invalid archive size')
    digest=hashlib.sha256(data).hexdigest()
    if expected_sha is not None and (not isinstance(expected_sha,str) or not re.fullmatch('[a-fA-F0-9]{64}',expected_sha) or expected_sha.lower()!=digest):raise BundleInvalid('Archive checksum mismatch')
    # zstandard 0.25.0 passes this value to ZSTD_DCtx_setMaxWindowSize in
    # BYTES despite its inherited docstring saying KiB; a regression verifies it.
    try:
        with _codec().ZstdDecompressor(max_window_size=max(1024,max_bytes)).stream_reader(io.BytesIO(data)) as reader:raw=reader.read(max_bytes+1)
    except BundleUnavailable:raise
    except Exception:raise BundleInvalid('Invalid zstd archive') from None
    if len(raw)>max_bytes:raise BundleInvalid('Expanded archive exceeds limit')
    files={};names=set();directories=set()
    try:
        with tarfile.open(fileobj=io.BytesIO(raw),mode='r:') as archive:
            for entry in archive:
                name=_path(entry.name.rstrip('/') if entry.isdir() else entry.name)
                if name in names or len(names)>=max_entries:raise BundleInvalid('Duplicate or excessive archive entries')
                names.add(name)
                if entry.isdir():directories.add(name);continue
                if not entry.isfile() or entry.sparse is not None or entry.size<0:raise BundleInvalid('Unsupported archive entry type')
                payload=archive.extractfile(entry).read(max_bytes+1)
                if len(payload)!=entry.size or sum(map(len,files.values()))+len(payload)>max_bytes:raise BundleInvalid('Archive size mismatch')
                files[name]=payload
    except (tarfile.TarError,OSError):raise BundleInvalid('Invalid tar archive') from None
    for name in names:
        parts=name.split('/')
        if any('/'.join(parts[:i]) in files for i in range(1,len(parts))):raise BundleInvalid('Archive file/directory collision')
    encoded=files.pop('manifest.json',None)
    if encoded is None:raise BundleInvalid('Missing archive manifest')
    manifest=_json(encoded);_validate(manifest,files)
    return Bundle(manifest,files,digest,len(encoded)+sum(map(len,files.values())),len(data))


def publish_bundle(root,name,data,**validation):
    """Publish only a validated new package in an explicit existing POSIX directory."""
    bundle=read_bundle(data,**validation)
    if os.name!='posix' or not hasattr(os,'O_NOFOLLOW'):raise BundleUnavailable('POSIX required')
    if not isinstance(name,str) or not re.fullmatch(r'task-[0-9]+-[0-9]+\.tar\.zst',name):raise BundleInvalid('Invalid package name')
    if int(name.split('-')[1])!=bundle.manifest['task_id']:raise BundleInvalid('Package task identity mismatch')
    root=os.fspath(root)
    if not isinstance(root,str) or not root.startswith('/') or any(p in ('.','..') for p in root.split('/')):raise BundleInvalid('Absolute existing directory required')
    directory=os.open('/',os.O_RDONLY|os.O_DIRECTORY);temporary='.archive-'+uuid.uuid4().hex
    try:
        for component in filter(None,root.split('/')):
            child=os.open(component,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory);os.close(directory);directory=child
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory)
        try:
            with os.fdopen(fd,'wb') as output:output.write(data);output.flush();os.fsync(output.fileno())
            os.link(temporary,name,src_dir_fd=directory,dst_dir_fd=directory,follow_symlinks=False)
            os.fsync(directory)
        finally:
            os.unlink(temporary,dir_fd=directory);os.fsync(directory)
    finally:os.close(directory)
