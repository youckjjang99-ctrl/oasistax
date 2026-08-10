from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _function_source(filename: str, function_name: str) -> str:
    source = (ROOT / filename).read_text(encoding="utf-8")
    start = source.index(f"def {function_name}(")
    next_function = source.find("\ndef ", start + 1)
    return source[start:] if next_function < 0 else source[start:next_function]


def test_articles_review_supports_read_only_mode_and_guides_registration():
    source = _function_source("articles_review.py", "render_articles_review")

    assert "allow_upload: bool = True" in source
    assert "uploaded = None" in source
    assert "if allow_upload:" in source
    assert 'else "기업정보등록에서 정관 등록 시 분석됩니다."' in source


def test_employee_status_supports_read_only_mode_and_guides_registration():
    source = _function_source("employee_status.py", "render_employee_status")

    assert "allow_upload: bool = True" in source
    assert "uploaded_files = []" in source
    assert "if allow_upload:" in source
    assert "if allow_upload else None" in source
    assert (
        'else "기업정보등록에서 4대보험 가입자명부 등록 시 분석됩니다."'
        in source
    )
