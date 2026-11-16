"""Independent selector and final raw disclosure checks through optional WSGI."""
import unittest
from artex.server.core import Request
from artex.db.notification_history import DeliverySelection
import test_metadata_read_setup as fixture

class MetadataReview(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_injected_history_selection_bool_cannot_equal_integer_before_grants(self):
        f=self.f
        result=f.history.handle(Request('GET','/api/notify/deliveries?channel_id=1'),selection=DeliverySelection(True))
        self.assertEqual(result.status,400);self.assertEqual(f.selections,[]);self.assertEqual(f.f.db.calls,[])
    def test_nested_request_does_not_replace_outer_selection_at_final_grants(self):
        f=self.f;nested=[]
        def start():nested.append(f.request('/api/notify/deliveries?channel_id=0')[0])
        status,_=f.request('/api/notify/deliveries?channel_id=2',start=start)
        self.assertEqual(status,200);self.assertEqual(nested,[200])
        selections=f.selections
        a=[s for s in selections if s.channel_id==2];b=[s for s in selections if s.channel_id==0]
        self.assertTrue(a and b);self.assertTrue(all(s is a[0] for s in a));self.assertTrue(all(s is b[0] for s in b))
        self.assertIsNot(a[0],b[0]);self.assertIs(selections[-1],a[0])
    def test_final_history_raw_grant_current_revocation_no_body(self):
        from contextlib import nullcontext
        from artex.server.notification_history import NotificationHistoryReadService
        f=self.f;started=[]
        def raw(request,selection):
            if started:f.current=False
            return True
        f.history=NotificationHistoryReadService(f.history.reader,authorize=lambda *a:True,raw_authorize=raw,current=lambda:f.current,lifecycle_guard=lambda:nullcontext())
        f.bundle=f.bind()
        with self.assertRaises(RuntimeError):f.request('/api/notify/deliveries',start=lambda:started.append(True))
