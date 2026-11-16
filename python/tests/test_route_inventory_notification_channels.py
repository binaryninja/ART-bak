import unittest
import test_route_inventory_detail as fixture
class Tests(unittest.TestCase):
    def test_exact_read_guard(self):
        rows=fixture.Tests().scan("        if 'notification_channels' in services:staged.add('GET','/api/notify/channels',handler)")
        self.assertEqual(rows[0]['path'],'/api/notify/channels');self.assertFalse(rows[0]['public'])
    def test_unknown_decl_refused(self):
        for text in ("if 'notification_channels' in other:staged.add('GET','/api/notify/channels',handler)","if 'notification_channels' in services:staged.add('POST','/api/notify/channels',handler)","if 'notification_channels' in services:staged.add('GET','/api/notify/other',handler)"):
            with self.assertRaises(fixture.fixture.audit.AuditError):fixture.Tests().scan('        '+text)
