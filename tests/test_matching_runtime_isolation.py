from __future__ import annotations

from pathlib import Path

import pytest

import utils
from utils import run_matching_engine_isolated


def test_matching_engine_uses_private_runtime_and_preserves_source(tmp_path: Path) -> None:
    repository_dir = tmp_path / "repository"
    repository_dir.mkdir()
    repository_customer = repository_dir / "고객DB.xlsx"
    repository_customer.write_bytes(b"repository-original")

    uploaded_customer = tmp_path / "uploaded.xlsx"
    uploaded_customer.write_bytes(b"private-customer")

    engine = repository_dir / "fake_engine.py"
    engine.write_text(
        "from pathlib import Path\n"
        "assert Path('고객DB.xlsx').read_bytes() == b'private-customer'\n"
        "Path('매칭결과_테스트.xlsx').write_bytes(b'result')\n",
        encoding="utf-8",
    )
    runtime_root = tmp_path / "runtime"
    result_dir = tmp_path / "results"

    process, results = run_matching_engine_isolated(
        uploaded_customer,
        "user-1",
        engine_script=engine,
        runtime_root=runtime_root,
        result_dir=result_dir,
    )

    assert process.returncode == 0
    assert repository_customer.read_bytes() == b"repository-original"
    assert uploaded_customer.read_bytes() == b"private-customer"
    assert len(results) == 1
    assert Path(results[0]).read_bytes() == b"result"
    assert list(runtime_root.iterdir()) == []


def test_failed_matching_run_still_removes_private_runtime(tmp_path: Path) -> None:
    uploaded_customer = tmp_path / "uploaded.xlsx"
    uploaded_customer.write_bytes(b"private-customer")
    engine = tmp_path / "failed_engine.py"
    engine.write_text("raise SystemExit(3)\n", encoding="utf-8")
    runtime_root = tmp_path / "runtime"

    process, results = run_matching_engine_isolated(
        uploaded_customer,
        "user-2",
        engine_script=engine,
        runtime_root=runtime_root,
        result_dir=tmp_path / "results",
    )

    assert process.returncode == 3
    assert results == []
    assert list(runtime_root.iterdir()) == []


def test_isolated_engine_receives_repository_reference_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository_dir = tmp_path / "repository"
    reference_dir = repository_dir / "data"
    reference_dir.mkdir(parents=True)
    (reference_dir / "bizinfo_programs.json").write_text(
        '{"cache":"preserved"}',
        encoding="utf-8",
    )
    uploaded_customer = tmp_path / "uploaded.xlsx"
    uploaded_customer.write_bytes(b"private-customer")
    engine = repository_dir / "fake_engine.py"
    engine.write_text(
        "from pathlib import Path\n"
        "assert Path('data/bizinfo_programs.json').read_text(encoding='utf-8') "
        "== '{\\\"cache\\\":\\\"preserved\\\"}'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(utils, "ROOT_DIR", repository_dir)

    process, results = run_matching_engine_isolated(
        uploaded_customer,
        "user-cache",
        engine_script=engine,
        runtime_root=tmp_path / "runtime",
        result_dir=tmp_path / "results",
    )

    assert process.returncode == 0
    assert results == []


def test_result_copy_failure_preserves_private_runtime_for_recovery(
    tmp_path: Path,
    monkeypatch,
) -> None:
    uploaded_customer = tmp_path / "uploaded.xlsx"
    uploaded_customer.write_bytes(b"private-customer")
    engine = tmp_path / "fake_engine.py"
    engine.write_text(
        "from pathlib import Path\n"
        "Path('\\uB9E4\\uCE6D\\uACB0\\uACFC_recovery.xlsx').write_bytes(b'result')\n",
        encoding="utf-8",
    )
    runtime_root = tmp_path / "runtime"
    original_copy = utils.shutil.copy2

    def fail_result_copy(source, destination, *args, **kwargs):
        source_path = Path(source)
        if source_path.name == "\uB9E4\uCE6D\uACB0\uACFC_recovery.xlsx":
            raise OSError("simulated result destination failure")
        return original_copy(source, destination, *args, **kwargs)

    monkeypatch.setattr(utils.shutil, "copy2", fail_result_copy)

    with pytest.raises(OSError, match="simulated result"):
        run_matching_engine_isolated(
            uploaded_customer,
            "user-recovery",
            engine_script=engine,
            runtime_root=runtime_root,
            result_dir=tmp_path / "results",
        )

    preserved_runs = list(runtime_root.iterdir())
    assert len(preserved_runs) == 1
    assert (
        preserved_runs[0] / "\uB9E4\uCE6D\uACB0\uACFC_recovery.xlsx"
    ).read_bytes() == b"result"


def test_cleanup_archives_old_files_without_deleting_customer_outputs(
    tmp_path: Path,
) -> None:
    files = []
    for index in range(4):
        path = tmp_path / f"result_{index}.txt"
        path.write_text(str(index), encoding="utf-8")
        path.touch()
        files.append(path)

    archived = utils.cleanup_old_files(
        tmp_path,
        "result_*.txt",
        keep_count=2,
    )

    assert len(archived) == 2
    assert len(list(tmp_path.glob("result_*.txt"))) == 2
    assert len(list((tmp_path / ".archive").glob("result_*.txt"))) == 2
    assert sorted(
        path.read_text(encoding="utf-8")
        for path in tmp_path.rglob("result_*.txt")
    ) == ["0", "1", "2", "3"]


def test_result_name_collision_preserves_existing_and_new_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    result_name = "\uB9E4\uCE6D\uACB0\uACFC_same.xlsx"
    result_dir = tmp_path / "results"
    result_dir.mkdir()
    existing = result_dir / result_name
    existing.write_bytes(b"existing-result")
    generated = tmp_path / result_name
    generated.write_bytes(b"new-result")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(utils, "RESULT_DIR", result_dir)

    moved = utils.move_result_files_to_results(set())

    assert len(moved) == 1
    assert existing.read_bytes() == b"existing-result"
    preserved = list(result_dir.glob("\uB9E4\uCE6D\uACB0\uACFC_same_*.xlsx"))
    assert len(preserved) == 1
    assert preserved[0].read_bytes() == b"new-result"
