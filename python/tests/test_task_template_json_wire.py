"""Existing template routes through real WSGI/JWT; SQL executor is inert."""
import io
import json
import unittest
from datetime import datetime, timezone

from artex.db.task_templates import TaskTemplateStore
from artex.server.application import build_application, build_persisted_application
from artex.server.auth import sign_jwt
from artex.server.task_templates import MAX_BODY


KEY = b"synthetic-template-test-key-only!!"
NOW = datetime(2026, 11, 17, tzinfo=timezone.utc)
DATA = {"name": "測試 🧪", "description": "Résumé", "goal": "確認"}
ROW = (1, DATA["name"], DATA["name"].lower(), DATA["description"],
       DATA["goal"], None, "[]", NOW, NOW)


class Executor:
    """Captures bound SQL, never opens a database or runs task work."""

    def __init__(self):
        self.calls = []

    def fetch_all(self, query):
        self.calls.append(query)
        if "FROM tasks WHERE deleted_at IS NULL" in query.sql:
            return []
        if (query.sql.startswith("INSERT INTO task_templates")
                or query.sql.startswith("UPDATE task_templates")):
            return [ROW]
        raise AssertionError(query.sql)

    def execute(self, query):
        raise AssertionError(query.sql)


class UnusedCodec:
    def hash(self, _password):
        raise AssertionError("No password operations in this wire fixture")

    def verify(self, _password, _encoded):
        raise AssertionError("No password operations in this wire fixture")


class TemplateJSONWireTests(unittest.TestCase):
    def setUp(self):
        self.db = Executor()
        self.app = build_persisted_application(self.db, KEY, UnusedCodec())
        self.assertEqual(len(self.db.calls), 1)  # Existing startup task read only.
        self.db.calls.clear()
        self.token = sign_jwt(KEY)

    def request(self, method, body, *, authorized=True, content_type="application/json"):
        path = "/api/task-templates" + ("/1" if method == "PATCH" else "")
        environ = {
            "REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": "",
            "CONTENT_LENGTH": str(len(body)), "CONTENT_TYPE": content_type,
            "wsgi.input": io.BytesIO(body),
        }
        if authorized:
            environ["HTTP_AUTHORIZATION"] = "Bearer " + self.token
        response = []
        chunks = self.app(environ, lambda status, headers: response.append((status, headers)))
        try:
            payload = b"".join(chunks)
        finally:
            close = getattr(chunks, "close", None)
            if close is not None:
                close()
        status, headers = response[0]
        self.assertEqual(dict(headers)["Content-Length"], str(len(payload)))
        return int(status.split()[0]), json.loads(payload)

    def test_utf16_utf32_and_boms_are_rejected_without_sql(self):
        text = json.dumps(DATA, ensure_ascii=False)
        for method in ("POST", "PATCH"):
            for encoding in ("utf-16", "utf-16-le", "utf-16-be", "utf-32",
                             "utf-32-le", "utf-32-be", "utf-8-sig"):
                with self.subTest(method=method, encoding=encoding):
                    self.assertEqual(self.request(method, text.encode(encoding)),
                                     (400, {"error": "Invalid task template request"}))
                    self.assertEqual(self.db.calls, [])

    def test_injected_factory_uses_same_wire_validation(self):
        self.app = build_application(self.db, KEY, UnusedCodec(),
                                     template_store=TaskTemplateStore(self.db))
        for method in ("POST", "PATCH"):
            self.assertEqual(self.request(method, json.dumps(DATA).encode("utf-16"))[0], 400)
        self.assertEqual(self.db.calls, [])

    def test_valid_utf8_and_json_escapes_preserve_bound_unicode(self):
        for method in ("POST", "PATCH"):
            for escaped in (False, True):
                with self.subTest(method=method, escaped=escaped):
                    self.db.calls.clear()
                    status, payload = self.request(method, json.dumps(DATA, ensure_ascii=escaped).encode("utf-8"))
                    self.assertEqual(status, 201 if method == "POST" else 200)
                    self.assertEqual(payload["name"], DATA["name"])
                    self.assertEqual(len(self.db.calls), 1)
                    params = self.db.calls[0].params
                    actual = (params[0], params[2], params[3]) if method == "POST" else (params[2], params[5], params[7])
                    self.assertEqual(actual, tuple(DATA.values()))
                    self.assertNotIn(DATA["name"], self.db.calls[0].sql)

    def test_unauthorized_alternate_encoding_never_reaches_store(self):
        for method in ("POST", "PATCH"):
            for encoding in ("utf-8", "utf-16", "utf-32"):
                self.assertEqual(self.request(method, json.dumps(DATA).encode(encoding), authorized=False)[0], 401)
        self.assertEqual(self.db.calls, [])

    def test_malformed_utf8_and_surrogate_escapes_are_redacted(self):
        for method in ("POST", "PATCH"):
            for body in (b'{"name":"private-\xff"}', b'{"name":"\xc0\xaf"}',
                         b'{"name":"\xed\xa0\x80"}', b'{"name":"\\ud800"}',
                         b'{"name":"truncated-\xe2\x82"}'):
                self.assertEqual(self.request(method, body),
                                 (400, {"error": "Invalid task template request"}))
        self.assertEqual(self.db.calls, [])

    def test_charset_header_does_not_enable_transcoding(self):
        for method in ("POST", "PATCH"):
            self.assertEqual(self.request(method, json.dumps(DATA).encode("utf-16"),
                                          content_type="application/json; charset=utf-16")[0], 400)
        self.assertEqual(self.db.calls, [])

    def test_limit_counts_wire_bytes_before_decoding(self):
        for method in ("POST", "PATCH"):
            self.db.calls.clear()
            encoded = json.dumps(DATA, ensure_ascii=False).encode("utf-8")
            exact = encoded + b" " * (MAX_BODY - len(encoded))
            self.assertEqual(len(exact), MAX_BODY)
            self.assertEqual(self.request(method, exact)[0], 201 if method == "POST" else 200)
            self.db.calls.clear()
            self.assertEqual(self.request(method, exact + b" "),
                             (413, {"error": "Request body too large"}))
            self.assertEqual(self.db.calls, [])

    def test_embedded_bom_is_not_stripped_from_a_valid_string(self):
        for method in ("POST", "PATCH"):
            data = dict(DATA, name="left\ufeffright")
            self.assertEqual(self.request(method, json.dumps(data, ensure_ascii=False).encode())[0],
                             201 if method == "POST" else 200)
            params = self.db.calls[-1].params
            self.assertEqual(params[0 if method == "POST" else 2], data["name"])


if __name__ == "__main__":
    unittest.main()
