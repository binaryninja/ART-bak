"""Offline traffic capture storage port. No proxy listener or target requests.

SQLite tables and archive v1 wire format match the Go traffic package. Live
MITM, staged deletion/recovery and background reclamation are not yet ported.
"""
from __future__ import annotations
import base64
from contextlib import ExitStack
from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
from urllib.parse import urlsplit

MAX_INLINE_BODY = 256 * 1024
BLOB_PREVIEW = 8 * 1024
MAX_INDEX_BODY = 4 * 1024 * 1024
BINARY_TYPES = ('image/', 'audio/', 'video/', 'font/', 'application/octet-stream',
 'application/zip', 'application/gzip', 'application/x-gzip', 'application/x-tar',
 'application/x-7z-compressed', 'application/x-rar', 'application/pdf',
 'application/x-msdownload', 'application/vnd.android.package-archive',
 'application/java-archive', 'application/wasm', 'application/x-shockwave-flash')
META = 'id,ts,host,method,url_template,url,status,content_type,resp_len,path'
HASH = re.compile(r'^[0-9a-f]{64}$')


def is_binary_body(content_type: str, body: bytes) -> bool:
    return content_type.lower().split(';')[0].strip().startswith(BINARY_TYPES) or b'\0' in body[:512]


def normalize_search_host(raw: str) -> tuple[str, str]:
    raw = raw.strip()
    if not raw:
        raise ValueError('host is required')
    if '://' in raw or raw.count(':') == 1 or raw.startswith('['):
        u = urlsplit(raw if '://' in raw else '//' + raw)
        host = u.hostname or ''
        port = str(u.port) if u.port is not None else ''
    else:
        host, port = raw, ''
    if not host or (port and not 1 <= int(port) <= 65535):
        raise ValueError('invalid host or port')
    return host.strip('[]').lower(), port


def validate_proxy_url(raw: str):
    u = urlsplit(raw.strip())
    if u.scheme not in ('http', 'https', 'socks5') or not u.netloc:
        raise ValueError('proxy requires http, https or socks5 scheme and a host')
    return u


def template_path(path: str) -> str:
    patterns = ((r'^[0-9]+$', '{id}'),
                (r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$', '{uuid}'),
                (r'^[0-9a-fA-F]{16,}$', '{hex}'), (r'^[A-Za-z0-9_-]{24,}$', '{token}'))
    def segment(s):
        return next((replacement for pattern, replacement in patterns if re.fullmatch(pattern, s)), s)
    return '/'.join(map(segment, (path or '/').split('/')))


@dataclass
class PageQuery:
    host: str = ''
    method: str = ''
    query: str = ''
    body: str = ''
    path: str = ''
    status: str = ''
    resp_min: int = -1
    resp_max: int = -1
    sort: str = ''
    order: str = ''


class Traffic:
    def __init__(self, directory):
        self.directory = Path(directory)
        (self.directory / '_index').mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.directory / '_index/index.sqlite', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA busy_timeout=5000')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript(Path(__file__).with_name('schema.sql').read_text())
        # Keep identical index contract; older SQLite degrades explicitly like Go.
        try:
            self.db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS ex_fts USING fts5(content, tokenize='trigram', content='', contentless_delete=1)")
            self.fts = True
        except sqlite3.OperationalError:
            self.fts = False
        self.seq = 0

    def close(self):
        with self.lock:
            self.db.close()

    def blob_path(self, digest):
        digest = digest.strip().lower()
        if not HASH.fullmatch(digest):
            raise ValueError('invalid blob hash')
        root = self.directory / '_blobs/sha256' / digest[:2]
        for p in (root / (digest + '.bin'), root / digest[2:4] / (digest + '.bin')):
            if p.is_file():
                return p
        raise FileNotFoundError(digest)

    def blob(self, digest):
        path = self.blob_path(digest)
        return path.open('rb'), path.stat().st_size

    def blob_range(self, digest, offset=0, length=0):
        with self.blob_path(digest).open('rb') as f:
            total = f.seek(0, 2)
            f.seek(max(0, offset))
            return f.read(length if length > 0 else -1), total

    def _spill(self, body, content_type):
        text = not is_binary_body(content_type, body)
        index = body[:MAX_INDEX_BODY].decode('utf-8', 'replace') if text else ''
        if len(body) <= MAX_INLINE_BODY:
            return body, None, index
        digest = hashlib.sha256(body).hexdigest()
        p = self.directory / '_blobs/sha256' / digest[:2] / (digest + '.bin')
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_bytes(body)
        preview = body[:BLOB_PREVIEW].decode('utf-8', 'ignore').encode() if text else (
            f'[binary {content_type.strip() or "application/octet-stream"}, {len(body)} bytes, magic={body[:4].hex()}]').encode()
        return preview, digest, index

    def record(self, *, url, method='GET', status=200, content_type='', req_head='', resp_head='',
               req_body=b'', resp_body=b'', req_content_type='', timestamp=None, exchange_id=None):
        """Store an already captured exchange; performs no network activity."""
        with self.lock:
            ts = int(time.time()) if timestamp is None else timestamp
            self.seq += 1
            ident = exchange_id or f'{ts}-{self.seq % 10000:04d}'
            u = urlsplit(url)
            req, rb, ri = self._spill(req_body, req_content_type)
            resp, sb, si = self._spill(resp_body, content_type)
            with self.db:
                if self.fts:
                    self.db.execute('DELETE FROM ex_fts WHERE rowid IN (SELECT rowid FROM exchanges WHERE id=?)', (ident,))
                self.db.execute('INSERT OR REPLACE INTO exchanges VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                    (ident, ts, u.hostname or '', method, template_path(u.path), url, status, content_type, len(req_body), len(resp_body), ''))
                self.db.execute('INSERT OR REPLACE INTO exchange_bodies VALUES (?,?,?,?,?,?,?)', (ident, req_head, req, rb, resp_head, resp, sb))
                self.db.execute('DELETE FROM blob_refs WHERE exchange_id=?', (ident,))
                for h in (rb, sb):
                    if h:
                        self.db.execute('INSERT OR IGNORE INTO blob_refs VALUES (?,?)', (h, ident))
                if self.fts:
                    rowid = self.db.execute('SELECT rowid FROM exchanges WHERE id=?', (ident,)).fetchone()[0]
                    self.db.execute('INSERT INTO ex_fts(rowid,content) VALUES (?,?)', (rowid, '\n'.join((url, req_head, ri, resp_head, si))))
            return ident

    def _rows(self, sql, args=()):
        return [dict(r) for r in self.db.execute(sql, args)]

    def count(self):
        with self.lock:
            return self.db.execute('SELECT COUNT(*) FROM exchanges').fetchone()[0]

    def hosts(self):
        with self.lock:
            return self._rows('SELECT host, COUNT(*) AS count FROM exchanges GROUP BY host ORDER BY MAX(ts) DESC')

    def search(self, host='', page=0, size=100):
        size = size if 0 < size <= 500 else 100
        with self.lock:
            return self._rows('SELECT ' + META + ' FROM exchanges' + (' WHERE host=?' if host else '') + ' ORDER BY ts DESC LIMIT ? OFFSET ?', ([host] if host else []) + [size, page * size])

    def _fts_filter(self, term):
        return ('rowid IN (SELECT rowid FROM ex_fts WHERE ex_fts MATCH ?)', '"' + term.replace('"', '""') + '"') if self.fts and len(term) >= 3 else None

    def page(self, filters=None, page=0, size=100):
        f = filters or PageQuery()
        clauses, args = [], []
        def add(sql, *values):
            clauses.append(sql)
            args.extend(values)
        for value, column in ((f.host, 'host'), (f.path, 'url_template')):
            if value.strip():
                add(column + ' LIKE ?', '%' + value.strip() + '%')
        if f.method.strip():
            add('method=?', f.method.strip().upper())
        status = f.status.strip().lower()
        if re.fullmatch('[1-5]xx', status):
            add('status>=? AND status<?', int(status[0])*100, int(status[0])*100+100)
        elif re.fullmatch('[+-]?[0-9]+', status):
            add('status=?', int(status))
        for value, op in ((f.resp_min, '>='), (f.resp_max, '<=')):
            if value >= 0:
                add('resp_len' + op + '?', value)
        if f.query.strip():
            s = f.query.strip()
            meta = ' OR '.join(c + ' LIKE ?' for c in ('host','url','url_template','method','content_type','CAST(status AS TEXT)'))
            full = self._fts_filter(s)
            add('(' + meta + (' OR '+full[0] if full else '') + ')', *(['%'+s+'%']*6 + ([full[1]] if full else [])))
        full = self._fts_filter(f.body.strip())
        if full:
            add(*full)
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        col = f.sort.strip().lower()
        col = col if col in ('ts', 'status', 'resp_len') else 'ts'
        order = 'ASC' if f.order.strip().lower() == 'asc' else 'DESC'
        size = size if 0 < size <= 500 else 100
        with self.lock:
            total = self.db.execute('SELECT COUNT(*) FROM exchanges'+where, args).fetchone()[0]
            rows = self._rows('SELECT '+META+' FROM exchanges'+where+' ORDER BY '+col+' '+order+', id DESC LIMIT ? OFFSET ?', args+[size, max(0,page)*size])
        return rows, total

    def query(self, host, contains='', body_contains='', page=0, limit=3):
        hostname, port = normalize_search_host(host)
        clauses, args = ['host=?'], [hostname]
        if port:
            authority = ('['+hostname+']' if ':' in hostname else hostname) + ':' + port
            clauses.append('(url LIKE ? OR url LIKE ? OR url LIKE ?)')
            args += ['%://'+authority+'/%', '%://'+authority+'?%', '%://'+authority]
        if contains:
            clauses.append('(url LIKE ? OR url_template LIKE ?)')
            args += ['%'+contains+'%']*2
        if body_contains.strip():
            full = self._fts_filter(body_contains.strip())
            if not full:
                raise ValueError('body search requires FTS5 and at least three characters')
            clauses.append(full[0]); args.append(full[1])
        limit = min(limit, 10) if limit > 0 else 3
        with self.lock:
            return self._rows('SELECT '+META+' FROM exchanges WHERE '+' AND '.join(clauses)+' ORDER BY ts DESC LIMIT ? OFFSET ?', args+[limit,max(0,page)*limit])

    def _legacy_path(self, relative):
        p = (self.directory / relative).resolve()
        if not relative or not p.is_relative_to(self.directory.resolve()) or Path(relative).is_absolute():
            raise ValueError('invalid legacy traffic path')
        return p

    def get(self, ident):
        with self.lock:
            e = self.db.execute('SELECT * FROM exchanges WHERE id=?', (ident,)).fetchone()
            if e is None:
                raise KeyError(ident)
            b = self.db.execute('SELECT * FROM exchange_bodies WHERE id=?', (ident,)).fetchone()
            if b is None:
                p = self._legacy_path(e['path'])
                return tuple((p/name).read_text(errors='replace') if (p/name).exists() else '' for name in ('request.http','response.http'))
            def raw(side):
                value = b[side+'_head']+'\n'+(b[side+'_body'] or b'').decode('utf-8','replace')
                if b[side+'_blob']:
                    value += f"\n…[truncated] @blob sha256:{b[side+'_blob']} (len={e[side+'_len']})"
                return value
            return raw('req'), raw('resp')

    def read_evidence(self, ids, consume):
        """Readers are valid only for the duration of consume; serialized with writes."""
        with self.lock:
            for ident in ids:
                e = self.db.execute('SELECT * FROM exchanges WHERE id=?', (ident,)).fetchone()
                if e is None:
                    raise KeyError(ident)
                b = self.db.execute('SELECT * FROM exchange_bodies WHERE id=?', (ident,)).fetchone()
                with ExitStack() as stack:
                    item = dict(e)
                    for side, name in (('req','request'), ('resp','response')):
                        if b is None:
                            reader = stack.enter_context((self._legacy_path(e['path'])/(name+'.http')).open('rb'))
                            head = bytearray()
                            while True:
                                line = reader.readline((1 << 20)+1)
                                if not line or line in (b'\n', b'\r\n'):
                                    break
                                head.extend(line)
                                if len(head) > 1 << 20:
                                    raise ValueError('legacy traffic header too large')
                            item[side+'_head'] = head.decode('utf-8','replace')
                            item[side+'_hash'] = ''
                        else:
                            digest = b[side+'_blob']
                            reader = stack.enter_context(self.blob_path(digest).open('rb') if digest else io.BytesIO(b[side+'_body'] or b''))
                            item[side+'_head'], item[side+'_hash'] = b[side+'_head'], digest or ''
                        item[name] = reader
                    consume(item)

    def export_hosts(self, hosts, directory):
        if not hosts:
            return 0
        dest = Path(directory)
        (dest/'blobs').mkdir(parents=True, exist_ok=True, mode=0o700)
        hosts = sorted({h.strip().lower() for h in hosts if h.strip()})
        with self.lock:
            rows = self._rows('SELECT * FROM exchanges WHERE host IN ('+','.join('?' for _ in hosts)+') ORDER BY ts,id', hosts)
            exchanges, blobs = [], set()
            for e in rows:
                b = self.db.execute('SELECT * FROM exchange_bodies WHERE id=?', (e['id'],)).fetchone()
                legacy = e.pop('path')
                if b is None:
                    if not legacy:
                        raise ValueError('exchange has no body record')
                    p = self._legacy_path(legacy)
                    e.update(req_head=(p/'request.http').read_text(errors='replace') if (p/'request.http').exists() else '',
                             resp_head=(p/'response.http').read_text(errors='replace') if (p/'response.http').exists() else '')
                else:
                    for side in ('req','resp'):
                        e[side+'_head'] = b[side+'_head']
                        if b[side+'_body']:
                            e[side+'_body'] = base64.b64encode(b[side+'_body']).decode()
                        if b[side+'_blob']:
                            e[side+'_blob'] = b[side+'_blob']; blobs.add(b[side+'_blob'])
                exchanges.append(e)
            for digest in blobs:
                blob_file = dest/'blobs'/(digest+'.bin')
                blob_file.write_bytes(self.blob_path(digest).read_bytes())
                blob_file.chmod(0o600)
            (dest/'traffic.json').write_text(json.dumps({'version':1, 'exchanges':exchanges or None, 'blobs':sorted(blobs) or None}, separators=(',',':')))
            (dest/'traffic.json').chmod(0o600)
            return len(exchanges)

    def import_archive(self, directory):
        source = Path(directory)
        if not (source/'traffic.json').exists():
            return 0
        snapshot = json.loads((source/'traffic.json').read_text())
        if snapshot['version'] != 1:
            raise ValueError('unsupported traffic archive version')
        with self.lock:
            for digest in snapshot.get('blobs') or []:
                if not HASH.fullmatch(digest):
                    raise ValueError('invalid archived traffic blob')
                data = (source/'blobs'/(digest+'.bin')).read_bytes()
                if hashlib.sha256(data).hexdigest() != digest:
                    raise ValueError('traffic blob checksum mismatch')
                dest = self.directory/'_blobs/sha256'/digest[:2]/(digest+'.bin')
                dest.parent.mkdir(parents=True, exist_ok=True)
                if not dest.exists():
                    dest.write_bytes(data)
            imported = 0
            with self.db:
                for item in snapshot.get('exchanges') or []:
                    keys = 'id ts host method url_template url status content_type req_len resp_len'.split()
                    cursor = self.db.execute('INSERT OR IGNORE INTO exchanges VALUES (?,?,?,?,?,?,?,?,?,?,?)', [item[k] for k in keys]+[''])
                    if not cursor.rowcount:
                        continue
                    bodies = [item['id']]
                    index = [item['url']]
                    for side in ('req','resp'):
                        body = base64.b64decode(item.get(side+'_body') or '', validate=True)
                        digest = item.get(side+'_blob') or None
                        bodies += [item.get(side+'_head',''), body, digest]
                        index += [item.get(side+'_head',''), body.decode('utf-8','replace')]
                        if digest:
                            self.db.execute('INSERT OR IGNORE INTO blob_refs VALUES (?,?)', (digest,item['id']))
                    self.db.execute('INSERT INTO exchange_bodies VALUES (?,?,?,?,?,?,?)', bodies)
                    if self.fts:
                        rowid = self.db.execute('SELECT rowid FROM exchanges WHERE id=?', (item['id'],)).fetchone()[0]
                        self.db.execute('INSERT INTO ex_fts(rowid,content) VALUES (?,?)', (rowid,'\n'.join(index)))
                    imported += 1
            return imported
