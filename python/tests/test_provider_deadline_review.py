"""Independent duration fences using fake connections, never sockets."""
import unittest
from unittest.mock import patch
from artex.agent.runtime import RuntimeContext
from artex.manager import CancellationToken
from artex.providers.http import TransportCancelled,TransportTimeout
import test_provider_http as fixture

class ProviderDeadlineReview(unittest.TestCase):
    def setUp(self):
        self.now=[1000.0];self.clock=lambda:self.now[0]
        self.context=RuntimeContext({},CancellationToken(),1005.0,self.clock)
    def test_elapsed_constructor_never_sends_request(self):
        conn=fixture.FakeConnection()
        def construct(*args,**kw):
            self.now[0]=1005.0
            return conn
        with patch('artex.providers.http.http.client.HTTPSConnection',side_effect=construct):
            with self.assertRaises((TransportCancelled,TransportTimeout)):
                fixture.send(fixture.transport(clock=lambda:7.0),self.context)
        self.assertEqual(conn.calls,[]);self.assertEqual(conn.closed,1)
    def test_stream_expiry_prevents_next_read_and_closes(self):
        conn=fixture.FakeConnection(fixture.FakeResponse(b'ab'))
        with patch('artex.providers.http.http.client.HTTPSConnection',return_value=conn):
            result=fixture.send(fixture.transport(clock=lambda:7.0,chunk_size=1),self.context,stream=True)
            it=iter(result.body);self.assertEqual(next(it),b'a')
            count=len(conn.response.reads);self.now[0]=1005.0
            with self.assertRaises((TransportCancelled,TransportTimeout)):next(it)
        self.assertEqual(len(conn.response.reads),count)
        self.assertEqual((conn.closed,conn.response.closed),(1,1))
    def test_context_shortened_during_read_does_not_disclose_chunk(self):
        def shorten():
            self.context.clamp_deadline(1000.25,clock=self.clock)
            self.now[0]=1000.5
        conn=fixture.FakeConnection(fixture.FakeResponse(b'private',on_read=shorten))
        with patch('artex.providers.http.http.client.HTTPSConnection',return_value=conn):
            result=fixture.send(fixture.transport(clock=lambda:7.0),self.context,stream=True)
            with self.assertRaises((TransportCancelled,TransportTimeout)):next(iter(result.body))
        self.assertEqual((conn.closed,conn.response.closed),(1,1))
