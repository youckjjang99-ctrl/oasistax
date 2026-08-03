from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

import pytest

import consultation_audio_storage as audio_storage


def _metadata_response(rows: list[dict[str, object]]) -> Mock:
    response = Mock(ok=True, status_code=200)
    response.json.return_value = rows
    response.text = "rows"
    return response


def test_link_audio_requires_owner_context_without_ui_session() -> None:
    with patch(
        "data_safety_storage._current_session_identity",
        return_value=("", ""),
    ):
        with pytest.raises(PermissionError):
            audio_storage.link_audio_to_journal(
                "audio-1",
                "journal-1",
                "title",
                "summary",
            )


def test_link_audio_patch_is_scoped_to_trusted_owner() -> None:
    response = _metadata_response([{"audio_id": "audio-1"}])
    with patch.object(
        audio_storage,
        "storage_is_configured",
        return_value=True,
    ), patch.object(
        audio_storage.requests,
        "patch",
        return_value=response,
    ) as request_patch:
        linked = audio_storage.link_audio_to_journal(
            "audio-1",
            "journal-1",
            "title",
            "summary",
            owner_user_id="owner-1",
        )

    assert linked is True
    assert request_patch.call_args.kwargs["params"] == {
        "audio_id": "eq.audio-1",
        "owner_user_id": "eq.owner-1",
    }
    assert (
        request_patch.call_args.kwargs["headers"]["Prefer"]
        == "return=representation"
    )


def test_link_audio_returns_false_when_owner_scoped_patch_matches_no_row() -> None:
    response = _metadata_response([])
    with patch.object(
        audio_storage,
        "storage_is_configured",
        return_value=True,
    ), patch.object(
        audio_storage.requests,
        "patch",
        return_value=response,
    ):
        linked = audio_storage.link_audio_to_journal(
            "audio-1",
            "journal-1",
            "title",
            "summary",
            owner_user_id="owner-1",
        )

    assert linked is False


def test_signed_url_requires_owner_scoped_metadata_before_signing() -> None:
    metadata_response = _metadata_response([])
    with patch.object(
        audio_storage,
        "storage_is_configured",
        return_value=True,
    ), patch.object(
        audio_storage.requests,
        "get",
        return_value=metadata_response,
    ) as request_get, patch.object(
        audio_storage.requests,
        "post",
    ) as request_post:
        signed_url = audio_storage.create_signed_audio_url(
            "caller/supplied/path.m4a",
            owner_user_id="owner-1",
            audio_id="audio-1",
        )

    assert signed_url == ""
    assert request_get.call_args.kwargs["params"]["owner_user_id"] == "eq.owner-1"
    assert request_get.call_args.kwargs["params"]["audio_id"] == "eq.audio-1"
    request_post.assert_not_called()


def test_signed_url_rejects_path_different_from_owned_metadata() -> None:
    metadata_response = _metadata_response(
        [
            {
                "audio_id": "audio-1",
                "owner_user_id": "owner-1",
                "storage_bucket": audio_storage.BUCKET_NAME,
                "storage_path": "owner-1/verified/audio.m4a",
                "status": "active",
                "archived_at": None,
            }
        ]
    )
    with patch.object(
        audio_storage,
        "storage_is_configured",
        return_value=True,
    ), patch.object(
        audio_storage.requests,
        "get",
        return_value=metadata_response,
    ), patch.object(audio_storage.requests, "post") as request_post:
        signed_url = audio_storage.create_signed_audio_url(
            "owner-1/untrusted/audio.m4a",
            owner_user_id="owner-1",
            audio_id="audio-1",
        )

    assert signed_url == ""
    request_post.assert_not_called()


def test_signed_url_signs_only_path_loaded_from_owned_metadata() -> None:
    metadata_path = "owner-1/verified/audio file.m4a"
    metadata_response = _metadata_response(
        [
            {
                "audio_id": "audio-1",
                "owner_user_id": "owner-1",
                "storage_bucket": audio_storage.BUCKET_NAME,
                "storage_path": metadata_path,
                "status": "active",
                "archived_at": None,
            }
        ]
    )
    sign_response = Mock(ok=True, status_code=200)
    sign_response.json.return_value = {"signedURL": "/object/sign/safe-token"}
    with patch.object(
        audio_storage,
        "storage_is_configured",
        return_value=True,
    ), patch.object(
        audio_storage.requests,
        "get",
        return_value=metadata_response,
    ), patch.object(
        audio_storage.requests,
        "post",
        return_value=sign_response,
    ) as request_post, patch.object(
        audio_storage,
        "_config",
        return_value=("https://project.example", "placeholder"),
    ):
        signed_url = audio_storage.create_signed_audio_url(
            metadata_path,
            expires_in=99_999,
            owner_user_id="owner-1",
            audio_id="audio-1",
        )

    assert signed_url == "https://project.example/storage/v1/object/sign/safe-token"
    assert "owner-1/verified/audio%20file.m4a" in request_post.call_args.args[0]
    assert request_post.call_args.kwargs["data"] == '{"expiresIn": 3600}'


def test_consultation_journal_passes_owner_to_all_audio_operations() -> None:
    source = Path("consultation_journal.py").read_text(encoding="utf-8")
    assert "link_audio_to_journal(" in source
    assert "owner_user_id=user_id" in source
    assert "audio_id=str(audio_item.get(\"audio_id\", \"\"))" in source
    assert "write_runtime_error(\"consultation_journal.generate\", exc)" in source
    assert "st.code(error_text)" not in source
    assert 'f"{error_message}"' not in source
