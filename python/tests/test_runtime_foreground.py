"""Foreground lifecycle, including real loopback HTTP with inert runtime stores."""
import http.client
import socket
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch, Mock
import unittest
from artex.runtime_lease import RuntimeLease, LeaseUnavailable
from artex.server.runtime_listener import RuntimeListener, DevListenerConfig
from artex.server.runtime_session import RuntimeSession, SessionResult
from artex.server.core import json_response
from artex.server.auth import sign_jwt
import test_runtime_listener as fixtures


class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.lease=RuntimeLease(str(Path(self.tmp.name)/'runtime.lock'));self.lease.acquire()
        self.addCleanup(self.lease.release)
        self.f.bundle.bootstrap.app.router.add('GET','/probe',lambda req,p:json_response(200,{'ok':True}))
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));self.port=sock.getsockname()[1]
        self.listener=RuntimeListener(self.f.bundle,self.f.owner,DevListenerConfig(port=self.port))
        self.session=RuntimeSession(self.listener,self.lease)
        self.addCleanup(self.session.stop,1)
        self.event=threading.Event()

    def test_real_loopback_pump_authenticated_read_and_stop(self):
        results=[]
        driver=threading.Thread(target=lambda:results.append(self.session.run_until_stopped(self.event,poll_interval=.01)))
        driver.start()
        def cleanup():
            self.event.set();driver.join(5)
        self.addCleanup(cleanup)
        end=time.monotonic()+3
        while self.session.result.reason=='not_started' and driver.is_alive() and time.monotonic()<end:
            time.sleep(.005)
        self.assertTrue(self.session.result.started,self.session.result)
        self.assertTrue(self.f.bundle.pump._thread.is_alive())
        for token,expected in ((None,401),(sign_jwt(b'x'*32),200)):
            connection=http.client.HTTPConnection('127.0.0.1',self.port,timeout=2)
            try:
                headers={} if token is None else {'Authorization':'Bearer '+token}
                connection.request('GET','/probe',headers=headers)
                response=connection.getresponse();body=response.read()
                self.assertEqual(response.status,expected,body)
            finally:connection.close()
        self.event.set();driver.join(3)
        self.assertFalse(driver.is_alive())
        self.assertEqual(results[0].reason,'stop_requested')
        self.assertTrue(results[0].shutdown.stopped)
        self.assertFalse(self.listener._thread.is_alive())
        self.assertFalse(self.f.bundle.pump._thread.is_alive())
        self.assertEqual(self.f.f.p.calls,[])
        with self.assertRaises(LeaseUnavailable):self.lease.validate()

    def test_pre_requested_stop_never_starts(self):
        self.event.set()
        with patch.object(self.listener,'start_listener') as start:
            result=self.session.run_until_stopped(self.event)
        start.assert_not_called();self.assertTrue(result.shutdown.stopped)
        self.assertEqual(result.reason,'stop_requested')

    def test_start_failure_not_retried(self):
        with patch.object(self.session,'start',return_value=SessionResult(False,False,'startup_failed_unresolved')),patch.object(self.session,'stop') as stop:
            result=self.session.run_until_stopped(self.event)
        stop.assert_not_called();self.assertFalse(result.shutdown.stopped)
        self.assertFalse(self.session._driving)
        self.assertTrue(self.lease.validate())

    def test_health_failure_drains_and_sanitizes(self):
        for reason in ('pump_failed','listener_stopped','pump_stopped','runtime_unavailable','interrupted'):
            with self.subTest(reason=reason):
                # No actual startup here; simulate only the observation boundary.
                self.session._state='new'
                self.listener._thread=Mock();self.listener._thread.is_alive.return_value=reason!='listener_stopped'
                self.f.bundle.pump._thread=Mock();self.f.bundle.pump._thread.is_alive.return_value=reason!='pump_stopped'
                self.f.bundle.pump._failure='private payload' if reason=='pump_failed' else ''
                effect=RuntimeError('private payload') if reason=='runtime_unavailable' else KeyboardInterrupt() if reason=='interrupted' else None
                with patch.object(self.session,'start',return_value=SessionResult(True,False,'running')),patch.object(self.listener,'_check',side_effect=effect),patch.object(self.session,'stop',return_value=SessionResult(False,False,'stopped_unresolved')) as stop:
                    result=self.session.run_until_stopped(self.event,poll_interval=.01)
                self.assertEqual(result.reason,reason);stop.assert_called_once()
                self.assertFalse(result.shutdown.stopped);self.assertTrue(self.lease.validate())
                self.assertNotIn('private',repr(result))
        self.listener._thread=None;self.f.bundle.pump._thread=None;self.f.bundle.pump._failure=''

    def test_invalid_inputs_and_duplicate_driver_before_start(self):
        for event,interval in ((None,.1),(self.event,True),(self.event,0),(self.event,2)):
            with self.assertRaises(ValueError):self.session.run_until_stopped(event,poll_interval=interval)
        self.session._driving=True
        with self.assertRaises(RuntimeError):self.session.run_until_stopped(self.event)
        self.session._driving=False
        self.assertEqual(self.f.calls,[])
