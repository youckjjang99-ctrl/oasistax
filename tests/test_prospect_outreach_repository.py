from __future__ import annotations

import hashlib
import hmac
from types import SimpleNamespace
from unittest.mock import Mock, patch

import prospect_db_repository as repository


def _phone_digits() -> str:
    return "".join(("010", "1234", "5678"))


def _domestic_phone() -> str:
    return "-".join(("010", "1234", "5678"))


def _international_phone() -> str:
    return " ".join(("+82", "10", "1234", "5678"))


def _business_uid() -> str:
    return "business:" + "".join(("123", "45", "67890"))


def _response(payload, *, ok: bool = True):
    response = Mock()
    response.ok = ok
    response.text = "payload" if payload is not None else ""
    response.json.return_value = payload
    return response


def test_company_suppression_checks_duplicate_prospect_rows_without_pii():
    company_uid = "source:" + ("a" * 64)
    responses = [
        _response([{"id": "prospect-1"}, {"id": "prospect-2"}]),
        _response(
            [
                {
                    "prospect_id": "prospect-2",
                    "do_not_contact": False,
                    "opt_out_at": "2026-08-04T00:00:00+00:00",
                }
            ]
        ),
    ]
    with patch.object(
        repository,
        "get_cloud_config",
        return_value=SimpleNamespace(
            url="https://example.invalid",
            timeout=5,
        ),
    ), patch.object(
        repository,
        "_rest_headers",
        return_value={"Authorization": "redacted"},
    ), patch.object(
        repository.requests,
        "get",
        side_effect=responses,
    ) as request:
        blocked = repository.company_contact_is_suppressed(
            company_uid,
            prospect_id="prospect-1",
        )

    assert blocked is True
    assert request.call_count == 2
    contact_query = request.call_args_list[1].kwargs["params"]
    assert contact_query["select"] == (
        "prospect_id,do_not_contact,opt_out_at"
    )
    assert "contact_value" not in repr(contact_query)
    assert "prospect-1" in contact_query["prospect_id"]
    assert "prospect-2" in contact_query["prospect_id"]


def test_company_suppression_fails_closed_on_malformed_response():
    with patch.object(
        repository,
        "get_cloud_config",
        return_value=SimpleNamespace(
            url="https://example.invalid",
            timeout=5,
        ),
    ), patch.object(
        repository,
        "_rest_headers",
        return_value={},
    ), patch.object(
        repository.requests,
        "get",
        return_value=_response({"unexpected": True}),
    ):
        try:
            repository.company_contact_is_suppressed(
                "source:" + ("a" * 64),
                prospect_id="prospect-1",
            )
        except RuntimeError:
            pass
        else:
            raise AssertionError("malformed response must fail closed")


def test_legacy_kakao_control_blocks_new_outreach_channels():
    responses = [
        _response([{"id": "prospect-1"}]),
        _response([]),
        _response([{"status": "opted_out"}]),
    ]
    with patch.object(
        repository,
        "get_cloud_config",
        return_value=SimpleNamespace(
            url="https://example.invalid",
            timeout=5,
        ),
    ), patch.object(
        repository,
        "_rest_headers",
        return_value={},
    ), patch.object(
        repository.requests,
        "get",
        side_effect=responses,
    ) as request:
        blocked = repository.company_contact_is_suppressed(
            "source:" + ("a" * 64),
            prospect_id="prospect-1",
        )

    assert blocked is True
    assert request.call_count == 3
    control_query = request.call_args_list[2].kwargs["params"]
    assert control_query["select"] == "status"
    assert control_query["status"] == "in.(opted_out,admin_blocked)"


def test_legacy_phone_hash_blocks_after_company_uid_change_without_pii():
    hash_key = "test-phone-hash-key-with-at-least-32-characters"
    expected_hash = hmac.new(
        hash_key.encode("utf-8"),
        _phone_digits().encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    with patch.dict(
        repository.os.environ,
        {repository.LEGACY_CONTACT_PHONE_HASH_KEY_ENV: hash_key},
        clear=True,
    ), patch.object(
        repository,
        "get_cloud_config",
        return_value=SimpleNamespace(
            url="https://example.invalid",
            timeout=5,
        ),
    ), patch.object(
        repository,
        "_rest_headers",
        return_value={"Authorization": "redacted"},
    ), patch.object(
        repository.requests,
        "get",
        return_value=_response([{"status": "opted_out"}]),
    ) as request:
        blocked = repository.legacy_phone_contact_is_suppressed(
            _business_uid(),
            _international_phone(),
        )

    assert blocked is True
    query = request.call_args.kwargs["params"]
    assert expected_hash in query["or"]
    assert _phone_digits() not in repr(query)
    assert query["status"] == "in.(opted_out,admin_blocked)"


def test_legacy_phone_hash_missing_key_fails_before_network():
    with patch.dict(repository.os.environ, {}, clear=True), patch.object(
        repository.requests,
        "get",
    ) as request:
        try:
            repository.legacy_phone_contact_is_suppressed(
                _business_uid(),
                _domestic_phone(),
            )
        except RuntimeError:
            pass
        else:
            raise AssertionError("missing HMAC key must fail closed")

    request.assert_not_called()
