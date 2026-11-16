"""Opt-in bounded response receipt retained through actual header/body delivery.

No policy is supplied implicitly. A receipt validates an already serialized body;
it does not execute a new query/action or turn headers into an atomic snapshot.
Transport wrappers that defer headers must call validate_disclosure after their
actual start_response and before yielding the captured chunk.
"""
from dataclasses import dataclass
from collections.abc import Callable
from .core import Response

@dataclass(frozen=True)
class DisclosureResponse(Response):
    validate_disclosure: Callable[[], None] | None = None

    def __post_init__(self):
        if type(self.body) is not bytes or not callable(self.validate_disclosure):
            raise ValueError('Exact serialized body and disclosure receipt required')

class DisclosureBody:
    """Single-use bounded representation, not a streaming response."""
    def __init__(self,response):
        if type(response) is not DisclosureResponse:raise ValueError('Exact disclosure response required')
        self._body=response.body;self._receipt=response.validate_disclosure
        self._pins=(self._body,self._receipt);self._sent=False;self._closed=False
    def validate_disclosure(self):
        if self._closed or self._body is not self._pins[0] or self._receipt is not self._pins[1]:
            raise RuntimeError('Disclosure binding unavailable')
        try:self._pins[1]()
        except Exception:raise RuntimeError('Disclosure unavailable') from None
        if self._body is not self._pins[0] or self._receipt is not self._pins[1]:
            raise RuntimeError('Disclosure binding unavailable')
    def __iter__(self):return self
    def __next__(self):
        if self._sent or self._closed:raise StopIteration
        self.validate_disclosure();self._sent=True;return self._pins[0]
    def close(self):self._closed=True
