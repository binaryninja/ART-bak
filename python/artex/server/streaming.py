"""Pull-based bounded byte streaming and SSE framing; no threads or endpoints.

The gateway must call close() on disconnect (WSGI's iterable contract). Exhaustion,
producer errors, invalid chunks and explicit close release both iterator/source
and the optional callback exactly once. Producer/cleanup exception text is never
emitted; a failed stream ends abruptly, so consumers must not interpret EOF as a
successful application-level completion. Chunk limits do not bound total stream
length or producer execution time: gateways still need timeouts/backpressure.
"""
from dataclasses import dataclass,field
from collections.abc import Iterable,Mapping,Callable

DEFAULT_CHUNK_BYTES=65536
MAX_CHUNK_BYTES=1048576


def _limit(value):
    if type(value) is not int or not 0<value<=MAX_CHUNK_BYTES:
        raise ValueError('Chunk limit must be 1..1048576 bytes')
    return value


@dataclass(frozen=True)
class StreamingResponse:
    status:int
    body:Iterable[bytes]
    headers:Mapping[str,str]=field(default_factory=lambda:{'Content-Type':'text/event-stream; charset=utf-8','Cache-Control':'no-cache'})
    on_close:Callable[[],None]|None=None
    max_chunk_bytes:int=DEFAULT_CHUNK_BYTES

    def __post_init__(self):
        _limit(self.max_chunk_bytes)
        if self.on_close is not None and not callable(self.on_close):raise TypeError('on_close must be callable')
        if isinstance(self.body,(str,bytes,bytearray)) or not hasattr(self.body,'__iter__'):
            raise TypeError('Streaming body must be an iterable of bytes')


class ClosingIterator:
    """Single-use gateway iterable. No producer invocation until first next()."""
    def __init__(self,response:StreamingResponse):
        self.source=response.body
        self.iterator=None
        self.callback=response.on_close
        self.maximum=_limit(response.max_chunk_bytes)
        self.closed=False
        self.failed=False

    def __iter__(self):return self

    def __next__(self):
        if self.closed:raise StopIteration
        try:
            if self.iterator is None:self.iterator=iter(self.source)
            chunk=next(self.iterator)
            if not isinstance(chunk,bytes) or len(chunk)>self.maximum:
                raise ValueError('Invalid streaming chunk')
            return chunk
        except StopIteration:
            self.close()
            raise
        except Exception:
            self.failed=True
            self.close()
            raise StopIteration from None
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.closed:return
        self.closed=True
        # Mark closed before cleanup so recursive/repeated close cannot duplicate
        # release callbacks. Distinct iterable and iterator may each own resources.
        seen=set()
        for target in (self.iterator,self.source):
            if target is None or id(target) in seen:continue
            seen.add(id(target))
            try:
                close=getattr(target,'close',None)
                if callable(close):close()
            except Exception:pass
        callback,self.callback=self.callback,None
        if callback is not None:
            try:callback()
            except Exception:pass


def sse_frame(data:str,*,event:str|None=None,id:str|None=None,retry:int|None=None,
              max_bytes:int=DEFAULT_CHUNK_BYTES)->bytes:
    """Encode one SSE event; multiline data is prefixed, never raw field injection.

    Empty id is allowed (resets client cursor); None omits it. Empty event is
    allowed by SSE. No JSON/string coercion, so callers control their data format.
    """
    _limit(max_bytes)
    if not isinstance(data,str) or '\x00' in data:raise ValueError('Invalid SSE data')
    lines=[]
    for key,value in (('event',event),('id',id)):
        if value is None:continue
        if not isinstance(value,str) or any(ch in value for ch in '\r\n\x00'):
            raise ValueError('Invalid SSE field')
        lines.append(key+': '+value+'\n')
    if retry is not None:
        if type(retry) is not int or not 0<=retry<2**31:raise ValueError('Invalid SSE retry')
        lines.append('retry: '+str(retry)+'\n')
    for line in data.replace('\r\n','\n').replace('\r','\n').split('\n'):
        lines.append('data: '+line+'\n')
    result=(''.join(lines)+'\n').encode('utf-8')
    if len(result)>max_bytes:raise ValueError('SSE frame exceeds chunk limit')
    return result
