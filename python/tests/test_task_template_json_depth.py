"""Template decoder recursion is bad input, not a store failure."""
import io
import json
import sys
import unittest
from unittest.mock import Mock

from artex.server.application import build_application
from artex.server.auth import sign_jwt
from artex.server.task_templates import MAX_BODY


KEY = b"synthetic-template-depth-key-only"
DATA = {"name": "Depth fixture", "description": "Synthetic", "goal": "No task work"}


class TemplateJSONDepthTests(unittest.TestCase):
    def setUp(self):
        self.executor = Mock()
        self.store = Mock()
        self.app = build_application(self.executor, KEY, Mock(), template_store=self.store)
        self.token = sign_jwt(KEY)

    def request(self, method, body, *, authorized=True):
        path = "/api/task-templates" + ("/1" if method == "PATCH" else "")
        environ = {"REQUEST_METHOD": method, "PATH_INFO": path,
                   "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body)}
        if authorized:
            environ["HTTP_AUTHORIZATION"] = "Bearer " + self.token
        result = []
        chunks = self.app(environ, lambda status, headers: result.append((status, headers)))
        try:
            payload = b"".join(chunks)
        finally:
            close = getattr(chunks, "close", None)
            if close is not None:
                close()
        status, headers = result[0]
        self.assertEqual(dict(headers)["Content-Length"], str(len(payload)))
        return int(status.split()[0]), json.loads(payload)

    def deep_bodies(self):
        # Exercise the actual decoder without changing this process's recursion
        # limit. The normal Python limit is well below this bounded fixture.
        if sys.getrecursionlimit() >= 12000:
            self.skipTest("Decoder recursion fixture needs the standard recursion limit")
        # CPython's C JSON decoder may have a separate, higher depth limit.
        array = b"[" * 12000 + b"0" + b"]" * 12000
        obj = b'{"nested":' * 12000 + b"0" + b"}" * 12000
        return (array, b'{"name":' + array + b"}", obj)

    def test_actual_deep_decoder_failure_is_400_without_store_calls(self):
        for body in self.deep_bodies():
            self.assertLess(len(body), MAX_BODY)
            with self.assertRaises(RecursionError):
                json.loads(body.decode("utf-8"))
            for method in ("POST", "PATCH"):
                with self.subTest(method=method, bytes=len(body)):
                    self.assertEqual(self.request(method, body),
                                     (400, {"error": "Invalid task template request"}))
                    self.assertEqual(self.store.mock_calls, [])
                    self.assertEqual(self.executor.mock_calls, [])

    def test_malformed_deep_document_is_also_redacted(self):
        body = self.deep_bodies()[0][:-1]
        for method in ("POST", "PATCH"):
            self.assertEqual(self.request(method, body),
                             (400, {"error": "Invalid task template request"}))
        self.assertEqual(self.store.mock_calls, [])

    def test_wire_limit_still_precedes_decoder(self):
        body = self.deep_bodies()[0]
        body += b" " * (MAX_BODY + 1 - len(body))
        for method in ("POST", "PATCH"):
            self.assertEqual(self.request(method, body),
                             (413, {"error": "Request body too large"}))
        self.assertEqual(self.store.mock_calls, [])
        self.assertEqual(self.executor.mock_calls, [])

    def test_authorization_still_precedes_decoder(self):
        body = self.deep_bodies()[0]
        for method in ("POST", "PATCH"):
            self.assertEqual(self.request(method, body, authorized=False),
                             (401, {"error": "Unauthorized"}))
        self.assertEqual(self.store.mock_calls, [])
        self.assertEqual(self.executor.mock_calls, [])

    def test_valid_payload_and_receipt_are_unchanged(self):
        for method, operation, status in (("POST", "create", 201), ("PATCH", "patch", 200)):
            self.store.reset_mock()
            getattr(self.store, operation).return_value.to_dict.return_value = {"id": 1, **DATA}
            self.assertEqual(self.request(method, json.dumps(DATA).encode()),
                             (status, {"id": 1, **DATA}))
            expected = (DATA,) if method == "POST" else (1, DATA)
            getattr(self.store, operation).assert_called_once_with(*expected)
        self.assertEqual(self.executor.mock_calls, [])

    def test_store_recursion_error_remains_internal_error(self):
        for method, operation in (("POST", "create"), ("PATCH", "patch")):
            self.store.reset_mock()
            getattr(self.store, operation).side_effect = RecursionError("synthetic private store detail")
            self.assertEqual(self.request(method, json.dumps(DATA).encode()),
                             (500, {"error": "Task template operation failed"}))
            getattr(self.store, operation).assert_called_once()
        self.assertEqual(self.executor.mock_calls, [])


if __name__ == "__main__":
    unittest.main()
