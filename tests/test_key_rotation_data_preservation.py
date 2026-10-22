"""API-key rotation must not change encryption or historical opt-out lookup."""
from unittest.mock import Mock

from claim_sales_application import ClaimSalesApplicationCipher
import prospect_db_repository as prospects


def test_database_and_login_key_rotation_preserves_encrypted_payload(monkeypatch):
    monkeypatch.delenv("SALES_APPLICATION_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("CLAIM_JOB_ENCRYPTION_KEY", "synthetic-stable-encryption-key-for-offline-tests")
    payload = {"record_id": "synthetic-record", "consent_version": "synthetic-version"}
    original_cipher = ClaimSalesApplicationCipher.from_environment()
    encrypted = original_cipher.encrypt(payload)

    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_" + "synthetic_replacement")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "synthetic-previous-key")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-replacement-ai-key")
    monkeypatch.setenv("APP_SESSION_SECRET", "synthetic-replacement-session-key-for-offline-tests")

    assert ClaimSalesApplicationCipher.from_environment().decrypt(encrypted) == payload


def test_database_key_rotation_preserves_historical_phone_opt_out(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://synthetic.example")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "synthetic-previous-key")
    monkeypatch.setenv(prospects.LEGACY_CONTACT_PHONE_HASH_KEY_ENV, "synthetic-stable-opt-out-key-for-offline-tests")
    phone = "010" + "0000" + "0000"
    previous_hash = prospects.legacy_phone_contact_hash(phone)
    response = Mock(ok=True, text="synthetic")
    response.json.return_value = [{"status": "opted_out"}]
    request = Mock(return_value=response)
    monkeypatch.setattr(prospects.requests, "get", request)

    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_" + "synthetic_replacement")
    assert prospects.legacy_phone_contact_hash(phone) == previous_hash
    assert prospects.legacy_phone_contact_is_suppressed("synthetic-company", phone)
    assert previous_hash in request.call_args.kwargs["params"]["or"]
    assert phone not in repr(request.call_args)
    assert "Authorization" not in request.call_args.kwargs["headers"]
