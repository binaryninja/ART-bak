"""Pure bounded finding/evidence ZIP layout from server/finding_traffic_export.go.

Inputs must already be authorized snapshots. No storage, path reads, database,
HTTP, fetching, publication or HTML rendering. Freeform Markdown is retained as
text; consumers must sanitize it before HTML rendering. Evidence URLs are data,
never fetched or turned into hyperlinks by this builder. ZIP metadata is fixed
for reproducible output; unlike Go, duplicate finding/binding IDs are rejected.
"""
from dataclasses import dataclass
from datetime import datetime,timezone
import hashlib,io,json,zipfile
from .report import Finding,TrafficBinding,finding_filename,single_finding_markdown

@dataclass(frozen=True)
class ExportEvidence:
    finding_id:int
    binding_id:int
    manifest_json:bytes
    request_body:bytes
    response_body:bytes

def _positive(v):
    if type(v) is not int or not 0<v<2**63:raise ValueError('Positive int64 identity required')
    return v

def _date(value):
    # Reject custom formatter/tzinfo hooks before formatting or copying.
    if type(value) is not datetime or value.tzinfo is not None and type(value.tzinfo) is not timezone:
        raise ValueError('Exact datetime with fixed-offset timezone required')
    return value

def _nonnegative(value):
    if type(value) is not int or not 0<=value<2**63:raise ValueError('Nonnegative integer required')
    return value

def _pairs(pairs):
    out={}
    for k,v in pairs:
        if k in out:raise ValueError('Duplicate manifest field')
        out[k]=v
    return out

def findings_evidence_zip(findings,evidence,generated_at,*,max_bytes=16*1024*1024,max_entries=10000):
    """Return a verified in-memory ZIP, never persist or publish its contents.

    Manifest preserves supplied source DTO fields, including exact string IDs.
    Hash/length checks prove consistency with supplied body bytes, not provenance
    or permission. Missing/extra bindings fail rather than emit dangling links.
    """
    if type(findings) not in (list,tuple) or type(evidence) not in (list,tuple) or type(generated_at) is not datetime:raise ValueError('Explicit bounded snapshots required')
    generated_at=_date(generated_at)
    if type(max_bytes) is not int or not 1024<=max_bytes<=64*1024*1024 or type(max_entries) is not int or not 1<=max_entries<=10000:raise ValueError('Invalid export bounds')
    if len(findings)+5*len(evidence)>max_entries:raise ValueError('Export entry bound exceeded')
    if any(type(f) is not Finding or type(f.traffic_bindings) is not list for f in findings):raise ValueError('Explicit finding DTOs required')
    if sum(len(f.traffic_bindings) for f in findings)!=len(evidence):raise ValueError('Missing or extra evidence bindings')
    if any(type(b) is not TrafficBinding for f in findings for b in f.traffic_bindings):raise ValueError('Explicit binding DTOs required')
    size=0;expected={};seen=set()
    copied=[]
    for f in findings:
        if type(f) is not Finding or _positive(f.id) in seen:raise ValueError('Duplicate/invalid finding')
        seen.add(f.id)
        created=_date(f.created_at)
        versions=(_nonnegative(f.evidence_version),_nonnegative(f.report_evidence_version))
        values={}
        for name in ('name','vulnclass','severity','status','task_description','summary','evidence','report'):
            value=getattr(f,name)
            values[name]=value
            if type(value) is not str:raise ValueError('Invalid finding text')
            size+=len(value.encode('utf-8'))
        bindings=[]
        for binding in f.traffic_bindings:
            status=_nonnegative(binding.status)
            if status>999:raise ValueError('Invalid HTTP status')
            for value in (binding.role,binding.method,binding.url,binding.note):
                if type(value) is not str:raise ValueError('Invalid binding text')
                size+=len(value.encode('utf-8'))
            key=(f.id,_positive(binding.id))
            if key in expected:raise ValueError('Duplicate binding')
            fresh=TrafficBinding(binding.id,binding.role,binding.method,binding.url,status,binding.note)
            bindings.append(fresh);expected[key]=fresh
        if size>max_bytes:raise ValueError('Export input bound exceeded')
        copied.append(Finding(f.id,created,**values,evidence_version=versions[0],report_evidence_version=versions[1],traffic_bindings=bindings))
    findings=copied
    snapshots={}
    for item in evidence:
        if type(item) is not ExportEvidence:raise ValueError('Explicit evidence bytes required')
        key=(_positive(item.finding_id),_positive(item.binding_id))
        if key not in expected or key in snapshots:raise ValueError('Unknown/duplicate evidence identity')
        for value in (item.manifest_json,item.request_body,item.response_body):
            if type(value) is not bytes:raise ValueError('Binary snapshots required')
            size+=len(value)
        if size>max_bytes or len(item.manifest_json)>1048576:raise ValueError('Export input bound exceeded')
        m=json.loads(item.manifest_json,object_pairs_hook=_pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('Invalid JSON constant')))
        if type(m) is not dict or m.get('id')!=str(key[1]) or m.get('finding_id')!=str(key[0]):raise ValueError('Manifest identity mismatch')
        if set(m)-{'id','finding_id','snapshot_id','role','note','position','created_at','snapshot'}:raise ValueError('Unknown manifest fields')
        for name in ('id','finding_id','snapshot_id','role','note','created_at'):
            if name in m and type(m[name]) is not str:raise ValueError('Invalid manifest text')
        if 'position' in m:_nonnegative(m['position'])
        snap=m.get('snapshot');b=expected[key]
        if type(snap) is not dict or type(snap.get('id')) is not str or not snap['id'] or m.get('snapshot_id')!=snap['id']:raise ValueError('Snapshot identity mismatch')
        if set(snap)-{'id','source_traffic_id','captured_at','url','method','status','content_type','req_head','resp_head','req_hash','resp_hash','req_len','resp_len','created_at'}:raise ValueError('Unknown snapshot fields')
        for name,value in snap.items():
            if name in ('captured_at','status','req_len','resp_len'):_nonnegative(value)
            elif type(value) is not str:raise ValueError('Invalid snapshot text')
        if type(snap.get('status')) is not int:raise ValueError('Invalid snapshot status')
        if any(m.get(k)!=v for k,v in (('role',b.role),('note',b.note))) or any(snap.get(k)!=v for k,v in (('method',b.method),('url',b.url),('status',b.status))):raise ValueError('Report/manifest mismatch')
        for side,body in (('req',item.request_body),('resp',item.response_body)):
            if type(snap.get(side+'_len')) is not int or snap[side+'_len']!=len(body) or snap.get(side+'_hash')!=hashlib.sha256(body).hexdigest():raise ValueError('Evidence bytes do not match manifest')
            if type(snap.get(side+'_head','')) is not str:raise ValueError('Invalid HTTP head')
        snapshots[key]=(item,m)
    if set(expected)!=set(snapshots):raise ValueError('Missing evidence bytes')
    if size>max_bytes:raise ValueError('Export input bound exceeded')
    members=[];names=set();total=0
    def add(name,body):
        nonlocal total
        if name in names or name.startswith('/') or '\\' in name or any(v in ('','..','.') for v in name.split('/')):raise ValueError('Unsafe/duplicate ZIP member')
        names.add(name);total+=len(body)
        if total>max_bytes or len(names)>max_entries:raise ValueError('Export output bound exceeded')
        members.append((name,body))
    # Source export preserves caller ordering, unlike the summary report sorter.
    for f in findings:
        add(finding_filename(f),single_finding_markdown(f,generated_at).encode('utf-8'))
        for b in f.traffic_bindings:
            item,m=snapshots[(f.id,b.id)];prefix=f'evidence/{f.id}/{b.id}/'
            add(prefix+'manifest.json',(json.dumps(m,ensure_ascii=False,allow_nan=False,separators=(',',':'))+'\n').encode('utf-8'))
            for side,short,body in (('request','req',item.request_body),('response','resp',item.response_body)):
                head=m['snapshot'].get(short+'_head','').rstrip('\r\n').encode('utf-8')
                add(prefix+side+'.http',head+b'\n\n'+body);add(prefix+side+'.bin',body)
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_STORED,allowZip64=False) as archive:
        for name,body in members:
            info=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0));info.external_attr=0o600<<16;info.compress_type=zipfile.ZIP_STORED
            archive.writestr(info,body)
    result=out.getvalue()
    if len(result)>max_bytes:raise ValueError('ZIP envelope exceeds bound')
    with zipfile.ZipFile(io.BytesIO(result)) as archive:
        if archive.testzip() is not None:raise ValueError('ZIP verification failed')
    return result
