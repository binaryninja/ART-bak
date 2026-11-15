"""Best-effort asset enrichment, ported from Go enrich.

Store/resolver adapters are injected; the default resolver is disabled until a
DNSX-compatible adapter is supplied. HTTP does NOT enforce rules of engagement:
callers must authorize targets before enqueueing, as in the upstream code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import html
import logging
from queue import Empty, Full, Queue
import re
import ssl
from threading import Event, Lock, Thread
import time
from typing import Callable, Protocol
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

COOLDOWN = 300.0
HTTP_TIMEOUT = 12.0
QUEUE_SIZE = 1024
BODY_LIMIT = 1 << 20
_LOG = logging.getLogger(__name__)
_TITLE = re.compile(r'<title[^>]*>(.*?)</title>', re.I | re.S)


@dataclass
class DNSRecords:
    a: list[str] = field(default_factory=list)
    aaaa: list[str] = field(default_factory=list)
    cname: list[str] = field(default_factory=list)


@dataclass
class UpsertIPReq:
    ip: str
    bound_domains: list[str]


@dataclass
class UpsertSubdomainReq:
    domain: str
    record_type: str
    record_value: list[str]


@dataclass
class UpsertHTTPServiceReq:
    url: str
    status_code: int
    content_length: int
    page_title: str


class AssetStore(Protocol):
    def upsert_ip(self, request: UpsertIPReq): ...
    def upsert_subdomain(self, request: UpsertSubdomainReq): ...
    def upsert_http_service(self, request: UpsertHTTPServiceReq): ...


class Resolver(Protocol):
    def query_multiple(self, host: str) -> DNSRecords | None: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HTTPClient:
    """Proxy evaluated per request; no redirects, no environment proxy fallback.

    TLS verification is disabled to match upstream's interception-proxy behavior.
    Use only for authorized probes, not general-purpose authenticated requests.
    """
    def __init__(self, proxy: Callable[[], str] | None = None):
        self.proxy = proxy

    def get(self, raw_url: str):
        proxy = self.proxy() if self.proxy else ''
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        opener = build_opener(
            ProxyHandler({'http': proxy, 'https': proxy} if proxy else {}),
            HTTPSHandler(context=context), _NoRedirect(),
        )
        request = Request(raw_url, headers={'User-Agent': 'artex-enrich/1.0', 'Connection': 'close'}, method='GET')
        try:
            return opener.open(request, timeout=HTTP_TIMEOUT)
        except HTTPError as response:
            # Unlike urllib, Go treats 3xx/4xx/5xx as ordinary HTTP responses.
            return response


def extract_title(body: bytes) -> str:
    match = _TITLE.search(body.decode('utf-8', errors='replace'))
    return html.unescape(match[1]).strip() if match else ''


def host_of(raw_url: str) -> str:
    try:
        return urlsplit(raw_url).hostname or ''
    except ValueError:
        return ''


def is_blank(value) -> bool:
    return value is None or (type(value) is str and value == '') or (type(value) in (int, float) and value == 0)


def uniq(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


@dataclass(frozen=True)
class _Job:
    kind: str
    id: int
    arg: str


class Engine:
    def __init__(self, store: AssetStore, proxy: Callable[[], str] | None = None,
                 workers: int = 4, *, resolver: Resolver | None = None,
                 client=None, clock: Callable[[], float] = time.monotonic):
        self.store, self.resolver = store, resolver
        self.client = client if client is not None else HTTPClient(proxy)
        self.clock = clock
        self.jobs: Queue[_Job] = Queue(QUEUE_SIZE)
        self._closed = Event()
        self._cool: dict[tuple[str, int], float] = {}
        self._lock = Lock()
        self._workers = [Thread(target=self._worker, daemon=True) for _ in range(workers if workers > 0 else 4)]
        for thread in self._workers:
            thread.start()

    def resolve_domain(self, id: int, host: str) -> None:
        self._enqueue(_Job('dns', id, host))

    def probe_site(self, id: int, raw_url: str) -> None:
        self._enqueue(_Job('http', id, raw_url))

    def _enqueue(self, job: _Job) -> None:
        if job.id <= 0 or not job.arg or self._closed.is_set():
            return
        try:
            self.jobs.put_nowait(job)
        except Full:
            _LOG.warning('Enrichment queue full; dropping kind=%s id=%s', job.kind, job.id)

    def close(self) -> None:
        """Idempotent, non-blocking stop; an in-flight request may finish."""
        self._closed.set()

    def _on_cooldown(self, job: _Job) -> bool:
        key = (job.kind, job.id)
        with self._lock:
            now = self.clock()
            last = self._cool.get(key)
            if last is not None and now - last < COOLDOWN:
                return True
            self._cool[key] = now
            return False

    def _worker(self) -> None:
        while not self._closed.is_set():
            try:
                job = self.jobs.get(timeout=0.1)
            except Empty:
                continue
            try:
                if not self._on_cooldown(job):
                    if job.kind == 'dns':
                        self._do_dns(job.id, job.arg)
                    else:
                        self._do_http(job.id, job.arg)
            except Exception:
                _LOG.exception('Enrichment job failed kind=%s id=%s', job.kind, job.id)
            finally:
                self.jobs.task_done()

    def _upsert(self, method: str, request) -> None:
        try:
            getattr(self.store, method)(request)
        except Exception:
            pass  # Upstream intentionally ignores individual store failures.

    def _do_dns(self, id: int, host: str) -> None:
        if self.resolver is None:
            return
        try:
            data = self.resolver.query_multiple(host)
        except Exception:
            return
        if data is None:
            return
        for ip in uniq(data.a + data.aaaa):
            self._upsert('upsert_ip', UpsertIPReq(ip, [host]))
        for record_type, values in [('A', data.a), ('AAAA', data.aaaa), ('CNAME', data.cname)]:
            for value in values:
                self._upsert('upsert_subdomain', UpsertSubdomainReq(host, record_type, [value]))

    def _do_http(self, id: int, raw_url: str) -> None:
        if not host_of(raw_url):
            return
        try:
            response = self.client.get(raw_url)
            try:
                body = response.read(BODY_LIMIT)
                status = response.status
            finally:
                response.close()
        except Exception:
            return
        self._upsert('upsert_http_service', UpsertHTTPServiceReq(raw_url, status, len(body), extract_title(body)))


# Public Go-name aliases ease incremental integration.
Engine.ResolveDomain = Engine.resolve_domain
Engine.ProbeSite = Engine.probe_site
Engine.Close = Engine.close
New = Engine
