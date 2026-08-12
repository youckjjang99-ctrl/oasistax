from pathlib import Path


SOURCE = (
    Path(__file__).resolve().parents[1] / "prospect_db_center.py"
).read_text(encoding="utf-8")
SAVED_SECTION = SOURCE.split("def _render_clean_saved_prospects(", 1)[1].split(
    "def _activity_datetime(", 1
)[0]


def test_contract_registered_db_replaces_download_position():
    contract_index = SAVED_SECTION.index('"계약/등록 DB"')
    table_index = SAVED_SECTION.index("st.dataframe(")
    download_index = SAVED_SECTION.index('"저장된 영업후보 엑셀 다운로드"')
    assert contract_index < table_index < download_index


def test_direct_db_is_available_even_when_assigned_list_is_empty():
    contract_index = SAVED_SECTION.index('"계약/등록 DB"')
    empty_return_index = SAVED_SECTION.index("if not rows:")
    assert contract_index < empty_return_index


def test_popup_contains_requested_columns_and_registration_action():
    for label in (
        "이력관리",
        "업체명",
        "사업자번호",
        "사업자유형",
        "발굴유형",
        "연락처",
        "업종명",
        "고용인원",
        "문자보내기",
        "카카오톡보내기",
        "+ DB 등록",
    ):
        assert label in SOURCE


def test_direct_registration_reuses_customer_crm_and_history():
    registration = SOURCE.split(
        "def _render_direct_db_registration_form(", 1
    )[1].split("@st.dialog(\n    \"계약/등록 DB\"", 1)[0]
    assert "direct_sales_customers.register_direct_customer(" in registration
    assert "upsert_customer_record(" in registration
    assert "sync_crm_record(" in registration
    assert "save_customer_event(" in registration
    assert "sales_assignments" not in registration


def test_message_send_is_consent_and_dnc_guarded():
    resolver = SOURCE.split("def _resolve_direct_outreach_target(", 1)[1].split(
        "def _record_direct_outreach_crm(", 1
    )[0]
    assert 'row.get("marketing_consent_confirmed")' in resolver
    assert "legacy_phone_contact_is_suppressed(" in resolver
    assert "legacy_phone_contact_hash(" in resolver

