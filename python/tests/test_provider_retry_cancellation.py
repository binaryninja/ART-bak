"""Pure retry-context and response-ownership regressions; no network or credentials."""
import asyncio
from collections import UserDict
from concurrent.futures import CancelledError as FutureCancelled
from itertools import product
from types import MappingProxyType
import unittest

from artex.providers import Response
from artex.providers.http import TransportCancelled, TransportError
from artex.providers.retry import RetryProvider, RetryTransport
from test_provider_retry import Clock, FakeProvider, OK


class RetryCancellationTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()

    def provider(self, provider):
        return RetryProvider(provider, format='openai', clock=self.clock,
                             sleep=self.clock.sleep)

    def transport(self, transport):
        return RetryTransport(transport, clock=self.clock, sleep=self.clock.sleep)

    def test_cancelled_general_mapping_never_enters_provider_or_transport(self):
        for make in (UserDict, MappingProxyType):
            for mode in ('complete', 'stream', 'transport'):
                with self.subTest(mapping=make.__name__, mode=mode):
                    original = make({'cancelled': True})
                    provider = FakeProvider([OK] if mode == 'complete' else [[{'type': 'message_stop'}]])
                    calls = []
                    def transport(*args):
                        calls.append(args)
                        return Response(200, b'')
                    with self.assertRaises(TransportCancelled):
                        if mode == 'transport':
                            self.transport(transport)(original, 'unused', {}, b'', False)
                        elif mode == 'complete':
                            self.provider(provider).complete(original, {})
                        else:
                            list(self.provider(provider).stream(original, {}))
                    self.assertEqual(provider.calls, 0)
                    self.assertEqual(calls, [])

    def test_general_mapping_retains_metadata_through_both_retry_layers(self):
        marker = object()
        seen = []
        def transport(context, *args):
            seen.append(context['fixture_marker'])
            return Response(200, b'')
        wrapped = self.transport(transport)
        class Provider:
            def complete(self, context, request):
                seen.append(context['fixture_marker'])
                response = wrapped(context, 'unused', {}, b'', False)
                response.close()
                return OK
            def stream(self, context, request):
                return iter(())
        for make in (UserDict, MappingProxyType):
            with self.subTest(mapping=make.__name__):
                self.assertEqual(self.provider(Provider()).complete(make({'fixture_marker': marker}), {}), OK)
        self.assertEqual(seen, [marker] * 4)

    def test_mutable_general_mapping_cancellation_stops_backoff(self):
        for mode in ('complete', 'stream'):
            with self.subTest(mode=mode):
                context = UserDict()
                clock = Clock()
                def sleep(delay):
                    clock.sleep(delay)
                    context['cancelled'] = True
                outcomes = [TransportError('synthetic disconnect'), OK]
                if mode == 'stream':
                    outcomes = [[TransportError('synthetic disconnect')], [{'type': 'text_delta', 'text': 'unexpected'}]]
                provider = FakeProvider(outcomes)
                retry = RetryProvider(provider, format='openai', clock=clock, sleep=sleep)
                with self.assertRaises(TransportCancelled):
                    if mode == 'stream':
                        list(retry.stream(context, {}))
                    else:
                        retry.complete(context, {})
                self.assertEqual(provider.calls, 1)
                self.assertEqual(clock.waits, [0.05])

    def test_readonly_mapping_observes_cancellation_after_response_and_closes_once(self):
        original = {}
        context = MappingProxyType(original)
        closed, calls = [], []
        def transport(*args):
            calls.append(args)
            original['cancelled'] = True
            return Response(200, b'', lambda: closed.append('closed'))
        with self.assertRaises(TransportCancelled):
            self.transport(transport)(context, 'unused', {}, b'', False)
        self.assertEqual(len(calls), 1)
        self.assertEqual(closed, ['closed'])
        self.assertEqual(self.clock.waits, [])

    def test_general_mapping_preserves_nested_check_without_constructing_a_runtime(self):
        class Cancelled:
            def check(self):
                raise TransportCancelled('synthetic nested cancellation')
        for make in (UserDict, MappingProxyType):
            with self.subTest(mapping=make.__name__):
                provider = FakeProvider([OK])
                with self.assertRaises(TransportCancelled):
                    self.provider(provider).complete(make({'runtime_context': Cancelled()}), {})
                self.assertEqual(provider.calls, 0)

    def test_baseexception_cancellation_after_response_closes_before_propagation(self):
        for method in ('check', 'err'):
            for status in (200, 503):
                with self.subTest(method=method, status=status):
                    cancellation = asyncio.CancelledError('synthetic cancellation')
                    closed, calls = [], []
                    class Context:
                        cancelled = False
                        def check(self):
                            if self.cancelled and method == 'check':
                                raise cancellation
                        def err(self):
                            return cancellation if self.cancelled and method == 'err' else None
                    context = Context()
                    def transport(*args):
                        calls.append(args)
                        context.cancelled = True
                        return Response(status, b'', lambda: closed.append('closed'))
                    with self.assertRaises(asyncio.CancelledError) as raised:
                        self.transport(transport)(context, 'unused', {}, b'', False)
                    self.assertIs(raised.exception, cancellation)
                    self.assertEqual(closed, ['closed'])
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(self.clock.waits, [])

    def test_cleanup_error_cannot_replace_one_shot_cancellation_and_trigger_replay(self):
        cases = product((asyncio.CancelledError, FutureCancelled, TransportCancelled),
                        ('check', 'err'), (200, 503), ('transport', 'complete', 'stream'),
                        (TransportError, ValueError))
        for kind, method, status, mode, cleanup_kind in cases:
            with self.subTest(cancellation=f'{kind.__module__}.{kind.__name__}',
                              method=method, status=status, mode=mode,
                              cleanup=cleanup_kind.__name__):
                self.clock = Clock()
                cancellation = kind('synthetic one-shot cancellation')
                closed, calls = [], []
                class Context:
                    error = None
                    def check(self):
                        if method == 'check' and self.error is not None:
                            error, self.error = self.error, None
                            raise error
                    def err(self):
                        if method == 'err':
                            error, self.error = self.error, None
                            return error
                        return None
                context = Context()
                def close():
                    closed.append('closed')
                    raise cleanup_kind('synthetic cleanup failure')
                def transport(*args):
                    calls.append(args)
                    if len(calls) == 1:
                        context.error = cancellation
                        return Response(status, b'', close)
                    return Response(200, b'')
                wrapped = self.transport(transport)
                class Provider:
                    def complete(self, context, request):
                        response = wrapped(context, 'unused', {}, b'', False)
                        response.close()
                        return OK
                    def stream(self, context, request):
                        response = wrapped(context, 'unused', {}, b'', True)
                        response.close()
                        yield {'type': 'text_delta', 'text': 'unexpected replay'}
                with self.assertRaises(kind) as raised:
                    if mode == 'transport':
                        wrapped(context, 'unused', {}, b'', False)
                    elif mode == 'complete':
                        self.provider(Provider()).complete(context, {})
                    else:
                        list(self.provider(Provider()).stream(context, {}))
                self.assertIs(raised.exception, cancellation)
                self.assertEqual(closed, ['closed'])
                self.assertEqual(len(calls), 1)
                self.assertEqual(self.clock.waits, [])

    def test_cancel_after_delivered_event_closes_iterator_without_replay(self):
        context = UserDict()
        provider = FakeProvider([[{'type': 'text_delta', 'text': 'delivered'},
                                  TransportError('later disconnect')]])
        stream = self.provider(provider).stream(context, {})
        self.assertEqual(next(stream), {'type': 'text_delta', 'text': 'delivered'})
        context['cancelled'] = True
        with self.assertRaises(TransportCancelled):
            next(stream)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(provider.closed, 1)
        self.assertEqual(self.clock.waits, [])


if __name__ == '__main__':
    unittest.main()
