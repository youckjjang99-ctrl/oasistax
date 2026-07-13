from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
WORKER_PATH = ROOT_DIR / "cretop_worker.py"


def run_cretop_worker(pdf_path, mode="full", timeout=120):
    """
    PDF 분석을 Streamlit 프로세스와 분리한다.
    worker가 segmentation fault로 종료돼도 앱 본체는 유지된다.
    """
    pdf_path = Path(pdf_path)
    logs = []

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

            for line in (stdout or "").splitlines():
                try:
                    item = json.loads(line)
                    if item.get("type") == "progress":
                        logs.append(item)
                except json.JSONDecodeError:
                    logs.append({"type": "log", "message": line})

            if process.returncode != 0:
                if process.returncode in (-11, 139):
                    return (
                        {},
                        "PDF 분석 모듈이 비정상 종료되었습니다. 앱 본체는 보호되었습니다. "
                        "다른 PDF 엔진으로 재시도할 수 있도록 로그를 남겼습니다.",
                        logs,
                    )

                message = (stderr or "").strip()
                return (
                    {},
                    f"PDF 분석 작업 실패(return code {process.returncode}): {message[-800:]}",
                    logs,
                )

            if not output_path.exists():
                return {}, "PDF 분석 결과 파일이 생성되지 않았습니다.", logs

            data = json.loads(output_path.read_text(encoding="utf-8"))
            return data, "", logs

        except Exception as exc:
            return {}, f"PDF 분석 프로세스 실행 실패: {type(exc).__name__}: {exc}", logs
