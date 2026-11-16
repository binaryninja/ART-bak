"""Explicit foreground-only Norma v0.4.3 tool/bash.go subset.

Bash(command, timeout_ms?, run_in_background=false) preserves its tool name and
foreground timeout defaults (120s, ceiling 600s), output and exit annotations.
No automatic registration, shell discovery, inherited environment, retries,
background timeout fallback, PTY/session features or capture-file spilling.

The host supplies a launch gate AND runner. A BoundedAgent gate alone does not
replace this boundary: every launch requires a fresh Grant with all three flags
literally True. Immutable Launch includes exact command, cwd, environment and
limits. The gate must authorize ALL effects, not merely the working directory.
The configured root is a starting directory, NOT a filesystem/network sandbox:
shell commands can access elsewhere or the network. Hosts requiring confinement
must supply an OS/container sandbox runner and deny anything outside that scope.

PosixRunner is an explicit opt-in, unsandboxed implementation, never a default.
It uses a new process group, no stdin, bounded combined output, and kills the
group on completion/cancellation/timeout/output overflow, then reaps the leader.
Descendants that escape the process group are NOT contained; use external
isolation when untrusted commands can do this. Numeric process-group signaling
also has an OS identifier-reuse race after leader exit/reaping; it is not a
persistent kernel containment handle or proof that every descendant drained. Cancellation is cooperative at
poll boundaries; process creation/kernel waits may block. Cleanup failures are
reported as uncertainty, not success, and must prevent host resource reuse.
"""
from dataclasses import dataclass, field
import math
import os
import re
import selectors
import signal
import subprocess
import time

from ..agent.runtime import EffectUncertain, Grant, Tool


class ProcessError(RuntimeError):
    pass


class CleanupUncertain(ProcessError, EffectUncertain):
    pass


@dataclass(frozen=True)
class Launch:
    command: str = field(repr=False)
    cwd: str
    shell: str
    environment: tuple = field(repr=False)
    timeout_ms: int
    max_output_bytes: int


@dataclass(frozen=True)
class ProcessResult:
    output: bytes
    exit_code: int
    timed_out: bool = False
    output_limited: bool = False


# Preserve Norma's hard-denial floor, not a purported complete shell analyzer.
_DENIED = tuple(re.compile(p) for p in (
    r'\brm\s+(-[a-zA-Z]*\s+)*-[a-zA-Z]*[rf][a-zA-Z]*\s+(-[a-zA-Z]+\s+)*/(\s|$)',
    r':\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;',
    r'\bmkfs\.\w+\s+/dev/', r'\bdd\b[^\n]*\bof=/dev/(sd|nvme|hd)',
))


def _check(context):
    check = getattr(context, 'check', None)
    if not callable(check):
        raise ProcessError('Cancellation-aware context required')
    check()


def _text(value, name):
    if not isinstance(value, str) or '\x00' in value:
        raise ValueError('Invalid ' + name)
    value.encode('utf-8')
    return value


class BashTool:
    def __init__(self, root, *, shell, environment, gate, runner,
                 max_output_bytes=65536, max_timeout_ms=600000):
        _text(root, 'root')
        _text(shell, 'shell')
        if (not os.path.isabs(root) or os.path.realpath(root) != root or
                not os.path.isdir(root)):
            raise ValueError('Explicit canonical existing working root required')
        if not os.path.isabs(shell) or os.path.normpath(shell) != shell:
            raise ValueError('Explicit absolute shell required')
        if not callable(gate) or not callable(runner):
            raise ValueError('Explicit launch gate and runner required')
        if type(environment) is not dict:
            raise ValueError('Explicit environment mapping required')
        for k, v in environment.items():
            if not _text(k, 'environment key') or '=' in k:
                raise ValueError('Invalid environment key')
            _text(v, 'environment value')
        if type(max_output_bytes) is not int or max_output_bytes <= 0:
            raise ValueError('Positive output bound required')
        if type(max_timeout_ms) is not int or not 1 <= max_timeout_ms <= 600000:
            raise ValueError('Timeout ceiling must be within 1..600000 ms')
        self.root, self.shell = root, shell
        self.environment = tuple(sorted(environment.items()))
        self.gate, self.runner = gate, runner
        self.max_output_bytes, self.max_timeout_ms = max_output_bytes, max_timeout_ms
        st = os.stat(root)
        self._root_identity = (st.st_dev, st.st_ino)

    def validate(self, args):
        if type(args) is not dict or set(args) - {'command', 'timeout_ms', 'run_in_background'}:
            raise ValueError('Unsupported Bash arguments')
        command = _text(args.get('command'), 'command')
        if not command.strip() or len(command.encode('utf-8')) > 65536:
            raise ValueError('Command must contain 1..65536 UTF-8 bytes')
        if 'timeout_ms' in args and type(args['timeout_ms']) is not int:
            raise ValueError('timeout_ms must be an integer')
        if 'run_in_background' in args and args['run_in_background'] is not False:
            raise ValueError('Background execution is unsupported')
        if any(p.search(command) for p in _DENIED):
            raise ProcessError('Command blocked by the source safety floor')
        return True

    def run(self, context, args):
        self.validate(args)
        timeout = args.get('timeout_ms', 0)
        timeout = min(timeout if timeout > 0 else 120000, self.max_timeout_ms)
        launch = Launch(args['command'], self.root, self.shell, self.environment,
                        timeout, self.max_output_bytes)
        _check(context)
        grant = self.gate(context, launch)
        if not isinstance(grant, Grant) or not all(x is True for x in
                (grant.permission, grant.in_scope, grant.approved)):
            raise ProcessError('Launch denied')
        _check(context)
        # Detect observed root replacement, not an OS security boundary/CAS.
        st = os.stat(self.root)
        if os.path.realpath(self.root) != self.root or (st.st_dev, st.st_ino) != self._root_identity:
            raise ProcessError('Working root changed')
        result = self.runner(context, launch)
        if (not isinstance(result, ProcessResult) or type(result.output) is not bytes or
                len(result.output) > launch.max_output_bytes or type(result.exit_code) is not int or
                type(result.timed_out) is not bool or type(result.output_limited) is not bool):
            raise ProcessError('Invalid process runner result')
        _check(context)
        text = result.output.decode('utf-8', errors='replace')
        if result.output_limited:
            return text + '\n\n[output limit reached; process group stopped]'
        if result.timed_out:
            return text + f'\n\n[command timed out after {timeout}ms]'
        if result.exit_code:
            return (text or '(no output)') + f'\n\n[exit code {result.exit_code}]'
        return text if text.strip() else '(command produced no output)'

    def tools(self):
        return (Tool('Bash', 'Run an explicitly authorized foreground shell command. No sandbox, background or TTY.',
            {'type': 'object', 'properties': {'command': {'type': 'string'},
              'timeout_ms': {'type': 'integer'}, 'run_in_background': {'type': 'boolean', 'const': False}},
             'required': ['command'], 'additionalProperties': False}, self.run, self.validate),)


class PosixRunner:
    """Opt-in host primitive. Inject dependencies for tests; never runs on import."""
    def __init__(self, *, popen=subprocess.Popen, selector=selectors.DefaultSelector,
                 killpg=os.killpg, read=os.read, clock=time.monotonic,
                 poll_seconds=0.05, cleanup_seconds=2):
        if os.name != 'posix':
            raise ValueError('POSIX process groups required')
        for n in (poll_seconds, cleanup_seconds):
            if type(n) not in (int, float) or not math.isfinite(n) or n <= 0:
                raise ValueError('Positive finite polling/cleanup bounds required')
        self.popen, self.selector, self.killpg = popen, selector, killpg
        self.read, self.clock = read, clock
        self.poll_seconds, self.cleanup_seconds = poll_seconds, cleanup_seconds

    def __call__(self, context, launch):
        _check(context)
        proc = None
        selector = None
        output = bytearray()
        timed_out = limited = False
        try:
            deadline = self.clock() + launch.timeout_ms / 1000
            proc = self.popen([launch.shell, '--noprofile', '--norc', '-c', launch.command],
                cwd=launch.cwd, env=dict(launch.environment), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, close_fds=True,
                start_new_session=True)
            selector = self.selector()
            selector.register(proc.stdout, selectors.EVENT_READ)
            while True:
                _check(context)
                remaining = deadline - self.clock()
                if remaining <= 0:
                    timed_out = True
                    break
                ready = selector.select(min(self.poll_seconds, remaining))
                if ready:
                    chunk = self.read(proc.stdout.fileno(), min(65536, launch.max_output_bytes - len(output) + 1))
                    if not chunk:
                        # EOF can precede exit; still wait cooperatively until deadline.
                        selector.unregister(proc.stdout)
                    else:
                        available = launch.max_output_bytes - len(output)
                        output.extend(chunk[:available])
                        if len(chunk) > available:
                            limited = True
                            break
                if not selector.get_map() and proc.poll() is not None:
                    break
        except (OSError, ValueError) as exc:
            raise ProcessError('Process execution failed') from None
        finally:
            # Cleanup is a drain boundary even under interruption. Catch every
            # individual stage so a BaseException cannot skip later resources.
            # An originating exception propagates unchanged if drain succeeds.
            cleanup_failed = False
            if proc is not None:
                try:
                    self.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except BaseException:
                    cleanup_failed = True
                try:
                    proc.wait(timeout=self.cleanup_seconds)
                except BaseException:
                    cleanup_failed = True
                if proc.stdout is not None:
                    try:
                        proc.stdout.close()
                    except BaseException:
                        cleanup_failed = True
            if selector is not None:
                try:
                    selector.close()
                except BaseException:
                    cleanup_failed = True
            if cleanup_failed:
                raise CleanupUncertain('Process group cleanup could not be verified') from None
        return ProcessResult(bytes(output), proc.returncode, timed_out, limited)
