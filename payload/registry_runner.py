from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
WORKER_PATH = ROOT_DIR / "registry_worker.py"


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
        except Exception as exc:
            return {}, f"등기자료 분석 실행 실패: {type(exc).__name__}: {exc}"

        if process.returncode != 0:
            if process.returncode in (-11, 139):
                return {}, "등기자료 분석 모듈이 비정상 종료되었습니다."
            return {}, (
                f"등기자료 분석 실패(return code {process.returncode}): "
                f"{(process.stderr or process.stdout)[-800:]}"
            )

        if not output_path.exists():
            return {}, "등기자료 분석 결과가 생성되지 않았습니다."

        try:
            return json.loads(output_path.read_text(encoding="utf-8")), ""
        except Exception as exc:
            return {}, f"등기자료 결과 읽기 실패: {type(exc).__name__}: {exc}"
