from pathlib import Path


SOURCE = (
    Path(__file__).resolve().parents[1] / "prospect_db_center.py"
).read_text(encoding="utf-8")
SAVED_SECTION = SOURCE.split("def _render_clean_saved_prospects(", 1)[1].split(
    "def _activity_datetime(", 1
)[0]


def test_registered_and_contracted_db_are_rendered_in_dashboard():
    dashboard_index = SAVED_SECTION.index("_render_saved_db_dashboard(")
    table_index = SAVED_SECTION.index("st.dataframe(")
    download_index = SAVED_SECTION.index('"저장된 영업후보 엑셀 다운로드"')
    assert dashboard_index < table_index < download_index
    assert "get_direct_customer_summary(" in SAVED_SECTION


def test_combined_contract_registered_db_button_is_removed():
    assert "_direct_db_button_label" not in SOURCE
    assert 'key="open_direct_db_dialog_v1230"' not in SOURCE


def test_direct_db_is_available_even_when_assigned_list_is_empty():
    contract_index = SAVED_SECTION.index("_render_saved_db_dashboard(")
    empty_return_index = SAVED_SECTION.index("if not rows:")
    assert contract_index < empty_return_index


def test_dashboard_cards_open_the_requested_direct_db_category():
    renderer = SOURCE.split("def _render_saved_db_dashboard(", 1)[1].split(
        "def _load_user_assignment_rows(", 1
    )[0]
    opener = SOURCE.split("def _open_direct_db_dialog(", 1)[1].split(
        "def _dismiss_direct_db_dialog(", 1
    )[0]

    assert '("registered", "등록 DB", "registered", "등록 DB")' in renderer
    assert '("contracted", "계약 DB", "contracted", "계약 DB")' in renderer
    assert "on_click=_open_direct_db_dialog" in renderer
    assert "args=(direct_category,)" in renderer
    assert "_DIRECT_DB_FILTER_KEY" in opener
    assert '{"전체", "등록 DB", "계약 DB"}' in opener


def test_registered_and_contracted_cards_open_separate_dialogs():
    assert '@st.dialog(\n    "등록 DB"' in SOURCE
    assert '@st.dialog(\n    "계약 DB"' in SOURCE
    assert "def _show_registered_db_dialog(" in SOURCE
    assert "def _show_contracted_db_dialog(" in SOURCE
    assert 'category="registered"' in SOURCE
    assert 'category="contracted"' in SOURCE
    assert "st.segmented_control(" not in SOURCE.split(
        "def _render_direct_db_dialog_content(", 1
    )[1].split("def _dismiss_direct_activity_dialog(", 1)[0]
    assert '== "계약 DB"' in SAVED_SECTION


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
        "사업장 주소",
    ):
        assert label in SOURCE


def test_direct_registration_reuses_customer_crm_and_history():
    registration = SOURCE.split(
        "def _render_direct_db_registration_form(", 1
    )[1].split("def _render_direct_db_dialog_content(", 1)[0]
    assert "direct_sales_customers.register_direct_customer(" in registration
    assert '"address": address' in registration
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
