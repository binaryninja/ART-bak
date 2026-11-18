"""Explicit protected reconciliation of the host's existing profile publication.

Only POST /api/llm/profiles/reconcile is declared. The existing Router policy
still applies, followed by an explicit authorize(original Request) callback
which must return literal True. This callback is the host's principal decision,
not a default permission, JWT substitute or decision derived from request JSON.
ProfileHost then enforces its separate authority, freshness and writer guard.

The body is absent or a <=256-byte UTF-8 JSON empty object. No query or fragment
is accepted. No profile mutation is retried/replayed and GET has no side effect.
Only a receipt captured by the host under its guard and released after successful
exit is projected. Reconciliation never returns a persisted-mutation assertion.
Publication/ownership across other writers or processes remains host-owned.
"""
from collections.abc import Mapping
import json
from types import MethodType
from urllib.parse import urlsplit

from .core import Response, error_response, json_response
from .profiles import runtime_dto
from ..providers.committed_profiles import ProfilePublicationStatus, ProfileMutationBusy
from ..providers.profile_host import (
    ProfileHost, ProfileHostReceipt, ProfileHostDenied,
    ProfileHostStale, ProfileHostUnavailable,
)


RECONCILE_PATH = '/api/llm/profiles/reconcile'
MAX_BODY = 256
_HOST_RECONCILE = ProfileHost.reconcile


def _empty_object(pairs):
    if pairs:
        raise ValueError('Reconciliation takes no fields')
    return {}


def _invalid_constant(value):
    raise ValueError('Invalid JSON constant')


def _validate_body(body):
    if not isinstance(body, bytes):
        raise ValueError('Request body must be bytes')
    if body:
        data = json.loads(body.decode('utf-8'), object_pairs_hook=_empty_object,
                          parse_constant=_invalid_constant)
        if type(data) is not dict or data:
            raise ValueError('Expected empty object')


def _project(receipt):
    if type(receipt) is not ProfileHostReceipt:
        raise ValueError('Invalid reconciliation receipt')
    status = receipt.status
    if (type(status) is not ProfilePublicationStatus or status.operation != 'reconcile'
            or status.phase != 'applied' or status.profile_id is not None
            or status.committed is not False or status.runtime_applied is not True):
        raise ValueError('Reconciliation was not applied')
    if not isinstance(receipt.snapshot, Mapping):
        raise ValueError('Invalid reconciliation snapshot')
    # Reuse the existing strict public whitelist and URL credential redaction.
    # An extra/private field in a receipt never becomes response JSON.
    snapshot = runtime_dto(dict(receipt.snapshot))
    return {'reconciled': True, 'runtime_applied': True, 'runtime': snapshot}


class ProfileReconciliationService:
    def __init__(self, host, *, authorize):
        if host is not None and type(host) is not ProfileHost:
            raise ValueError('Original profile host required')
        if not callable(authorize):
            raise ValueError('Explicit reconciliation request authorization required')
        reconcile = host.reconcile if host is not None else None
        if host is not None and (type(reconcile) is not MethodType or
                reconcile.__self__ is not host or reconcile.__func__ is not _HOST_RECONCILE):
            raise ValueError('Original profile reconciliation method required')
        self.host, self.authorize = host, authorize
        self._host_pin, self._authorize_pin, self._reconcile = host, authorize, reconcile

    def _pins_match(self):
        if self.host is not self._host_pin or self.authorize is not self._authorize_pin:
            return False
        if self.host is None:
            return True
        method = self.host.reconcile
        return all(type(candidate) is MethodType and candidate.__self__ is self._host_pin
                   and candidate.__func__ is _HOST_RECONCILE
                   for candidate in (method, self._reconcile))

    def register(self, router):
        router.add('POST', RECONCILE_PATH,
                   lambda request, params: self._handle(request), public=False)

    def _handle(self, request):
        # No body parsing, status/snapshot access or profile work before the
        # original request-principal decision. Replaced callbacks are not run.
        try:
            if not self._pins_match():
                return error_response(503, 'Profile reconciliation binding unavailable')
            try:
                allowed = self._authorize_pin(request) is True
            except Exception:
                allowed = False
            if not allowed:
                return error_response(403, 'Profile reconciliation denied')
            if not self._pins_match():
                return error_response(503, 'Profile reconciliation binding unavailable')
            if request.method.upper() != 'POST':
                response = error_response(405, 'Method not allowed')
                return Response(response.status, response.body,
                                {**response.headers, 'Allow': 'POST'})
            target = urlsplit(request.target)
            if target.path != RECONCILE_PATH:
                return error_response(404, 'Not found')
            if '?' in request.target or '#' in request.target:
                return error_response(400, 'Reconciliation takes no query parameters')
            if not isinstance(request.body, bytes):
                return error_response(400, 'Invalid reconciliation request')
            if len(request.body) > MAX_BODY:
                return error_response(413, 'Request body too large')
            try:
                _validate_body(request.body)
            except (ValueError, UnicodeError):
                return error_response(400, 'Invalid reconciliation request')
            if self._host_pin is None:
                return error_response(503, 'Profile reconciliation host unavailable')
            receipt = self._reconcile()
            if not self._pins_match():
                return error_response(503, 'Profile reconciliation binding unavailable')
            try:
                value = _project(receipt)
            except Exception:
                return error_response(503, 'Profile reconciliation outcome unavailable')
            return json_response(200, value)
        except ProfileHostDenied:
            return error_response(403, 'Profile reconciliation denied')
        except ProfileHostStale:
            return error_response(409, 'Profile reconciliation authority is stale')
        except ProfileMutationBusy:
            return error_response(409, 'Profile operation already in progress')
        except ProfileHostUnavailable:
            return error_response(503, 'Profile reconciliation host unavailable')
        except Exception:
            # No raw exception/status is copied; failure does not prove rollback
            # or that any earlier profile mutation was/was not persisted.
            return error_response(500, 'Profile reconciliation failed')
