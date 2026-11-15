"""Independent multipart parser checks; no filesystem operations."""
import unittest
from artex.server.workspace_upload import _multipart


class Review(unittest.TestCase):
    def body(self, disposition=b'form-data; name="file"; filename="fixture.bin"', extra=b'', data=b'\x00\xff\r\nraw'):
        return (b'--bound\r\nContent-Disposition: '+disposition+b'\r\n'+extra+b'\r\n'+data+b'\r\n--bound--\r\n')

    def test_lone_header_control_is_not_silently_trimmed(self):
        for control in (b'\r', b'\n', b'\x0b', b'\x0c'):
            with self.subTest(control=control):
                value=b'form-data; name="file"; filename="fixture.bin"'+control
                with self.assertRaises(ValueError):
                    _multipart('multipart/form-data; boundary=bound', self.body(value), 1024)

    def test_binary_payload_preserved_without_normalization(self):
        raw=b'\x00\xff\r\n\r\nend\x00'
        name, data = _multipart('multipart/form-data; boundary=bound', self.body(data=raw), 1024)
        self.assertEqual(name,'fixture.bin');self.assertEqual(data,raw)

    def test_duplicate_filename_or_transfer_encoding_rejected(self):
        with self.assertRaises(ValueError):
            _multipart('multipart/form-data; boundary=bound', self.body(b'form-data; name="file"; filename="x"; filename="y"'), 1024)
        with self.assertRaises(ValueError):
            _multipart('multipart/form-data; boundary=bound', self.body(extra=b'Content-Transfer-Encoding: base64\r\n'), 1024)
