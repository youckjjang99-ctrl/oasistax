from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def test_matching_subprocess_output_is_sanitized_before_rendering():
    source = (ROOT / "app.py").read_text(encoding="utf-8")

    assert "st.code(result.stdout)" not in source
    assert "st.code(result.stderr)" not in source
    assert "st.code(sanitize_public_text(result.stdout))" in source
    assert "st.code(sanitize_public_text(result.stderr))" in source


def test_known_customer_save_errors_do_not_render_raw_exception_text():
    source = (ROOT / "app.py").read_text(encoding="utf-8")

    assert 'f"매칭설정 저장 중 오류가 발생했습니다: {exc}"' not in source
    assert 'f"실패했습니다: {preference_error}"' not in source
    assert 'f"{sync_error}"' not in source
    assert "safe_public_error" in source


def test_stage1_touched_ui_modules_do_not_render_raw_exception_messages():
    module_names = (
        "app.py",
        "articles_review.py",
        "consultation_journal.py",
        "consulting_report.py",
        "maintenance.py",
        "prospect_db_center.py",
        "stock_valuation.py",
        "temporary_advance_ui.py",
    )
    raw_ui_error = re.compile(
        r"st\.(?:error|warning|info)\([^\n]*\{(?:exc|[A-Za-z_]+_error|e)\}"
    )

    for module_name in module_names:
        source = (ROOT / module_name).read_text(encoding="utf-8")
        assert not raw_ui_error.search(source), module_name
