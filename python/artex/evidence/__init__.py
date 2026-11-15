"""Durable content-addressed evidence port of evidence/store.go.

Metadata operations require an injected repository adapter. It must implement
with_evidence_tx(callback), holding the PostgreSQL advisory lock 7337741004
through callback execution and commit. No in-memory lock is substituted for it.
"""
from dataclasses import dataclass, replace
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import tempfile
import time


@dataclass(frozen=True)
class TrafficRef:
    traffic_id: str
    role: str = ""
    note: str = ""


def normalize_traffic_refs(refs):
    result, seen = [], set()
    for ref in refs:
        ref = replace(ref, traffic_id=ref.traffic_id.strip(), role=ref.role or "supporting")
        if not ref.traffic_id:
            raise ValueError("traffic_id must not be empty")
        if ref.role not in {"baseline", "proof", "verification", "supporting"}:
            raise ValueError(f"invalid traffic role {ref.role!r}")
        if ref.traffic_id not in seen:
            seen.add(ref.traffic_id)
            result.append(ref)
    return result


def _clean(value):
    if isinstance(value, bytes):
        # Go strings.ToValidUTF8 replaces each contiguous run of invalid bytes.
        value = value.decode("utf-8", "surrogateescape")
    value = re.sub(r"[\ud800-\udfff]+", "\ufffd", value)
    return value.replace("\0", "")


@dataclass(frozen=True)
class Snapshot:
    id: str = ""
    source_traffic_id: str = ""
    captured_at: int = 0
    url: str = ""
    method: str = ""
    status: int = 0
    content_type: str = ""
    req_head: str = ""
    resp_head: str = ""
    req_hash: str = ""
    resp_hash: str = ""
    req_len: int = 0
    resp_len: int = 0
    created_at: str = "0001-01-01T00:00:00Z"

    def normalize(self):
        names = ("source_traffic_id", "url", "method", "content_type", "req_head", "resp_head")
        return replace(self, **{name: _clean(getattr(self, name)) for name in names})


def snapshot_id(snapshot):
    """Match Go's ordered struct JSON, including HTML escaping and zero time."""
    s = replace(snapshot.normalize(), id="", created_at="0001-01-01T00:00:00Z")
    data = dict(vars(s))
    for key in ("req_head", "resp_head"):
        if not data[key]:
            del data[key]
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    for char, escaped in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"), ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
        raw = raw.replace(char, escaped)
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class PreparedEvidence:
    ref: TrafficRef
    snapshot: Snapshot


def hash_path(directory, digest):
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("invalid evidence hash")
    return Path(directory) / "blobs" / digest[:2] / (digest + ".bin")


def verify_file(path, digest, length):
    hasher, size = hashlib.sha256(), 0
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
            size += len(chunk)
    if size != length or hasher.hexdigest() != digest:
        raise ValueError(f"evidence body verification failed: {digest}")


class Store:
    def __init__(self, db=None, traffic=None, directory="."):
        self.db, self.traffic, self.directory = db, traffic, Path(directory)

    def _transaction(self, callback):
        if self.db is None:
            raise RuntimeError("metadata repository adapter required")
        return self.db.with_evidence_tx(callback)

    def write_body(self, reader, expected_length, expected_hash=""):
        stage = self.directory / ".staging"
        stage.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="body-", dir=stage)
        try:
            hasher, size = hashlib.sha256(), 0
            with os.fdopen(fd, "wb") as output:
                for chunk in iter(lambda: reader.read(1024 * 1024), b""):
                    output.write(chunk)
                    hasher.update(chunk)
                    size += len(chunk)
                digest = hasher.hexdigest()
                if size != expected_length:
                    raise ValueError(f"incomplete body: expected {expected_length} bytes, read {size}")
                if expected_hash and expected_hash != digest:
                    raise ValueError("original traffic body hash mismatch")
                output.flush()
                os.fsync(output.fileno())
            target = hash_path(self.directory, digest)
            if target.exists():
                verify_file(target, digest, size)
                os.utime(target, None)
                return digest
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.replace(temporary, target)
            directory_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            return digest
        finally:
            Path(temporary).unlink(missing_ok=True)

    def open_body(self, snapshot, side):
        if side not in {"request", "response"}:
            raise ValueError("side must be request or response")
        digest, length = ((snapshot.req_hash, snapshot.req_len) if side == "request"
                          else (snapshot.resp_hash, snapshot.resp_len))
        path = hash_path(self.directory, digest)
        verify_file(path, digest, length)
        return path.open("rb"), length

    def _copy_snapshots(self, snapshots, destination):
        for snapshot in snapshots:
            for side, digest in (("request", snapshot.req_hash), ("response", snapshot.resp_hash)):
                stream, length = self.open_body(snapshot, side)
                with stream:
                    target = hash_path(destination, digest)
                    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    try:
                        fd = os.open(target, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
                    except FileExistsError:
                        verify_file(target, digest, length)
                        continue
                    with os.fdopen(fd, "wb") as output:
                        shutil.copyfileobj(stream, output)
                        output.flush()
                        os.fsync(output.fileno())

    def copy_snapshots(self, snapshots, destination):
        return self._transaction(lambda tx: self._copy_snapshots(snapshots, destination))

    def with_installed_snapshots(self, snapshots, source, restore):
        def install(tx):
            for snapshot in snapshots:
                if snapshot.id != snapshot_id(snapshot):
                    raise ValueError("archive evidence snapshot metadata hash mismatch")
                for digest, length in ((snapshot.req_hash, snapshot.req_len), (snapshot.resp_hash, snapshot.resp_len)):
                    with hash_path(source, digest).open("rb") as stream:
                        self.write_body(stream, length, digest)
            return restore()
        return self._transaction(install)

    def install_snapshots(self, snapshots, source):
        return self.with_installed_snapshots(snapshots, source, lambda: None)

    def prepare(self, refs):
        refs = normalize_traffic_refs(refs)
        if not refs:
            return []
        by_id, result = {ref.traffic_id: ref for ref in refs}, []
        def consume(exchange):
            request = self.write_body(exchange.request, exchange.req_len, exchange.req_hash)
            response = self.write_body(exchange.response, exchange.resp_len, exchange.resp_hash)
            s = Snapshot(source_traffic_id=exchange.id, captured_at=exchange.ts,
                         req_hash=request, resp_hash=response,
                         **{name: getattr(exchange, name) for name in
                            ("url", "method", "status", "content_type", "req_head", "resp_head", "req_len", "resp_len")}).normalize()
            result.append(PreparedEvidence(by_id[exchange.id], replace(s, id=snapshot_id(s))))
        self.traffic.read_evidence(list(by_id), consume)
        return result

    def record(self, finding_input, refs):
        def record(tx):
            tx.lock_task_evidence(finding_input.task_id)
            return tx.record_finding(finding_input, self.prepare(refs))
        return self._transaction(record)

    def bind(self, finding_id, refs):
        def bind(tx):
            tx.lock_finding_evidence(finding_id, None)
            tx.add_finding_traffic(finding_id, self.prepare(refs))
            return tx.finding_traffic(finding_id)
        return self._transaction(bind)

    def with_binding(self, finding_id, binding_id, callback):
        def resolve(tx):
            for binding in tx.finding_traffic(finding_id).bindings:
                if binding.id == binding_id:
                    return callback(binding)
            raise LookupError("traffic evidence not found")
        return self._transaction(resolve)

    def binding(self, finding_id, binding_id):
        return self.with_binding(finding_id, binding_id, lambda binding: binding)

    def stage_findings_export(self, findings, destination, copy_bodies):
        def stage(tx):
            snapshots = []
            for finding in findings:
                traffic = tx.finding_traffic(finding.id)
                finding.traffic_bindings = traffic.bindings
                finding.traffic_count = len(traffic.bindings)
                finding.evidence_version = traffic.version
                finding.report_evidence_version = traffic.report_version
                finding.report = tx.finding_report(finding.id)
                snapshots.extend(binding.snapshot for binding in traffic.bindings)
            if copy_bodies:
                self._copy_snapshots(snapshots, destination)
        return self._transaction(stage)

    def collect(self, now=None):
        now = time.time() if now is None else now
        def collect(tx):
            # Adapter marks newly unreferenced snapshots at now, deletes only
            # those older than now-24h, then returns both remaining body hashes.
            refs = set(tx.collect_evidence_references(now, now - 86400))
            for root in (self.directory / "blobs", self.directory / ".staging"):
                if not root.exists():
                    continue
                for directory, _, filenames in os.walk(root):
                    for name in filenames:
                        path = Path(directory) / name
                        if name.removesuffix(".bin") not in refs and now - path.stat().st_mtime >= 86400:
                            path.unlink()
        return self._transaction(collect)

    def run_gc(self, stop_event):
        while not stop_event.wait(3600):
            try:
                self.collect()
            except Exception:
                logging.exception("[evidence] cleanup failed")
