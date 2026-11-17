"""Normalize an already URI-decoded WSGI skill read path exactly once.

WSGI PATH_INFO carries URI bytes as Latin-1. Standalone read services retain their
encoded-URL contract, so literal percent is refused here before their unquote.
No filesystem access, normalization, URI decoding, query decoding or authority.
"""
from urllib.parse import urlsplit
from .core import Request


def decode_skill_request(request):
    if type(request) is not Request or type(request.target) is not str or len(request.target)>4096:
        raise ValueError('Invalid skill transport request')
    parts=urlsplit(request.target)
    if parts.scheme or parts.netloc or parts.fragment:raise ValueError('Invalid skill transport target')
    path=parts.path.encode('latin-1').decode('utf-8')
    # % was already decoded by the gateway. Allowing it to reach the standalone
    # URL parser would create aliases through a second URI decoding pass.
    if '%' in path:raise ValueError('Encoded skill aliases are refused')
    target=path+('?' + parts.query if parts.query else '')
    return Request(request.method,target,request.headers,request.body)
