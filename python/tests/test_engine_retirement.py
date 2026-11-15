"""Runner retirement handshake on actual inert bootstrap handles, no timed runtime."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import test_engine_runner_bootstrap as fixture
from artex.manager import CancellationToken,TaskConflict

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.EngineBootstrapTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.host=self.f.f.host;self.manager=self.f.f.manager;self.runner=self.f.runner
        self.host.start('5');self.handle=self.runner.handles()[0]

    def test_retirement_denial_retains_cancelled_handle_without_completion(self):
        self.host.pause('5')
        with patch.object(self.manager,'acknowledge_deadline_retirement',return_value=False) as ack:
            state=self.host.poll()
            self.assertEqual(state.settled,())
            self.assertEqual(self.runner.handles(),(self.handle,))
            ack.assert_called_once_with('5',self.handle.token,self.handle)
        self.host.poll()
        self.assertEqual(self.runner.handles(),())

    def test_retirement_ack_runs_without_runner_lock(self):
        self.host.pause('5')
        actual=self.manager.acknowledge_deadline_retirement
        with ThreadPoolExecutor(max_workers=1) as executor:
            def acknowledge(*args):
                # A separate thread can take inventory while acknowledgement runs.
                self.assertEqual(executor.submit(self.runner.handles).result(1),(self.handle,))
                return actual(*args)
            with patch.object(self.manager,'acknowledge_deadline_retirement',side_effect=acknowledge):
                self.host.poll()
        self.assertEqual(self.runner.handles(),())

    def test_direct_replacement_cannot_bypass_denied_retirement(self):
        self.host.pause('5')
        with patch.object(self.manager,'acknowledge_deadline_retirement',return_value=False):
            with self.assertRaises(TaskConflict):
                self.runner.start(self.f.constructions[0],CancellationToken())
        self.assertEqual(self.runner.handles(),(self.handle,))
