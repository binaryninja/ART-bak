#!/usr/bin/env python3
"""One live-provider fixture conversation in an empty disposable PostgreSQL DB."""
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlsplit
import uuid

from local_postgres_acceptance import validate_arguments, verify_connection

TOOL = 'read_acceptance_fixture'
PROMPT = ('Call read_acceptance_fixture once with no arguments. Then reply with '
          'exactly the value returned by that tool, with no additional text.')


class AcceptanceFailure(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise AcceptanceFailure(message)


def verify_exchange(records, fixture):
    """Validate the independently loaded normalized database records."""
    require(len(records) == 2, 'Expected exactly two recorded provider completions')
    first, last = records
    for row in records:
        require(row['status'] == 'ok', 'Provider recording did not succeed')
        require(row['request']['tools'] == [TOOL], 'Unexpected advertised tool')
        require(not row['raw_request'] and not row['raw_response'], 'Unexpected raw capture')
    require(fixture not in json.dumps(first['request']), 'Fixture leaked into initial request')
    response = first['response']
    calls = [b for b in response['message']['content'] if b['type'] == 'tool_use']
    require(response['stop_reason'] == 'tool_use' and len(calls) == 1,
            'Expected one provider-originated tool call')
    call = calls[0]
    require(call['name'] == TOOL and call['input'] == {}, 'Unexpected tool invocation')
    messages = last['request']['messages']
    results = [b for m in messages for b in m['content'] if b['type'] == 'tool_result']
    require(len(results) == 1 and results[0]['tool_use_id'] == call['id']
            and results[0]['content'] == [{'type': 'text', 'text': fixture}]
            and results[0].get('is_error') is False,
            'Durable tool result mismatch')
    require(response['message'] in messages, 'Provider tool call missing from next request')
    final = last['response']
    require(final['stop_reason'] == 'end_turn'
            and all(b['type'] == 'text' for b in final['message']['content'])
            and ''.join(b['text'] for b in final['message']['content']) == fixture,
            'Durable final response mismatch')
    return call['id']


def run(args, workspace, connection):
    import psycopg
    from artex.agent.host import AgentHost
    from artex.agent.runner import AgentRunner, RunPlan
    from artex.agent.runtime import BoundedAgent, Grant, Limits, Tool
    from artex.db.core import Query
    from artex.db.llm_records import LLMRecordStore
    from artex.db.pool import ExecutorPool
    from artex.db.profiles import LLMProfile, ProfileStore, RetryOverride, RetryRule
    from artex.db.recording_schema import ensure_recording_schema
    from artex.db.schema import SchemaExecutor, bootstrap_empty_database
    from artex.db.tasks import TaskCreateOptions, TaskStore
    from artex.llmrec.postgres import PostgresRecorder, RecordingScope
    from artex.manager import TaskManager
    from artex.providers import Response
    from artex.providers.factory import ProfileFactory
    from artex.providers.http import HTTPTransport
    from artex.transcripts import TranscriptStore, messages as transcript_messages

    key = os.environ[args.api_key_env]
    require(bool(key.strip()), 'Empty provider credential')
    parsed = urlsplit(args.base_url)
    require(parsed.scheme == 'https' and parsed.hostname and not parsed.username
            and not parsed.password and not parsed.query and not parsed.fragment,
            'Explicit credential-free HTTPS provider URL required')
    origin = f'https://{parsed.netloc}'
    generation = str(uuid.uuid4())
    fixture = secrets.token_hex(32)
    session = 'acceptance-' + generation
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    patch = subprocess.check_output(['git', 'diff', 'HEAD', '--', 'python'])
    sources = hashlib.sha256()
    python_root = Path(__file__).resolve().parents[1]
    for path in sorted([*python_root.joinpath('artex').rglob('*.py'), Path(__file__).resolve()]):
        sources.update(str(path.relative_to(python_root)).encode() + b'\0' + path.read_bytes())
    root = workspace / 'transcripts'
    root.mkdir(mode=0o700)
    transcript = TranscriptStore(str(root))
    admitted = threading.Event()
    owner = {}
    observations = []

    def observe(phase):
        h, manager = owner['handle'], owner['manager']
        require(TaskManager.owns_generation(manager, h.task_id, h.token, h),
                'Original generation ownership lost')
        with manager._lock:
            require(manager._runs[h.task_id].guarded_work > 0,
                    'Original generation guard absent')
        observations.append({'phase': phase, 'task_id': h.task_id, 'generation_id': generation})

    with psycopg.connect(**connection, autocommit=True) as db:
        verify_connection(db, args)
        schema = SchemaExecutor(db, cursor_factory=psycopg.RawCursor)
        bootstrap_empty_database(schema, expected_database=args.database, allow_schema_creation=True)
        ensure_recording_schema(schema, expected_database=args.database, action='create_or_upgrade')
    pool = ExecutorPool.postgres(psycopg.conninfo.make_conninfo(**connection), max_size=3)
    try:
        # Store the actual selection, but keep its credential only in memory.
        profile = LLMProfile(name='Fixture acceptance', format=args.format,
            base_url=args.base_url, model=args.model, streaming=False, max_tokens=2048,
            max_tokens_field='max_completion_tokens' if args.format == 'openai' else '',
            retry=RetryOverride(*(RetryRule(-1, 0) for _ in range(3))))
        profile_id = ProfileStore(pool).save(profile)
        profile = ProfileStore(pool).get(profile_id)
        profile.api_key = key
        transport = HTTPTransport([origin], timeout=30, total_timeout=90)

        def observed_transport(context, url, headers, body, stream):
            observe('provider_request')
            result = transport(context, url, headers, body, stream)
            def close():
                try:
                    result.close()
                finally:
                    observe('provider_response_closed')
            return Response(result.status, result.body, close)

        provider = ProfileFactory(lambda config: observed_transport,
                                  retry_max_attempts=2, retry_max_seconds=100).build(profile)

        def redact(direction, body, scope):
            require(key not in body, 'Credential found in normalized transcript')
            return body

        class RecordedProvider:
            def complete(self, context, request):
                observe('provider_binding')
                h = owner['handle']
                scope = RecordingScope(h.task_id, session, 'fixture', args.model,
                                       profile.name, owner['exploration_id'])
                recorder = PostgresRecorder(provider, LLMRecordStore(pool, task_id=h.task_id),
                    scope=scope, enabled=True, redact=redact,
                    binding_validator=lambda s, inner: s == scope and inner is provider)
                try:
                    return recorder.complete(context, request)
                except Exception as exc:
                    owner['provider_failure'] = type(exc).__name__
                    raise
                finally:
                    observe('provider_recording_finished')

        def read_fixture(context, arguments):
            observe('tool_call')
            try:
                require(arguments == {}, 'Fixture tool accepts no arguments')
                return fixture
            finally:
                observe('tool_cleanup')

        def gate(context, action):
            observe('permission_' + action.kind)
            allowed = ((action.kind, action.name) == ('model', 'complete') or
                       (action.kind, action.name) == ('tool', TOOL) and action.arguments() == {})
            return Grant(allowed, allowed, allowed)

        class OwnedAgent(BoundedAgent):
            def run(self, request, *, token=None, metadata=None):
                require(admitted.wait(30), 'Admission barrier timed out')
                h, manager = owner['handle'], owner['manager']
                require(token is h.token, 'Generation token changed')
                with TaskManager.guard_generation(manager, h.task_id, token, h):
                    observe('agent_enter')
                    try:
                        return super().run(request, token=token, metadata=metadata)
                    finally:
                        observe('agent_cleanup')

        def sink(task, token, result):
            h, manager = owner['handle'], owner['manager']
            require(str(task['id']) == h.task_id and token is h.token, 'Sink generation changed')
            with TaskManager.guard_generation(manager, h.task_id, token, h):
                observe('sink_enter')
                try:
                    require(result.reason == 'completed' and result.tool_calls == 1
                            and result.text == fixture, 'Bounded fixture task did not complete')
                    redact('transcript', json.dumps(result.messages), None)
                    for message in result.messages:
                        transcript.record_message(session, message)
                finally:
                    observe('sink_cleanup')

        agent = OwnedAgent(RecordedProvider(), gate=gate,
            tools=[Tool(TOOL, 'Read the acceptance fixture value.',
                        {'type': 'object', 'properties': {}, 'additionalProperties': False},
                        read_fixture, lambda value: value == {})],
            limits=Limits(max_turns=3, max_tokens=8192, max_seconds=120,
                          max_tool_calls=1, max_tool_output_bytes=256, max_request_bytes=16384))
        options = TaskCreateOptions(llm_profile_ids=[profile_id], coverage_enabled=False)
        def preflight(description, goal, selected, seed):
            return description == PROMPT and goal == PROMPT and selected == options and seed is False
        runner = AgentRunner(agent, validate_create=preflight,
            readiness=lambda task: task.get('description') == PROMPT and task.get('goal') == PROMPT,
            bootstrap=lambda task: RunPlan({'messages': [{'role': 'user', 'content': [
                {'type': 'text', 'text': PROMPT}]}]}, {'session_id': session}),
            result_sink=sink, max_pending=1)
        def enrich(dto):
            return dict(last_activity_unix=0, tokens=0, goals_total=0,
                        goals_met=0, in_flight=0, findings=0)
        manager = TaskManager(TaskStore(pool), runner=runner, dto_enricher=enrich,
                              profile_available=lambda identifier: identifier == profile_id)
        host = AgentHost(manager, runner)
        owner['manager'] = manager
        task = host.create_task(PROMPT, PROMPT, options)
        require(len(runner.handles()) == 1, 'Expected one submitted task generation')
        handle = runner.handles()[0]
        owner.update(handle=handle, exploration_id=TaskStore(pool).get(int(handle.task_id)).exploration_id)
        require(task['id'] == handle.task_id and manager.owns_generation(handle.task_id, handle.token, handle),
                'Generation was not installed')
        admitted.set()
        deadline = time.monotonic() + 150
        while not handle.wait(min(10, max(0, deadline-time.monotonic()))):
            require(time.monotonic() < deadline, 'Provider task failed to drain')
        require(handle.failure == '' and handle.terminal_status == 'done',
                'Runner failed: ' + (handle.failure or 'none') + '; result=' +
                (handle.result.reason if handle.result else 'no result') + '; provider=' +
                owner.get('provider_failure', 'none'))
        settled = host.poll()
        require(settled.settled == (handle.task_id,) and settled.all_settled, 'Generation did not settle')

        # A separate connection, independent of all writer/pool/manager caches.
        with psycopg.connect(**connection, autocommit=True) as db:
            verify_connection(db, args)
            rows = db.execute('SELECT id,status,request_body,response_body,raw_request,raw_response '
                              'FROM llm_records WHERE task_id=%s AND session_id=%s AND model=%s '
                              'ORDER BY id', (handle.task_id, session, args.model)).fetchall()
            records = [dict(id=r[0], status=r[1], request=json.loads(r[2]), response=json.loads(r[3]),
                            raw_request=r[4], raw_response=r[5]) for r in rows]
            call_id = verify_exchange(records, fixture)
            durable = db.execute('SELECT status,completed_at,paused,queued FROM tasks WHERE id=%s',
                                 (int(handle.task_id),)).fetchone()
            require(durable and durable[0] == 'done' and durable[1] is not None
                    and durable[2:] == (False, False), 'Durable task completion missing')
            require(db.execute('SELECT count(*) FROM tasks').fetchone()[0] == 1, 'Task submitted more than once')
        persisted_messages, _ = transcript_messages(TranscriptStore(str(root)).read(session))
        require(persisted_messages == handle.result.messages, 'Fresh file transcript differs')
        state = manager.retirement_snapshot(handle.task_id)
        require(state[0] == 'done' and state[1] is not None and state[2:] == (False, False)
                and not manager.owns_generation(handle.task_id, handle.token, handle, allow_cancelled=True)
                and runner.handles() == (), 'Generation still retained')
        phases = [v['phase'] for v in observations]
        for phase, count in [('provider_request', 2), ('provider_response_closed', 2),
                             ('provider_recording_finished', 2), ('tool_call', 1),
                             ('tool_cleanup', 1), ('agent_cleanup', 1), ('sink_cleanup', 1)]:
            require(phases.count(phase) == count, 'Missing guarded callback: ' + phase)
        host.stop_admissions()
        drained = host.drain(5)
        require(drained.all_settled and host.close_lifetime(drained)
                and host.lifetime_closed(drained), 'Host lifetime did not close')
        require(pool.close(timeout=5).drained, 'Database pool did not close')
        evidence = dict(result='pass', revision=revision, working_diff_sha256=hashlib.sha256(patch).hexdigest(),
            python_source_sha256=sources.hexdigest(), model=args.model, provider_origin=origin,
            database=args.database, task_id=handle.task_id, generation_id=generation, session_id=session,
            profile_id=profile_id, fixture_value=fixture, tool_call_id=call_id,
            completed_at=durable[1].isoformat(), records=records, ownership_observations=observations,
            settlement=asdict(settled), drain=asdict(drained), host_closed=True, pool_closed=True)
        text = json.dumps(evidence, indent=2, sort_keys=True) + '\n'
        redact('evidence', text, None)
        (workspace / 'evidence.json').write_text(text)
        return evidence
    finally:
        # Never replay an uncertain provider call or report failed acceptance as clean.
        admitted.set()
        if 'handle' in owner and not owner['handle'].wait(0):
            owner['handle'].cancel('acceptance_failed')
            owner['handle'].wait(150)
        pool.close(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True)
    parser.add_argument('--port', type=int, default=5432)
    parser.add_argument('--database', required=True)
    parser.add_argument('--user', required=True)
    parser.add_argument('--confirm-disposable', required=True)
    parser.add_argument('--format', choices=('openai', 'anthropic'), default='openai')
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--api-key-env', required=True)
    parser.add_argument('--output-parent', type=Path, default=Path(tempfile.gettempdir()))
    args = parser.parse_args()
    validate_arguments(args)
    workspace = Path(tempfile.mkdtemp(prefix='artex-provider-acceptance-', dir=args.output_parent)).resolve()
    passfile = workspace / 'empty.pgpass'
    passfile.touch(mode=0o600)
    connection = dict(host=args.host, port=args.port, dbname=args.database, user=args.user,
        password='', passfile=str(passfile), sslmode='disable', connect_timeout=3,
        options='-c statement_timeout=4000', application_name='artex_fixture_acceptance')
    print('Acceptance workspace:', workspace, flush=True)
    try:
        evidence = run(args, workspace, connection)
    except Exception as exc:
        detail = str(exc) if type(exc) is AcceptanceFailure else type(exc).__name__
        print('FAIL:', detail, '(database and workspace retained)', flush=True)
        raise SystemExit(1) from None
    print('PASS:', evidence['task_id'], evidence['generation_id'], workspace / 'evidence.json', flush=True)


if __name__ == '__main__':
    main()
