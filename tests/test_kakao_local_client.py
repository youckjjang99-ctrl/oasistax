from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

import requests

import kakao_local_client as client


def _test_phone(*parts: str) -> str:
    return "-".join(parts)


_LANDLINE_A = _test_phone("02", "000", "0000")
_LANDLINE_B = _test_phone("02", "000", "0001")


class _FakeResponse:
    def __init__(
        self,
        status_code: int,
        payload: object | None = None,
        *,
        json_error: ValueError | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error

    @property
    def text(self) -> str:
        raise AssertionError("response body must never be read")

    def json(self) -> object:
        if self._json_error is not None:
            raise self._json_error
        return self._payload


def _payload(*documents: dict[str, object]) -> dict[str, object]:
    count = len(documents)
    return {
        "documents": list(documents),
        "meta": {
            "total_count": count,
            "pageable_count": count,
            "is_end": True,
        },
    }


class KakaoLocalClientTests(unittest.TestCase):
    api_key = "super-secret-kakao-key"

    def _search(self, mocked_get, *, address: str = "서울특별시 중구 세종대로 1"):
        with patch.dict(os.environ, {client.KAKAO_KEY_ENV: self.api_key}):
            with patch.object(client.requests, "get", mocked_get):
                return client.search_company("테스트상사", address)

    def _connection(self, mocked_get):
        with patch.dict(os.environ, {client.KAKAO_KEY_ENV: self.api_key}):
            with patch.object(client.requests, "get", mocked_get):
                return client.test_connection()

    def assert_secret_free(self, result: dict[str, object]) -> None:
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn(self.api_key, serialized)
        self.assertNotIn("secret-response-body", serialized)
        self.assertNotIn("secret-exception-detail", serialized)

    def test_key_status_never_returns_key_prefix(self):
        with patch.dict(os.environ, {client.KAKAO_KEY_ENV: self.api_key}):
            result = client.key_status()

        self.assertTrue(result["configured"])
        self.assertEqual(result["masked"], "설정됨")
        self.assertNotIn(self.api_key[:4], json.dumps(result, ensure_ascii=False))

    def test_search_missing_key_is_provider_error_without_request(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(client.requests, "get") as mocked_get:
                result = client.search_company("테스트상사", "서울특별시")

        self.assertFalse(result["ok"])
        self.assertEqual(result["outcome"], "error")
        self.assertEqual(result["status"], "KEY_MISSING")
        self.assertEqual(result["safe_error_code"], "KEY_MISSING")
        self.assertEqual(result["request_count"], 0)
        self.assertEqual(result["candidates"], [])
        mocked_get.assert_not_called()

    def test_search_all_http_failures_are_provider_errors(self):
        for status_code in (400, 401, 403, 429, 500, 502, 503, 418, 599):
            with self.subTest(status_code=status_code):
                response = _FakeResponse(status_code, _payload())
                result = self._search(return_value(response))

                self.assertFalse(result["ok"])
                self.assertEqual(result["outcome"], "error")
                self.assertEqual(result["status"], f"HTTP_{status_code}")
                self.assertEqual(result["safe_error_code"], f"HTTP_{status_code}")
                self.assertEqual(result["request_count"], 1)
                self.assertEqual(result["candidates"], [])
                self.assert_secret_free(result)

    def test_search_timeout_and_network_error_are_not_no_match(self):
        errors = (
            (requests.Timeout("secret-exception-detail"), "TIMEOUT"),
            (requests.ConnectionError("secret-exception-detail"), "NETWORK_ERROR"),
        )
        for raised, safe_code in errors:
            with self.subTest(safe_code=safe_code):
                result = self._search(side_effect(raised))

                self.assertFalse(result["ok"])
                self.assertEqual(result["outcome"], "error")
                self.assertEqual(result["safe_error_code"], safe_code)
                self.assertEqual(result["request_count"], 1)
                self.assert_secret_free(result)

    def test_search_invalid_json_is_provider_error(self):
        response = _FakeResponse(
            200,
            json_error=ValueError("secret-response-body"),
        )
        result = self._search(return_value(response))

        self.assertFalse(result["ok"])
        self.assertEqual(result["outcome"], "error")
        self.assertEqual(result["safe_error_code"], "INVALID_JSON")
        self.assertEqual(result["request_count"], 1)
        self.assert_secret_free(result)

    def test_search_malformed_json_shape_is_provider_error(self):
        for malformed in (
            [],
            {},
            {"documents": None},
            {"documents": {}},
            {"documents": [None]},
            {"documents": ["unexpected"]},
            {"documents": [], "meta": None},
            {"documents": [], "meta": {}},
            {
                "documents": [],
                "meta": {"total_count": 0, "pageable_count": 0},
            },
            {
                "documents": [],
                "meta": {
                    "total_count": "0",
                    "pageable_count": 0,
                    "is_end": True,
                },
            },
            {
                "documents": [],
                "meta": {
                    "total_count": 0,
                    "pageable_count": -1,
                    "is_end": True,
                },
            },
            {
                "documents": [],
                "meta": {
                    "total_count": 0,
                    "pageable_count": 0,
                    "is_end": 1,
                },
            },
        ):
            with self.subTest(payload=malformed):
                result = self._search(return_value(_FakeResponse(200, malformed)))

                self.assertFalse(result["ok"])
                self.assertEqual(result["outcome"], "error")
                self.assertEqual(result["safe_error_code"], "INVALID_JSON")
                self.assertEqual(result["request_count"], 1)

    def test_search_first_request_matched_and_contains_no_raw_document(self):
        document = {
            "place_name": "테스트상사",
            "road_address_name": "서울특별시 중구 세종대로 1",
            "phone": _LANDLINE_A,
            "place_url": "https://place.example/one",
            "secret_response_body": "must-not-be-copied",
        }
        get = return_value(_FakeResponse(200, _payload(document)))
        result = self._search(get)

        self.assertTrue(result["ok"])
        self.assertEqual(result["outcome"], "matched")
        self.assertEqual(result["status"], "MATCHED")
        self.assertEqual(result["request_count"], 1)
        self.assertEqual(get.call_count, 1)
        self.assertNotIn("raw", result["candidates"][0])
        self.assertNotIn("must-not-be-copied", json.dumps(result, ensure_ascii=False))
        self.assert_secret_free(result)

    def test_search_normal_empty_responses_are_no_match_after_two_requests(self):
        get = side_effect(
            _FakeResponse(200, _payload()),
            _FakeResponse(200, _payload()),
        )
        result = self._search(get)

        self.assertTrue(result["ok"])
        self.assertEqual(result["outcome"], "no_match")
        self.assertEqual(result["status"], "NO_MATCH")
        self.assertEqual(result["request_count"], 2)
        self.assertEqual(get.call_count, 2)
        self.assertEqual(result["candidates"], [])
        self.assertNotIn("query", result)
        self.assertNotIn("fallback_query", result)

    def test_search_fallback_can_match_on_second_request(self):
        fallback_document = {
            "place_name": "테스트상사",
            "address_name": "서울특별시 중구 세종대로 1",
            "phone": _LANDLINE_A,
            "place_url": "https://place.example/two",
        }
        get = side_effect(
            _FakeResponse(200, _payload()),
            _FakeResponse(200, _payload(fallback_document)),
        )
        result = self._search(get)

        self.assertTrue(result["ok"])
        self.assertEqual(result["outcome"], "matched")
        self.assertEqual(result["request_count"], 2)
        self.assertEqual(get.call_count, 2)

    def test_untrusted_first_phone_does_not_suppress_fallback(self):
        unrelated_document = {
            "place_name": "unrelated business",
            "address_name": "different address",
            "phone": _LANDLINE_A,
            "place_url": "https://place.example/unrelated",
        }
        trusted_document = {
            "place_name": "테스트상사",
            "address_name": "서울특별시 중구 세종대로 1",
            "phone": _LANDLINE_B,
            "place_url": "https://place.example/trusted",
        }
        get = side_effect(
            _FakeResponse(200, _payload(unrelated_document)),
            _FakeResponse(200, _payload(trusted_document)),
        )

        result = self._search(get)

        self.assertTrue(result["ok"])
        self.assertEqual(result["outcome"], "matched")
        self.assertEqual(result["request_count"], 2)
        self.assertEqual(get.call_count, 2)

    def test_search_fallback_http_error_is_propagated(self):
        get = side_effect(
            _FakeResponse(200, _payload()),
            _FakeResponse(500, _payload()),
        )
        result = self._search(get)

        self.assertFalse(result["ok"])
        self.assertEqual(result["outcome"], "error")
        self.assertEqual(result["safe_error_code"], "HTTP_500")
        self.assertEqual(result["request_count"], 2)
        self.assertEqual(get.call_count, 2)
        self.assert_secret_free(result)

    def test_search_fallback_timeout_is_propagated(self):
        get = side_effect(
            _FakeResponse(200, _payload()),
            requests.Timeout("secret-exception-detail"),
        )
        result = self._search(get)

        self.assertFalse(result["ok"])
        self.assertEqual(result["safe_error_code"], "TIMEOUT")
        self.assertEqual(result["request_count"], 2)
        self.assertEqual(get.call_count, 2)
        self.assert_secret_free(result)

    def test_search_fallback_invalid_json_is_propagated(self):
        get = side_effect(
            _FakeResponse(200, _payload()),
            _FakeResponse(200, json_error=ValueError("secret-response-body")),
        )
        result = self._search(get)

        self.assertFalse(result["ok"])
        self.assertEqual(result["safe_error_code"], "INVALID_JSON")
        self.assertEqual(result["request_count"], 2)
        self.assertEqual(get.call_count, 2)
        self.assert_secret_free(result)

    def test_search_fallback_malformed_document_is_propagated(self):
        get = side_effect(
            _FakeResponse(200, _payload()),
            _FakeResponse(200, {"documents": [None]}),
        )

        result = self._search(get)

        self.assertFalse(result["ok"])
        self.assertEqual(result["safe_error_code"], "INVALID_JSON")
        self.assertEqual(result["request_count"], 2)
        self.assertEqual(get.call_count, 2)

    def test_connection_http_categories_are_allowlisted(self):
        expected = {
            200: ("CONNECTED", ""),
            400: ("NETWORK_ERROR", "HTTP_400"),
            401: ("AUTH_ERROR", "HTTP_401"),
            403: ("PERMISSION_ERROR", "HTTP_403"),
            429: ("QUOTA_ERROR", "HTTP_429"),
            500: ("NETWORK_ERROR", "HTTP_500"),
            502: ("NETWORK_ERROR", "HTTP_502"),
            503: ("NETWORK_ERROR", "HTTP_503"),
        }
        for status_code, (category, safe_code) in expected.items():
            with self.subTest(status_code=status_code):
                result = self._connection(
                    return_value(_FakeResponse(status_code, _payload()))
                )

                self.assertEqual(result["status"], category)
                self.assertEqual(result["category"], category)
                self.assertEqual(result["safe_error_code"], safe_code)
                self.assertEqual(result["request_count"], 1)
                self.assertEqual(result["ok"], status_code == 200)
                self.assert_secret_free(result)

    def test_connection_missing_key_is_auth_error_without_request(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(client.requests, "get") as get:
                result = client.test_connection()

        self.assertFalse(result["ok"])
        self.assertEqual(result["category"], "AUTH_ERROR")
        self.assertEqual(result["safe_error_code"], "KEY_MISSING")
        self.assertEqual(result["request_count"], 0)
        get.assert_not_called()

    def test_connection_transport_and_invalid_json_are_network_error(self):
        cases = (
            (side_effect(requests.Timeout("secret-exception-detail")), "TIMEOUT"),
            (
                side_effect(requests.ConnectionError("secret-exception-detail")),
                "NETWORK_ERROR",
            ),
            (
                return_value(
                    _FakeResponse(
                        200,
                        json_error=ValueError("secret-response-body"),
                    )
                ),
                "INVALID_JSON",
            ),
            (
                return_value(
                    _FakeResponse(200, {"documents": [None]})
                ),
                "INVALID_JSON",
            ),
            (
                return_value(_FakeResponse(200, {"documents": []})),
                "INVALID_JSON",
            ),
        )
        for get, safe_code in cases:
            with self.subTest(safe_code=safe_code):
                result = self._connection(get)

                self.assertFalse(result["ok"])
                self.assertEqual(result["category"], "NETWORK_ERROR")
                self.assertEqual(result["safe_error_code"], safe_code)
                self.assertEqual(result["request_count"], 1)
                self.assert_secret_free(result)


def return_value(value):
    from unittest.mock import Mock

    return Mock(return_value=value)


def side_effect(*values):
    from unittest.mock import Mock

    if len(values) == 1 and isinstance(values[0], BaseException):
        return Mock(side_effect=values[0])
    return Mock(side_effect=list(values))


if __name__ == "__main__":
    unittest.main()
