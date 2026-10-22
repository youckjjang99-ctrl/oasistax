"""Synthetic credentials only; no provider or production database requests."""
from __future__ import annotations

from unittest.mock import Mock

import pytest

import cloud_db
import consultation_audio_storage as audio_storage
import prospect_db_repository as prospects
from supabase_api_config import (
    SERVER_KEY_NAMES,
    resolve_server_key,
    server_api_headers,
)


OPAQUE_KEY = "sb_secret_" + "synthetic_offline_key"
LEGACY_KEY = "synthetic-legacy-jwt-placeholder"


@pytest.fixture(autouse=True)
def isolated_credentials(monkeypatch):
    for name in (*SERVER_KEY_NAMES, "SUPABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(audio_storage.st, "secrets", {})


@pytest.mark.parametrize("alias", SERVER_KEY_NAMES)
def test_all_legacy_variable_aliases_are_shared_by_database_and_audio(alias, monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://synthetic.example/")
    monkeypatch.setenv(alias, "  " + OPAQUE_KEY + "  ")

    config = cloud_db.get_cloud_config()
    assert (config.url, config.secret_key) == audio_storage._config()
    assert config.secret_key == OPAQUE_KEY
    assert config.configured
    assert audio_storage.storage_is_configured()


def test_primary_alias_wins_even_if_previous_alias_is_still_present(monkeypatch):
    monkeypatch.setenv("SUPABASE_SECRET_KEY", OPAQUE_KEY)
    for alias in SERVER_KEY_NAMES[1:]:
        monkeypatch.setenv(alias, LEGACY_KEY)
    assert cloud_db.get_cloud_config().secret_key == OPAQUE_KEY
    assert audio_storage._config()[1] == OPAQUE_KEY


def test_blank_aliases_are_skipped_without_exposing_values():
    values = {SERVER_KEY_NAMES[0]: "  ", SERVER_KEY_NAMES[2]: LEGACY_KEY}
    assert resolve_server_key(values.get) == LEGACY_KEY
    assert resolve_server_key(lambda _: "") == ""


def test_streamlit_only_credential_uses_same_alias_precedence(monkeypatch):
    monkeypatch.setattr(audio_storage.st, "secrets", {
        "SUPABASE_SECRET_KEY": OPAQUE_KEY,
        "SUPABASE_SERVICE_ROLE_KEY": LEGACY_KEY,
    })
    assert cloud_db.get_cloud_config().secret_key == OPAQUE_KEY
    assert audio_storage._config()[1] == OPAQUE_KEY


@pytest.mark.parametrize("key", [OPAQUE_KEY, "sb_publishable_synthetic_offline"])
def test_opaque_api_key_is_not_sent_as_a_bearer_jwt(key):
    assert server_api_headers(key) == {"apikey": key}


def test_legacy_header_remains_backwards_compatible():
    assert server_api_headers(LEGACY_KEY) == {
        "apikey": LEGACY_KEY,
        "Authorization": "Bearer " + LEGACY_KEY,
    }


@pytest.mark.parametrize("key", [OPAQUE_KEY, LEGACY_KEY])
def test_read_rpc_and_private_storage_use_the_same_rotated_credentials(key, monkeypatch):
    response = Mock(ok=True, text="synthetic", status_code=200)
    response.json.return_value = []
    client = Mock()
    client.get.return_value = response
    client.post.return_value = response
    monkeypatch.setattr(cloud_db, "_http_client", lambda: client)
    database = cloud_db.CloudDatabase(cloud_db.CloudConfig("https://synthetic.example", key))

    assert database.select("synthetic_table", columns="id", limit=1) == []
    assert database.rpc("synthetic_rpc", {"p_limit": 1}) == []
    database.upload_private_object("synthetic-bucket", "owner/object.bin", b"synthetic", "application/octet-stream")

    for call in [*client.get.call_args_list, *client.post.call_args_list]:
        headers = call.kwargs["headers"]
        assert headers["apikey"] == key
        if key == OPAQUE_KEY:
            assert "Authorization" not in headers
        else:
            assert headers["Authorization"] == "Bearer " + key
    assert client.post.call_args_list[-1].kwargs["headers"]["x-upsert"] == "false"


def test_audio_and_discovery_headers_match_new_primary_key(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://synthetic.example")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", OPAQUE_KEY)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", LEGACY_KEY)

    for headers in [
        cloud_db.CloudDatabase().headers,
        audio_storage._headers("audio/mp4"),
        prospects._rest_headers(representation=True),
    ]:
        assert headers["apikey"] == OPAQUE_KEY
        assert "Authorization" not in headers


def test_invalid_replacement_never_falls_back_to_the_previous_key(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://synthetic.example")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", OPAQUE_KEY)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", LEGACY_KEY)
    response = Mock(ok=False, status_code=401, text="Invalid API key")
    client = Mock()
    client.get.return_value = response
    monkeypatch.setattr(cloud_db, "_http_client", lambda: client)

    with pytest.raises(RuntimeError, match="401"):
        cloud_db.CloudDatabase().select("synthetic_table", limit=1)
    client.get.assert_called_once()
    assert client.get.call_args.kwargs["headers"]["apikey"] == OPAQUE_KEY
