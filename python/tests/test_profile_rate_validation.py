"""Rate numeric validation only; synthetic JWT, inert stores/coordinators."""
import io
import json
import sys
import unittest
from unittest.mock import Mock

from artex.db.profiles import LLMProfile, ProfileInvalid, ProfileStore
from artex.server.application import build_application
from artex.server.auth import sign_jwt


KEY = b"synthetic-profile-rate-key-only!!"
FIELDS = ("rate_per_second", "rate_per_minute")
HUGE = (10**309, -(10**309), 10**1000, -(10**1000))


class ProfileRateValidationTests(unittest.TestCase):
    def setUp(self):
        self.executor = Mock()
        self.store = ProfileStore(self.executor)
        self.coordinator = Mock()
        self.coordinator.save.return_value = 9
        self.app = build_application(self.executor, KEY, Mock(),
                                     profile_store=self.store,
                                     profile_coordinator=self.coordinator)
        self.token = sign_jwt(KEY)

    def request(self, field, value, *, authorized=True, literal=False):
        if literal:
            body = ('{"name":"Rate fixture","model":"synthetic",'
                    + json.dumps(field) + ':' + value + '}').encode()
        else:
            body = json.dumps({"name": "Rate fixture", "model": "synthetic",
                               field: value}).encode()
        environ = {"REQUEST_METHOD": "POST", "PATH_INFO": "/api/llm/profiles",
                   "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body)}
        if authorized:
            environ["HTTP_AUTHORIZATION"] = "Bearer " + self.token
        result = []
        chunks = self.app(environ, lambda status, headers: result.append((status, headers)))
        try:
            response = b"".join(chunks)
        finally:
            close = getattr(chunks, "close", None)
            if close is not None:
                close()
        status, headers = result[0]
        self.assertEqual(dict(headers)["Content-Length"], str(len(response)))
        return int(status.split()[0]), json.loads(response)

    def test_oversized_integer_is_400_before_coordinator_or_sql(self):
        for field in FIELDS:
            for value in HUGE:
                with self.subTest(field=field, negative=value < 0, digits=len(str(abs(value)))):
                    self.assertEqual(self.request(field, value),
                                     (400, {"error": "Invalid profile request"}))
                    self.assertEqual(self.coordinator.mock_calls, [])
                    self.assertEqual(self.executor.mock_calls, [])

    def test_direct_store_rejects_before_transaction(self):
        for field in FIELDS:
            for value in HUGE:
                with self.subTest(field=field, negative=value < 0, digits=len(str(abs(value)))):
                    profile = LLMProfile(name="Rate fixture", model="synthetic", **{field: value})
                    with self.assertRaises(ProfileInvalid) as raised:
                        self.store.save(profile)
                    self.assertEqual(str(raised.exception), "rate limits must be finite and nonnegative")
                    self.assertEqual(self.executor.mock_calls, [])
                    self.assertEqual(getattr(profile, field), value)

    def test_finite_nonnegative_values_still_reach_coordinator_unchanged(self):
        for field in FIELDS:
            for value in (0, -0.0, 1, 0.5, 10**308, sys.float_info.max):
                with self.subTest(field=field, value=value):
                    self.coordinator.reset_mock()
                    self.assertEqual(self.request(field, value), (200, {"id": 9}))
                    self.coordinator.save.assert_called_once()
                    actual = getattr(self.coordinator.save.call_args.args[0], field)
                    self.assertEqual(actual, value)
                    self.assertIs(type(actual), type(value))
                    self.assertEqual(self.executor.mock_calls, [])

    def test_existing_invalid_types_and_nonfinite_values_still_fail(self):
        for field in FIELDS:
            for value in (True, None, "1", [], {}, -1, -0.5):
                self.assertEqual(self.request(field, value)[0], 400)
            for value in ("NaN", "Infinity", "-Infinity", "1e999", "-1e999"):
                self.assertEqual(self.request(field, value, literal=True)[0], 400)
        self.assertEqual(self.coordinator.mock_calls, [])
        self.assertEqual(self.executor.mock_calls, [])

    def test_unauthorized_huge_values_remain_401_without_effects(self):
        for field in FIELDS:
            self.assertEqual(self.request(field, HUGE[0], authorized=False),
                             (401, {"error": "Unauthorized"}))
        self.assertEqual(self.coordinator.mock_calls, [])
        self.assertEqual(self.executor.mock_calls, [])

    def test_missing_coordinator_keeps_valid_request_unavailable(self):
        self.app = build_application(self.executor, KEY, Mock(), profile_store=self.store)
        for field in FIELDS:
            self.assertEqual(self.request(field, HUGE[0])[0], 400)
            self.assertEqual(self.request(field, 0)[0], 503)
        self.assertEqual(self.executor.mock_calls, [])

    def test_coordinator_overflow_is_still_an_internal_error_not_bad_input(self):
        self.coordinator.save.side_effect = OverflowError("synthetic private adapter detail")
        self.assertEqual(self.request(FIELDS[0], 1),
                         (500, {"error": "Profile operation failed"}))
        self.coordinator.save.assert_called_once()
        self.assertEqual(self.executor.mock_calls, [])


if __name__ == "__main__":
    unittest.main()
