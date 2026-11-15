import signal
import subprocess
import tempfile
import unittest
from types import SimpleNamespace

from artex.agent.runtime import Grant
from artex.tools.process import BashTool, CleanupUncertain, Launch, PosixRunner, ProcessError, ProcessResult


class Context:
    def __init__(self):
        self.cancelled = False
    def check(self):
        if self.cancelled:
            raise RuntimeError('cancelled')


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ctx = Context()
        self.launches = []
        self.grants = []
    def tool(self, gate=None, runner=None, **kwargs):
        def approve(ctx, launch):
            self.grants.append(launch)
            return Grant(True, True, True)
        def run(ctx, launch):
            self.launches.append(launch)
            return ProcessResult(b'fixture', 0)
        return BashTool(self.tmp.name, shell='/bin/bash', environment={},
                        gate=gate or approve, runner=runner or run, **kwargs)
    def test_exact_launch_and_schema(self):
        t = self.tool()
        self.assertEqual(t.run(self.ctx, {'command': 'inert fixture'}), 'fixture')
        self.assertEqual(self.launches, self.grants)
        self.assertEqual(self.launches[0].environment, ())
        self.assertEqual(self.launches[0].timeout_ms, 120000)
        self.assertEqual(t.tools()[0].name, 'Bash')
    def test_denied_never_launches(self):
        for grant in (None, True, Grant(True, False, True), Grant(1, True, True)):
            with self.assertRaises(ProcessError):
                self.tool(gate=lambda c, l: grant).run(self.ctx, {'command': 'fixture'})
        self.assertFalse(self.launches)
    def test_cancel_during_gate(self):
        def gate(c, l):
            c.cancelled = True
            return Grant(True, True, True)
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            self.tool(gate=gate).run(self.ctx, {'command': 'fixture'})
        self.assertFalse(self.launches)
    def test_strict_arguments_and_safety_floor(self):
        for args in ({'command': ' '}, {'command': 'x', 'run_in_background': True},
                     {'command': 'x', 'timeout_ms': True}, {'command': 'x', 'tty': True},
                     {'command': 'rm -rf /'}, {'command': '\ud800'}):
            with self.assertRaises((ValueError, ProcessError)):
                self.tool().run(self.ctx, args)
        self.assertFalse(self.launches)
        self.assertFalse(self.grants)
    def test_timeout_clamps_and_defaults(self):
        t = self.tool(max_timeout_ms=5000)
        for n in (0, -1, 999999):
            t.run(self.ctx, {'command': 'fixture', 'timeout_ms': n})
            self.assertEqual(self.launches[-1].timeout_ms, 5000)
    def test_result_annotations(self):
        for result, expected in ((ProcessResult(b'', 0), 'no output'),
            (ProcessResult(b'', 2), 'exit code 2'),
            (ProcessResult(b'', -9, timed_out=True), 'timed out'),
            (ProcessResult(b'abc', -9, output_limited=True), 'output limit')):
            text = self.tool(runner=lambda c,l: result).run(self.ctx, {'command':'fixture'})
            self.assertIn(expected, text)
    def test_oversized_result_rejected(self):
        with self.assertRaises(ProcessError):
            self.tool(max_output_bytes=2, runner=lambda c,l: ProcessResult(b'abc',0)).run(self.ctx, {'command':'fixture'})
    def test_bounded_agent_gate_and_pairing(self):
        from artex.agent.runtime import BoundedAgent
        from test_agent_runtime import FakeProvider, assistant, request
        t = self.tool()
        p = FakeProvider(assistant('', [('call', 'Bash', {'command': 'fixture'})]), assistant())
        def outer(c, action):
            return Grant(True, action.kind == 'model', True)
        self.assertEqual(BoundedAgent(p, gate=outer, tools=t.tools()).run(request()).reason, 'permission_denied')
        self.assertFalse(self.launches)
        p = FakeProvider(assistant('', [('call', 'Bash', {'command': 'fixture'})]), assistant())
        result = BoundedAgent(p, gate=lambda c,a: Grant(True,True,True), tools=t.tools()).run(request())
        self.assertEqual(result.reason, 'completed')
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(p.requests[-1]['messages'][-1]['content'][0]['tool_use_id'], 'call')

    def test_missing_dependencies(self):
        with self.assertRaises(ValueError):
            BashTool(self.tmp.name, shell='/bin/bash', environment={}, gate=None, runner=None)


class FakeStream:
    closed = False
    def fileno(self): return 88
    def close(self): self.closed = True

class FakeProcess:
    pid = 54321
    returncode = 0
    def __init__(self): self.stdout = FakeStream(); self.waits = []
    def poll(self): return self.returncode
    def wait(self, timeout): self.waits.append(timeout); return self.returncode

class FakeSelector:
    def __init__(self): self.registered = False; self.closed = False
    def register(self, stream, flags): self.registered = True
    def unregister(self, stream): self.registered = False
    def get_map(self): return {1:1} if self.registered else {}
    def select(self, timeout): return [(None,None)] if self.registered else []
    def close(self): self.closed = True


class RunnerTests(unittest.TestCase):
    def setup_runner(self, chunks, *, context=None, clock=None):
        self.proc, self.sel = FakeProcess(), FakeSelector()
        self.kills, self.spawns = [], []
        def popen(*a, **kw): self.spawns.append((a, kw)); return self.proc
        def read(fd, cap):
            data = chunks.pop(0)
            self.assertLessEqual(len(data), cap)
            return data
        self.runner = PosixRunner(popen=popen, selector=lambda:self.sel,
            killpg=lambda pid,sig:self.kills.append((pid,sig)), read=read,
            clock=clock or (lambda:0))
        return context or Context(), Launch('inert fixture','/fixture','/bin/bash',(),1000,4)
    def test_normal_closes_and_reaps_group(self):
        c,l = self.setup_runner([b'ab',b''])
        result = self.runner(c,l)
        self.assertEqual(result.output,b'ab')
        self.assertEqual(self.kills,[(54321,signal.SIGKILL)])
        self.assertTrue(self.proc.stdout.closed and self.sel.closed)
        kw = self.spawns[0][1]
        self.assertEqual(kw['env'],{})
        self.assertTrue(kw['start_new_session'] and kw['close_fds'])
        self.assertEqual(kw['stdin'],subprocess.DEVNULL)
    def test_limit_stops_without_unbounded_buffer(self):
        c,l = self.setup_runner([b'abcde'])
        result = self.runner(c,l)
        self.assertTrue(result.output_limited)
        self.assertEqual(result.output,b'abcd')
        self.assertTrue(self.kills)
    def test_timeout_cleanup(self):
        ticks = iter([0,2])
        c,l = self.setup_runner([],clock=lambda:next(ticks))
        self.assertTrue(self.runner(c,l).timed_out)
        self.assertTrue(self.kills)
    def test_cancellation_cleanup(self):
        c,l = self.setup_runner([])
        def select(timeout):
            c.cancelled = True
            return []
        self.sel.select = select
        with self.assertRaisesRegex(RuntimeError,'cancelled'):
            self.runner(c,l)
        self.assertTrue(self.kills and self.proc.stdout.closed)
    def test_cleanup_uncertainty_never_success(self):
        c,l = self.setup_runner([b''])
        def wait(timeout): raise subprocess.TimeoutExpired('redacted',timeout)
        self.proc.wait = wait
        with self.assertRaises(CleanupUncertain): self.runner(c,l)
        self.assertTrue(self.sel.closed)
    def test_originating_baseexception_preserved_after_successful_cleanup(self):
        c,l = self.setup_runner([])
        class Interrupted(BaseException):
            pass
        original = Interrupted('fixture interruption')
        def interrupt(timeout):
            raise original
        self.sel.select = interrupt
        with self.assertRaises(Interrupted) as caught:
            self.runner(c,l)
        self.assertIs(caught.exception, original)
        self.assertTrue(self.kills and self.proc.waits and self.proc.stdout.closed and self.sel.closed)

    def test_pipe_close_failure_still_closes_selector(self):
        c,l = self.setup_runner([b''])
        def fail(): raise OSError('private path')
        self.proc.stdout.close = fail
        with self.assertRaisesRegex(CleanupUncertain, '^Process group cleanup could not be verified$'):
            self.runner(c,l)
        self.assertTrue(self.sel.closed)

    def test_spawn_failure_redacted(self):
        c,l = self.setup_runner([])
        def fail(*a,**kw): raise OSError('secret value')
        self.runner.popen = fail
        with self.assertRaisesRegex(ProcessError,'^Process execution failed$'):
            self.runner(c,l)
    def test_bad_bounds(self):
        for value in (0,True,float('nan'),float('inf')):
            with self.assertRaises(ValueError): PosixRunner(poll_seconds=value)

if __name__ == '__main__': unittest.main()
