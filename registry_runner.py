from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
WORKER_PATH = ROOT_DIR / "registry_worker.py"


def registry_result_error(data):
    """Reject empty extraction without treating an unreadable PDF as success."""
    if not isinstance(data, dict):
        return "등기자료 분석 결과 형식이 올바르지 않습니다. 파일을 다시 확인해주세요."
    identity = any(str(data.get(key) or "").strip() for key in ("법인명", "법인등록번호"))
    if not identity:
        return (
            "등기자료에서 법인명이나 법인등록번호를 확인하지 못했습니다. "
            "기존 정보는 변경하지 않았습니다. 스캔본은 텍스트를 선택할 수 있는 "
            "원본 PDF로 다시 업로드해주세요."
        )
    return ""


def run_registry_worker(pdf_path, timeout=90):
    pdf_path = Path(pdf_path)

    with tempfile.TemporaryDirectory(prefix="oasis_registry_") as temp_dir:
        output_path = Path(temp_dir) / "registry_result.json"
        command = [
            sys.executable,
            str(WORKER_PATH),
            "--pdf",
            str(pdf_path),
            "--output",
            str(output_path),
        ]

        try:
            process = subprocess.run(
                command,
                cwd=str(ROOT_DIR),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return {}, "등기자료 분석 시간이 초과되었습니다."
        except Exception:
            return {}, "등기자료 분석을 시작하지 못했습니다. 잠시 후 다시 시도해주세요."

        if process.returncode != 0:
            if process.returncode in (-11, 139):
                return {}, "등기자료 분석 모듈이 비정상 종료되었습니다."
            return {}, "등기자료를 읽지 못했습니다. 암호 설정·파일 손상 여부를 확인해주세요."

        if not output_path.exists():
            return {}, "등기자료 분석 결과가 생성되지 않았습니다."

        try:
            data = json.loads(output_path.read_text(encoding="utf-8"))
            error = registry_result_error(data)
            return ({}, error) if error else (data, "")
        except Exception:
            return {}, "등기자료 분석 결과를 읽지 못했습니다. 파일을 다시 업로드해주세요."
