"""Different clock epochs; fake HTTP connections only."""
import unittest
from unittest.mock import patch
from artex.agent.runtime import RuntimeContext
from artex.manager import CancellationToken
from artex.providers.http import TransportCancelled,TransportPolicyError,TransportTimeout
import test_provider_http as f

class Tests(unittest.TestCase):
    def context(self,remaining=3):
        self.now=[1000000.0];self.token=CancellationToken()
        return RuntimeContext({},self.token,self.now[0]+remaining,lambda:self.now[0])
    def test_distinct_clock_epochs_clamp_constructor_and_socket(self):
        context=self.context();conn=f.FakeConnection()
        with patch('artex.providers.http.http.client.HTTPSConnection',return_value=conn) as factory:
            f.send(f.transport(clock=lambda:7),context)
        self.assertEqual(factory.call_args.kwargs['timeout'],3)
        self.assertTrue(conn.sock.timeouts)
        self.assertTrue(all(0<t<=3 for t in conn.sock.timeouts))
    def test_nested_context_and_elapsed_read_reclamp(self):
        context=self.context();conn=f.FakeConnection(f.FakeResponse(b'ab',on_read=lambda:self.now.__setitem__(0,self.now[0]+.5)))
        with patch('artex.providers.http.http.client.HTTPSConnection',return_value=conn):
            result=f.send(f.transport(clock=lambda:7,chunk_size=1),{'runtime_context':context})
        self.assertEqual(result.body,b'ab')
        self.assertLess(min(conn.sock.timeouts),3)
    def test_expired_or_revoked_never_constructs_connection(self):
        for cancelled in (False,True):
            context=self.context(0 if not cancelled else 3)
            if cancelled:self.token.cancel('fixture')
            with patch('artex.providers.http.http.client.HTTPSConnection') as factory:
                with self.assertRaises((TransportCancelled,TransportTimeout)):f.send(f.transport(),context)
            factory.assert_not_called()
    def test_nonruntime_context_keeps_transport_timeout(self):
        conn=f.FakeConnection()
        with patch('artex.providers.http.http.client.HTTPSConnection',return_value=conn) as factory:
            f.send(f.transport(timeout=8,clock=lambda:7),{})
        self.assertEqual(factory.call_args.kwargs['timeout'],8)
    def test_invalid_duration_never_constructs_connection(self):
        context=self.context()
        for value in (True,float('nan'),float('inf'),-1,0,'3'):
            with patch.object(RuntimeContext,'remaining_seconds',return_value=value),patch('artex.providers.http.http.client.HTTPSConnection') as factory:
                with self.assertRaises((TransportPolicyError,TransportTimeout)):f.send(f.transport(),context)
            factory.assert_not_called()
