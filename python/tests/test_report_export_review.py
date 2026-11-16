"""Independent pure exporter validation; no filesystem or external data."""
import unittest,json
from dataclasses import replace
import test_report_export as fixture
from artex.report_export import findings_evidence_zip

class ExportReview(unittest.TestCase):
    def setUp(self):self.f=fixture.Tests();self.f.setUp()
    def test_invalid_text_rejected_before_copy_protocol_callback(self):
        f=self.f;called=[]
        class Unexpected:
            def __deepcopy__(self,memo):called.append(True);return 'injected'
        f.f.report=Unexpected()
        with self.assertRaises((ValueError,TypeError)):f.export()
        self.assertEqual(called,[])
    def test_timestamp_not_arbitrary_formatter(self):
        f=self.f;called=[]
        class Unexpected:
            def strftime(self,pattern):called.append(True);return 'injected'
        f.f.created_at=Unexpected()
        with self.assertRaises((ValueError,TypeError)):f.export()
        self.assertEqual(called,[])
    def test_boolean_http_status_not_integer_wire_value(self):
        f=self.f;f.binding.status=True;f.manifest['snapshot']['status']=1
        f.e=replace(f.e,manifest_json=json.dumps(f.manifest).encode())
        with self.assertRaises(ValueError):f.export()
