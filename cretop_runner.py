from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from cretop_worker import has_company_identity


ROOT_DIR = Path(__file__).resolve().parent
WORKER_PATH = ROOT_DIR / "cretop_worker.py"


def run_cretop_worker(pdf_path, mode="full", timeout=240):
    """
    PDF 분석을 Streamlit 프로세스와 분리한다.
    worker가 segmentation fault로 종료돼도 앱 본체는 유지된다.
    """
    pdf_path = Path(pdf_path)
    logs = []
    error_messages = {
        "identity_not_found": "PDF에서 업체명과 사업자 정보를 확인하지 못했습니다. 기업개요가 포함된 다른 PDF를 업로드해 주세요.",
        "ocr_unavailable": "이미지형 PDF를 읽는 한글 인식 기능을 사용할 수 없습니다. 관리자에게 확인을 요청하거나 텍스트가 포함된 PDF를 업로드해 주세요.",
        "document_unreadable": "PDF를 읽지 못했습니다. 파일이 정상적으로 열리는지 확인한 뒤 다시 업로드해 주세요.",
    }

    with tempfile.TemporaryDirectory(prefix="oasis_cretop_") as temp_dir:
        output_path = Path(temp_dir) / "result.json"
        command = [
            sys.executable,
            str(WORKER_PATH),
            "--pdf",
            str(pdf_path),
            "--output",
            str(output_path),
            "--mode",
            mode,
            "--timeout-seconds",
            str(max(1, timeout - 5)),
        ]

        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(ROOT_DIR),
            )

            try:
                stdout, stderr = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
                return {}, "PDF 분석 시간이 초과되어 작업을 중단했습니다.", logs

            error_code = ""
            for line in (stdout or "").splitlines():
                try:
                    item = json.loads(line)
                    if not isinstance(item, dict):
                        continue
                    if item.get("type") == "progress":
                        logs.append(item)
                    elif item.get("type") == "error":
                        error_code = item.get("code", "")
                except json.JSONDecodeError:
                    # Native PDF/OCR errors may contain document text or file paths.
                    continue

            if process.returncode != 0:
                if process.returncode in (-11, 139):
                    return (
                        {},
                        "PDF 분석 모듈이 비정상 종료되었습니다. 앱 본체는 보호되었습니다. "
                        "다른 PDF 엔진으로 재시도할 수 있도록 로그를 남겼습니다.",
                        logs,
                    )

                return (
                    {},
                    error_messages.get(error_code, "PDF 분석 작업을 완료하지 못했습니다. 파일을 확인한 뒤 다시 시도해 주세요."),
                    logs,
                )

            if not output_path.exists():
                return {}, "PDF 분석 결과 파일이 생성되지 않았습니다.", logs

            data = json.loads(output_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not has_company_identity(data):
                return {}, error_messages["identity_not_found"], logs
            return data, "", logs

        except Exception as exc:
            return {}, f"PDF 분석 프로세스 실행 실패: {type(exc).__name__}", logs
