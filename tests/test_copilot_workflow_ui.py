import inspect
import sys
from unittest.mock import Mock

sys.modules.setdefault("cv2", Mock())
sys.modules.setdefault("pytesseract", Mock())
sys.modules.setdefault("fitz", Mock())

import consulting_copilot as copilot


def test_copilot_uses_three_conditionally_rendered_workflow_stages():
    source = inspect.getsource(copilot.render_copilot_page)

    assert "st.segmented_control(" in source
    assert '"① 상담 준비"' in source
    assert '"② 상담 진행"' in source
    assert '"③ 상담 마무리"' in source
    assert "st.tabs(" not in source
    assert "tab_report" not in source
    assert "tab_tax" not in source


def test_detailed_report_is_opt_in_and_duplicate_tax_page_is_removed():
    source = inspect.getsource(copilot.render_copilot_page)

    assert '"상세 AI 상담보고서 보기"' in source
    assert "if show_detailed_report:" in source
    assert "render_ai_consulting_report_page(" in source
    assert "render_tax_diagnosis_page(" not in source


def test_finish_stage_reuses_consultation_journal_and_crm_integration():
    source = inspect.getsource(copilot.render_copilot_page)

    assert '"상담일지에 저장하고 마무리"' in source
    assert "save_consultation_journal(" in source
    assert "save_company_memory(" in source
    assert "get_customer_record(" in source
    assert "make_customer_key(" in source


def test_success_case_writer_is_admin_only():
    source = inspect.getsource(copilot._render_success_case_registration)

    assert "if not is_admin(user_id):" in source
    assert '"관리자용 성공사례 등록"' in source


def test_multiline_finish_inputs_are_normalized_and_deduplicated():
    assert copilot._split_list_input("자료 요청\n재연락, 자료 요청") == [
        "자료 요청",
        "재연락",
    ]
