"""Cumulative usage regressions with inert providers and in-memory sinks only."""
import asyncio
from contextlib import contextmanager
from dataclasses import asdict
import json
from types import SimpleNamespace
import unittest

from artex.agent.streaming import StreamingCompletion
from artex.db.llm_records import LLMRecordStore
from artex.llmrec import Recorder, Usage
from artex.llmrec.postgres import (
    CaptureLimit, PostgresRecorder, RecordingCancelled, RecordingPersistenceError,
    RecordingScope,
)
from artex.providers import _anthropic_events, _openai_events


FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


def start(**usage):
    return {"type": "message_start", "usage": usage}


def delta(**usage):
    return {"type": "message_delta", "usage": usage}


def finish(**usage):
    return {"type": "message_delta", "usage": usage, "stop_reason": "end_turn"}


STOP = {"type": "message_stop"}


class Source:
    def __init__(self, events, close_error=None):
        self.events = iter(events)
        self.pulls = self.closes = 0
        self.close_error = close_error

    def __iter__(self):
        return self

    def __next__(self):
        self.pulls += 1
        item = next(self.events)
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self):
        self.closes += 1
        if self.close_error is not None:
            raise self.close_error


class Provider:
    def __init__(self, events=(), close_error=None):
        self.events, self.close_error = events, close_error
        self.sources = []
        self.result = ({"content": [{"type": "text", "text": "synthetic"}]},
                       "end_turn", Usage(13, 8, 2, 1))

    def stream(self, context, request):
        source = Source(request.get("events", self.events), self.close_error)
        self.sources.append(source)
        return source

    def complete(self, context, request):
        return self.result


class Sink:
    def __init__(self):
        self.usage, self.records = [], []

    def insert_llm_usage(self, row):
        self.usage.append(row)

    def insert_llm_record(self, row):
        self.records.append(row)


class MemoryExecutor:
    """Collect Query objects without connecting to or executing a database."""
    def __init__(self):
        self.rows, self.transactions = [], []
        self.fail_record = False

    @contextmanager
    def transaction(self):
        self.transactions.append("begin")
        first = len(self.rows)
        try:
            yield self
        except BaseException:
            del self.rows[first:]
            self.transactions.append("rollback")
            raise
        else:
            self.transactions.append("commit")

    def execute(self, query):
        if self.fail_record and "INSERT INTO llm_records" in query.sql:
            raise RuntimeError("synthetic write failure")
        self.rows.append(query)
        return 1


class Harness:
    def __init__(self, kind, events=(), **options):
        self.kind = kind
        self.provider = options.pop("provider", Provider(events, options.pop("close_error", None)))
        if kind == "compatibility":
            self.sink = Sink()
            self.recorder = Recorder(self.provider, self.sink, model="synthetic", clock=lambda: 0, **options)
        else:
            self.executor = MemoryExecutor()
            self.recorder = PostgresRecorder(self.provider, LLMRecordStore(self.executor, task_id="task"),
                scope=RecordingScope("task", "session", "worker", "synthetic", "profile"),
                enabled=True, redact=lambda kind, text, scope: text,
                binding_validator=lambda scope, inner: True, clock=lambda: 0, **options)

    def totals(self):
        if self.kind == "compatibility":
            return [tuple(row[name] for name in FIELDS) for row in self.sink.usage]
        return [row.params[6:10] for row in self.executor.rows if "INSERT INTO llm_usage" in row.sql]

    def statuses(self):
        if self.kind == "compatibility":
            return [row["status"] for row in self.sink.usage]
        return [row.params[10] for row in self.executor.rows if "INSERT INTO llm_usage" in row.sql]

    def consume(self):
        return list(self.recorder.stream({}, {}))


class FenceContext(dict):
    def check(self):
        pass


class StreamUsageTests(unittest.TestCase):
    kinds = ("compatibility", "durable")

    def test_repeated_cumulative_output_is_eight_not_eleven(self):
        for kind in self.kinds:
            with self.subTest(kind=kind):
                h = Harness(kind, [start(input_tokens=13, cache_read_tokens=2, cache_write_tokens=1),
                    delta(output_tokens=3), finish(output_tokens=8), STOP])
                h.consume()
                self.assertEqual(h.totals(), [(13, 8, 2, 1)])
                self.assertEqual(h.statuses(), ["ok"])
                self.assertEqual(h.provider.sources[0].closes, 1)

    def test_all_counters_use_independent_high_water_marks(self):
        events = [start(input_tokens=10, output_tokens=1, cache_read_tokens=2, cache_write_tokens=3),
                  delta(input_tokens=12, output_tokens=5, cache_read_tokens=4),
                  delta(input_tokens=11, output_tokens=3, cache_write_tokens=6),
                  finish(input_tokens=0, output_tokens=5, cache_read_tokens=0, cache_write_tokens=0), STOP]
        for kind in self.kinds:
            with self.subTest(kind=kind):
                h = Harness(kind, events)
                h.consume()
                self.assertEqual(h.totals(), [(12, 5, 4, 6)])

    def test_sparse_absent_zero_and_duplicate_snapshots_preserve_counts(self):
        events = [start(input_tokens=10), delta(output_tokens=4),
                  delta(output_tokens=4), {"type": "message_delta"}, delta(),
                  finish(input_tokens=0, output_tokens=0), STOP]
        for kind in self.kinds:
            with self.subTest(kind=kind):
                h = Harness(kind, events)
                h.consume()
                self.assertEqual(h.totals(), [(10, 4, 0, 0)])

    def test_mapping_object_and_dataclass_usage_keep_event_identity(self):
        events = [SimpleNamespace(type="message_start", usage=Usage(9, 1, 2, 3)),
                  SimpleNamespace(type="message_delta", usage=SimpleNamespace(output_tokens=4)),
                  SimpleNamespace(type="message_delta", usage={"output_tokens": 8}), STOP]
        before = asdict(events[0].usage)
        for kind in self.kinds:
            with self.subTest(kind=kind):
                h = Harness(kind, events)
                returned = h.consume()
                self.assertTrue(all(a is b for a, b in zip(returned, events)))
                self.assertEqual(h.totals(), [(9, 8, 2, 3)])
        self.assertEqual(asdict(events[0].usage), before)

    def test_other_event_usage_is_not_metered_and_no_snapshots_remain_zero(self):
        for kind in self.kinds:
            with self.subTest(kind=kind):
                h = Harness(kind, [start(), {"type": "text_delta", "text": "synthetic",
                    "usage": {"output_tokens": 100}}, finish(), STOP])
                h.consume()
                self.assertEqual(h.totals(), [(0, 0, 0, 0)])

    def test_early_close_meters_only_seen_snapshots_once(self):
        for kind in self.kinds:
            with self.subTest(kind=kind):
                h = Harness(kind, [start(input_tokens=5), delta(output_tokens=3),
                    delta(output_tokens=8), finish(output_tokens=99), STOP])
                stream = h.recorder.stream({}, {})
                for _ in range(3):
                    next(stream)
                stream.close()
                stream.close()
                self.assertEqual(h.totals(), [(5, 8, 0, 0)])
                self.assertEqual(h.statuses(), ["ok" if kind == "compatibility" else "cancelled"])
                self.assertEqual((h.provider.sources[0].pulls, h.provider.sources[0].closes), (3, 1))

    def test_provider_failure_and_baseexception_keep_seen_usage(self):
        for kind in self.kinds:
            for error in (RuntimeError("synthetic failure"), asyncio.CancelledError("synthetic cancellation")):
                with self.subTest(kind=kind, error=type(error).__name__):
                    h = Harness(kind, [start(input_tokens=6), delta(output_tokens=3), delta(output_tokens=8), error])
                    with self.assertRaises(type(error)) as caught:
                        h.consume()
                    self.assertIs(caught.exception, error)
                    self.assertEqual(h.totals(), [(6, 8, 0, 0)])
                    self.assertEqual(h.statuses(), ["error"])
                    self.assertEqual(h.provider.sources[0].closes, 1)

    def test_close_failure_still_meters_without_changing_status_policy(self):
        for kind in self.kinds:
            with self.subTest(kind=kind):
                failure = RuntimeError("synthetic close failure")
                h = Harness(kind, [start(input_tokens=6), delta(output_tokens=3), delta(output_tokens=8)],
                            close_error=failure)
                with self.assertRaises(RuntimeError) as caught:
                    h.consume()
                self.assertIs(caught.exception, failure)
                self.assertEqual(h.totals(), [(6, 8, 0, 0)])
                self.assertEqual(h.statuses(), ["ok" if kind == "compatibility" else "error"])

    def test_never_started_stream_makes_no_provider_or_sink_calls(self):
        for kind in self.kinds:
            with self.subTest(kind=kind):
                h = Harness(kind, [start(input_tokens=9)])
                stream = h.recorder.stream({}, {})
                stream.close()
                self.assertEqual(h.provider.sources, [])
                self.assertEqual(h.totals(), [])

    def test_separate_interleaved_streams_do_not_share_totals(self):
        for kind in self.kinds:
            with self.subTest(kind=kind):
                h = Harness(kind)
                a = h.recorder.stream({}, {"events": [start(input_tokens=10), delta(output_tokens=3), finish(output_tokens=8), STOP]})
                b = h.recorder.stream({}, {"events": [start(input_tokens=2), delta(output_tokens=1), finish(output_tokens=4), STOP]})
                next(a)
                next(b)
                next(a)
                list(b)
                list(a)
                self.assertEqual(h.totals(), [(2, 4, 0, 0), (10, 8, 0, 0)])

    def test_complete_and_usage_add_stay_additive(self):
        usage = Usage(5, 6, 7, 8)
        usage.add(Usage(3, 2, 1, 4))
        self.assertEqual(asdict(usage), dict(zip(FIELDS, (8, 8, 8, 12))))
        for kind in self.kinds:
            with self.subTest(kind=kind):
                h = Harness(kind)
                self.assertIs(h.recorder.complete({}, {}), h.provider.result)
                self.assertIs(h.recorder.complete({}, {}), h.provider.result)
                self.assertEqual(h.totals(), [(13, 8, 2, 1), (13, 8, 2, 1)])

    def test_disabled_compatibility_bodies_still_meter_cumulative_usage(self):
        h = Harness("compatibility", [start(input_tokens=10), delta(output_tokens=3), finish(output_tokens=8)],
                    enabled=lambda: False)
        h.consume()
        self.assertEqual(h.totals(), [(10, 8, 0, 0)])
        self.assertEqual(h.sink.records, [])

    def test_compatibility_context_error_preserves_close_cancellation_status(self):
        h = Harness("compatibility", [start(input_tokens=5), delta(output_tokens=3), delta(output_tokens=8)])
        stream = h.recorder.stream({"error": RuntimeError("synthetic cancellation")}, {})
        for _ in range(3):
            next(stream)
        stream.close()
        self.assertEqual(h.totals(), [(5, 8, 0, 0)])
        self.assertEqual(h.statuses(), ["error"])
        self.assertEqual(h.sink.records[0]["error"], "synthetic cancellation")

    def test_compatibility_normalized_record_and_meter_agree(self):
        h = Harness("compatibility", [start(input_tokens=10), {"type": "text_delta", "text": "synthetic"},
            delta(output_tokens=3), finish(output_tokens=8), STOP])
        h.consume()
        self.assertEqual(json.loads(h.sink.records[0]["response_body"]), {
            "text": "synthetic", "stop_reason": "end_turn", "usage": dict(zip(FIELDS, (10, 8, 0, 0)))})

    def test_durable_capture_bound_retains_latest_observed_usage(self):
        h = Harness("durable", [start(input_tokens=5), delta(output_tokens=3), delta(output_tokens=8)], max_events=2)
        with self.assertRaises(CaptureLimit):
            h.consume()
        self.assertEqual(h.totals(), [(5, 8, 0, 0)])
        self.assertEqual(h.statuses(), ["error"])

    def test_durable_cancellation_keeps_usage_read_before_cancellation_check(self):
        h = Harness("durable", [start(input_tokens=5), delta(output_tokens=3), delta(output_tokens=8)])
        context = {}
        stream = h.recorder.stream(context, {})
        next(stream)
        next(stream)
        context["cancelled"] = True
        with self.assertRaises(RecordingCancelled):
            next(stream)
        self.assertEqual(h.totals(), [(5, 8, 0, 0)])
        self.assertEqual(h.statuses(), ["cancelled"])

    def test_durable_settlement_failure_is_not_retried(self):
        h = Harness("durable", [start(input_tokens=5), delta(output_tokens=3), finish(output_tokens=8), STOP])
        h.executor.fail_record = True
        with self.assertRaises(RecordingPersistenceError):
            h.consume()
        self.assertEqual(len(h.provider.sources), 1)
        self.assertEqual(h.executor.rows, [])
        self.assertEqual(h.executor.transactions, ["begin", "rollback"])

    def test_malformed_counters_are_not_sanitized_by_maximum(self):
        for value in (-1, -1.5, 0.0):
            with self.subTest(value=value):
                h = Harness("durable", [start(), finish(output_tokens=value), STOP])
                with self.assertRaises(RecordingPersistenceError):
                    h.consume()
                self.assertEqual(h.executor.rows, [])
        # Preserve legacy additive handling for unsupported types; validation
        # remains the existing consumer/settlement responsibility.
        h = Harness("compatibility", [start(), finish(output_tokens=-1)])
        h.consume()
        self.assertEqual(h.totals(), [(0, -1, 0, 0)])

    def test_streaming_completion_and_meter_use_same_totals_and_callback_increments(self):
        events = [start(input_tokens=13, cache_read_tokens=2, cache_write_tokens=1),
                  delta(output_tokens=3), delta(output_tokens=3), finish(output_tokens=8), STOP]
        for kind in self.kinds:
            with self.subTest(kind=kind):
                h = Harness(kind, events)
                increments = []
                result = StreamingCompletion(h.recorder, check=lambda: None,
                    usage_callback=increments.append).complete(FenceContext(), {"max_tokens": 8})
                self.assertEqual(result[2], dict(zip(FIELDS, (13, 8, 2, 1))))
                self.assertEqual(h.totals(), [(13, 8, 2, 1)])
                self.assertEqual([item["output_tokens"] for item in increments], [0, 3, 0, 5])

    def test_unchanged_output_cap_retains_observed_cost_when_consumer_stops(self):
        for kind in self.kinds:
            with self.subTest(kind=kind):
                h = Harness(kind, [start(input_tokens=13), delta(output_tokens=3), finish(output_tokens=8), STOP])
                with self.assertRaisesRegex(RuntimeError, "token_budget"):
                    StreamingCompletion(h.recorder, check=lambda: None).complete(FenceContext(), {"max_tokens": 7})
                self.assertEqual(h.totals(), [(13, 8, 0, 0)])
                self.assertEqual(h.provider.sources[0].pulls, 3)
                self.assertEqual(h.provider.sources[0].closes, 1)

    def test_existing_provider_parsers_preserve_cumulative_contract(self):
        anthropic = [json.dumps(frame) for frame in (
            {"type": "message_start", "message": {"usage": {"input_tokens": 10,
                "cache_read_input_tokens": 2, "cache_creation_input_tokens": 1}}},
            {"type": "message_delta", "usage": {"output_tokens": 3}},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 8}},
            {"type": "message_stop"})]
        openai = [json.dumps({"choices": [{"delta": {"content": "synthetic"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 13, "completion_tokens": 8,
                      "prompt_tokens_details": {"cached_tokens": 2}}}), "[DONE]"]
        for kind in self.kinds:
            for parser, frames, expected in ((_anthropic_events, anthropic, (13, 8, 2, 1)),
                                             (_openai_events, openai, (13, 8, 2, 0))):
                with self.subTest(kind=kind, parser=parser.__name__):
                    provider = SimpleNamespace(stream=lambda c, r: parser(iter(frames), "synthetic"))
                    h = Harness(kind, provider=provider)
                    result = StreamingCompletion(h.recorder, check=lambda: None).complete(FenceContext(), {"max_tokens": 8})
                    self.assertEqual(tuple(result[2][name] for name in FIELDS), expected)
                    self.assertEqual(h.totals(), [expected])
