"""Bounded, cancellation-aware HTTP transport for explicitly scoped adapters."""
import hashlib
import http.client
import ssl
import threading
import time
from urllib.parse import urlsplit

class ScopedHTTP:
    def __init__(self,origin,*,authorized_origins,methods=('GET',),allow_path=lambda method,path:True,
                 interval=1.0,max_bytes=262144,connection_factory=http.client.HTTPSConnection):
        parsed=urlsplit(origin)
        if origin not in authorized_origins or parsed.scheme!='https' or parsed.netloc!=parsed.hostname or parsed.path or parsed.query or parsed.fragment:
            raise ValueError('Explicit canonical HTTPS origin required')
        if not methods or set(methods)-{'GET','HEAD','POST','OPTIONS'} or interval<1 or not 1<=max_bytes<=1048576:raise ValueError('Bounded HTTP policy required')
        self.origin,self.host=origin,parsed.hostname
        self.methods,self.allow_path=tuple(methods),allow_path
        self.interval,self.max_bytes,self.connection=interval,max_bytes,connection_factory
        self._tls=ssl.create_default_context();self._last=0.0;self._lock=threading.RLock()

    def request(self,context,method,path,headers,*,body=None):
        p=urlsplit(path)
        if method not in self.methods or p.scheme or p.netloc or p.fragment or not path.startswith('/') or path.startswith('//') or '\\' in path or any(ord(c)<32 for c in path) or self.allow_path(method,path) is not True:
            raise PermissionError('HTTP operation outside declared scope')
        if body is not None and (not isinstance(body,bytes) or len(body)>65536):raise ValueError('Bounded request body required')
        with self._lock:
            context.check()
            remaining=self._last+self.interval-time.monotonic()
            while remaining>0:
                time.sleep(min(remaining,.05));context.check();remaining=self._last+self.interval-time.monotonic()
            self._last=time.monotonic()
            conn=self.connection(self.host,443,timeout=min(20,context.remaining_seconds()),context=self._tls)
            try:
                context.check()
                if body is None:conn.request(method,path,headers=headers)
                else:conn.request(method,path,body=body,headers=headers)
                response=conn.getresponse()
                names=('Content-Type','Strict-Transport-Security','X-Content-Type-Options','X-Frame-Options')
                response_headers=response.getheaders() if hasattr(response,'getheaders') else [(k,response.getheader(k)) for k in names if response.getheader(k)]
                data=response.read(self.max_bytes+1);context.check()
                return dict(status=response.status,headers=response_headers,body=data,body_sha256=hashlib.sha256(data).hexdigest())
            finally:conn.close()
