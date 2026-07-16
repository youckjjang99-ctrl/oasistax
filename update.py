from __future__ import annotations

import importlib.util
import py_compile
import shutil
import sys
from datetime import datetime
from pathlib import Path

TARGET_VERSION = "v8.2.0"
SUPPORTED_VERSIONS = {
    "v7.4.5", "7.4.5",
    "v8.0.0", "8.0.0",
    "v8.1.0", "8.1.0",
    "v8.2.0", "8.2.0",
}
TARGET_FILES = [
    "app.py",
    "enterprise_center.py",
    "consulting_copilot.py",
    "consultation_journal.py",
    "VERSION.txt",
]

SPLIT_AUDIO_CODE = 'def _split_audio_if_needed(\n    path: Path,\n    output_dir: Path,\n) -> list[Path]:\n    """Split long audio by duration as well as file size."""\n    if not _ffmpeg_available():\n        if path.stat().st_size <= MAX_API_FILE_BYTES:\n            return [path]\n        raise RuntimeError(\n            "장시간 또는 24MB 초과 녹음을 처리하려면 ffmpeg가 필요합니다."\n        )\n\n    duration = _audio_duration_seconds(path)\n    needs_time_split = duration > LONG_AUDIO_SEGMENT_SECONDS\n    needs_size_split = path.stat().st_size > MAX_API_FILE_BYTES\n\n    if not needs_time_split and not needs_size_split:\n        return [path]\n\n    segment_seconds = LONG_AUDIO_SEGMENT_SECONDS\n    if needs_size_split:\n        target_ratio = MAX_API_FILE_BYTES / max(path.stat().st_size, 1)\n        size_based_seconds = max(120, int(duration * target_ratio * 0.80))\n        segment_seconds = min(segment_seconds, size_based_seconds)\n\n    output_pattern = output_dir / "audio_part_%03d.m4a"\n    result = subprocess.run(\n        [\n            "ffmpeg", "-y", "-i", str(path), "-vn",\n            "-ac", "1", "-ar", "16000",\n            "-c:a", "aac", "-b:a", "48k",\n            "-f", "segment",\n            "-segment_time", str(segment_seconds),\n            "-reset_timestamps", "1",\n            str(output_pattern),\n        ],\n        capture_output=True,\n        text=True,\n        timeout=1200,\n    )\n    if result.returncode != 0:\n        raise RuntimeError(\n            "녹음파일 분할에 실패했습니다: "\n            + (result.stderr[-1000:] if result.stderr else "ffmpeg 오류")\n        )\n\n    chunks = sorted(output_dir.glob("audio_part_*.m4a"))\n    if not chunks:\n        raise RuntimeError("분할된 녹음파일을 찾지 못했습니다.")\n\n    oversized = [\n        chunk\n        for chunk in chunks\n        if chunk.stat().st_size > MAX_API_FILE_BYTES\n    ]\n    if oversized:\n        raise RuntimeError("분할된 일부 파일이 24MB를 초과했습니다.")\n\n    return chunks\n'
TRANSCRIBE_CHUNK_CODE = 'def _transcribe_chunk(\n    api_key: str,\n    path: Path,\n    company_name: str,\n    chunk_index: int,\n    total_chunks: int,\n) -> str:\n    prompt = (\n        "한국어 기업 컨설팅 상담 녹음입니다. "\n        f"업체명은 {company_name or \'미확인\'}입니다. "\n        "기업 상담 전문용어를 문맥에 맞게 정확히 받아쓰세요. "\n        "이익소각, 자기주식, 가지급금, 가업승계, 정관개정, "\n        "임원퇴직금, 비상장주식평가, 경영인정기보험, 정책자금, "\n        "고용지원금, 세액공제 용어를 일반 단어로 바꾸지 마세요. "\n        f"전체 {total_chunks}개 중 {chunk_index}번째 구간입니다."\n    )\n\n    last_error = ""\n    for attempt in range(1, TRANSCRIPTION_RETRY_COUNT + 1):\n        try:\n            with path.open("rb") as audio_file:\n                response = requests.post(\n                    "https://api.openai.com/v1/audio/transcriptions",\n                    headers={"Authorization": f"Bearer {api_key}"},\n                    files={\n                        "file": (\n                            path.name,\n                            audio_file,\n                            "audio/mp4"\n                            if path.suffix.lower() == ".m4a"\n                            else "application/octet-stream",\n                        )\n                    },\n                    data={\n                        "model": _read_secret(\n                            "OPENAI_TRANSCRIPTION_MODEL",\n                            DEFAULT_TRANSCRIPTION_MODEL,\n                        ),\n                        "response_format": "json",\n                        "language": "ko",\n                        "prompt": prompt,\n                    },\n                    timeout=900,\n                )\n        except requests.RequestException as exc:\n            last_error = str(exc)\n            if attempt < TRANSCRIPTION_RETRY_COUNT:\n                import time\n                time.sleep(attempt * 2)\n                continue\n            raise RuntimeError(\n                f"{chunk_index}/{total_chunks} 구간 연결 실패: {last_error}"\n            ) from exc\n\n        if response.ok:\n            value = response.json().get("text", "")\n            return value.strip() if isinstance(value, str) else ""\n\n        last_error = f"HTTP {response.status_code}: {response.text[:800]}"\n        retryable = (\n            response.status_code >= 500\n            or response.status_code in {408, 409, 429}\n        )\n        if retryable and attempt < TRANSCRIPTION_RETRY_COUNT:\n            import time\n            time.sleep(attempt * 3)\n            continue\n        break\n\n    raise RuntimeError(\n        f"{chunk_index}/{total_chunks} 구간 음성 변환 실패: {last_error}"\n    )\n'
TRANSCRIBE_AUDIO_CODE = 'def transcribe_audio(\n    audio_bytes: bytes,\n    filename: str,\n    company_name: str,\n    progress_callback=None,\n    noise_reduction: bool = True,\n    aggressive_noise_reduction: bool = False,\n    user_id: str = "",\n    audio_digest: str = "",\n) -> str:\n    api_key = _read_secret("OPENAI_API_KEY")\n    if not api_key:\n        raise RuntimeError("OPENAI_API_KEY가 설정되지 않았습니다.")\n\n    transcription_model = _read_secret(\n        "OPENAI_TRANSCRIPTION_MODEL",\n        DEFAULT_TRANSCRIPTION_MODEL,\n    )\n    digest = audio_digest or _audio_hash(audio_bytes)\n    suffix = Path(filename).suffix.lower() or ".m4a"\n\n    with tempfile.TemporaryDirectory(prefix="oasis_audio_") as temp_dir_name:\n        temp_dir = Path(temp_dir_name)\n        original_path = temp_dir / f"original{suffix}"\n        original_path.write_bytes(audio_bytes)\n\n        processing_path = original_path\n        if noise_reduction:\n            processing_path = _preprocess_audio(\n                original_path,\n                temp_dir / "noise_reduced.m4a",\n                aggressive=aggressive_noise_reduction,\n            )\n\n        processing_path = _optimize_audio_for_api(\n            processing_path,\n            temp_dir / "api_optimized.m4a",\n        )\n        chunks = _split_audio_if_needed(processing_path, temp_dir)\n        transcript_parts = []\n        skipped_silence_chunks = 0\n\n        for index, chunk in enumerate(chunks, start=1):\n            if progress_callback:\n                progress_callback(\n                    index,\n                    len(chunks),\n                    f"{index}/{len(chunks)} 구간 처리 중",\n                )\n\n            chunk_key = _chunk_transcript_cache_key(\n                digest,\n                transcription_model,\n                noise_reduction,\n                aggressive_noise_reduction,\n                index,\n                len(chunks),\n            )\n\n            chunk_text = ""\n            if user_id:\n                cached_chunk = _get_cached_value(\n                    user_id,\n                    chunk_key,\n                    "chunk_transcript",\n                )\n                if isinstance(cached_chunk, str):\n                    chunk_text = cached_chunk.strip()\n\n            if not chunk_text:\n                chunk_text = _transcribe_chunk(\n                    api_key,\n                    chunk,\n                    company_name,\n                    index,\n                    len(chunks),\n                )\n                if user_id:\n                    _set_cached_value(\n                        user_id,\n                        chunk_key,\n                        "chunk_transcript",\n                        chunk_text,\n                        metadata={\n                            "filename": filename,\n                            "company_name": company_name,\n                            "chunk_index": index,\n                            "total_chunks": len(chunks),\n                            "model": transcription_model,\n                        },\n                    )\n\n            if not chunk_text:\n                skipped_silence_chunks += 1\n                continue\n\n            transcript_parts.append((index, chunk_text))\n\n    if not transcript_parts:\n        raise RuntimeError(\n            "녹음파일 전체에서 인식 가능한 음성을 찾지 못했습니다."\n        )\n\n    transcript = "\\n\\n".join(\n        f"[구간 {chunk_index}]\\n{chunk_text}"\n        for chunk_index, chunk_text in transcript_parts\n    )\n    if skipped_silence_chunks:\n        transcript += (\n            "\\n\\n[처리 안내]\\n"\n            f"음성이 없는 구간 {skipped_silence_chunks}개는 자동 제외했습니다."\n        )\n    return transcript\n'
SUMMARY_HELPERS = 'def _split_text_sections(transcript: str) -> list[str]:\n    if len(transcript) <= SUMMARY_SECTION_CHARS:\n        return [transcript]\n\n    sections = []\n    start = 0\n    while start < len(transcript):\n        end = min(start + SUMMARY_SECTION_CHARS, len(transcript))\n        if end < len(transcript):\n            boundary = transcript.rfind(\n                "\\n",\n                start + SUMMARY_SECTION_CHARS // 2,\n                end,\n            )\n            if boundary > start:\n                end = boundary\n\n        section = transcript[start:end].strip()\n        if section:\n            sections.append(section)\n\n        if end >= len(transcript):\n            break\n        start = max(end - SUMMARY_SECTION_OVERLAP, start + 1)\n\n    return sections\n\n\ndef _summarize_transcript_section(\n    api_key: str,\n    model: str,\n    section: str,\n    section_index: int,\n    total_sections: int,\n    company_name: str,\n) -> str:\n    prompt = f"""\n한국 중소기업·법인 컨설팅 상담 녹취 일부입니다.\n전체 {total_sections}개 텍스트 구간 중 {section_index}번째입니다.\n업체명: {company_name or \'미확인\'}\n\n최종 상담일지를 위해 다음 항목만 간결하게 정리하세요.\n- 고객이 말한 사실과 수치\n- 고객 니즈와 질문\n- 컨설턴트가 제안한 내용\n- 정책자금·고용지원금·세액공제 관련 근거\n- 정관·주가평가·등기·가지급금·승계·보험 언급\n- 고객과 컨설턴트 후속조치\n- 추가 확인사항\n녹취에 없는 내용은 만들지 마세요.\n\n녹취:\n{section}\n""".strip()\n\n    response = requests.post(\n        "https://api.openai.com/v1/responses",\n        headers={\n            "Authorization": f"Bearer {api_key}",\n            "Content-Type": "application/json",\n        },\n        json={"model": model, "input": prompt},\n        timeout=900,\n    )\n    if not response.ok:\n        raise RuntimeError(\n            f"긴 녹취 {section_index}/{total_sections} 구간 요약 실패 "\n            f"HTTP {response.status_code}: {response.text[:500]}"\n        )\n    return _extract_response_text(response.json()).strip()\n\n\ndef _compact_transcript_for_summary(\n    transcript: str,\n    company_name: str,\n    model: str,\n    api_key: str,\n    user_id: str = "",\n) -> str:\n    sections = _split_text_sections(transcript)\n    if len(sections) == 1:\n        return transcript\n\n    notes = []\n    for index, section in enumerate(sections, start=1):\n        cache_key = _summary_section_cache_key(\n            section,\n            model,\n            index,\n            len(sections),\n        )\n        note = ""\n        if user_id:\n            cached_note = _get_cached_value(\n                user_id,\n                cache_key,\n                "section_summary",\n            )\n            if isinstance(cached_note, str):\n                note = cached_note.strip()\n\n        if not note:\n            note = _summarize_transcript_section(\n                api_key,\n                model,\n                section,\n                index,\n                len(sections),\n                company_name,\n            )\n            if user_id:\n                _set_cached_value(\n                    user_id,\n                    cache_key,\n                    "section_summary",\n                    note,\n                    metadata={\n                        "company_name": company_name,\n                        "section_index": index,\n                        "total_sections": len(sections),\n                        "model": model,\n                    },\n                )\n\n        notes.append(\n            f"[장시간 녹취 요약 {index}/{len(sections)}]\\n{note}"\n        )\n    return "\\n\\n".join(notes)\n\n\n'
CACHE_HELPERS = 'def _chunk_transcript_cache_key(\n    audio_digest: str,\n    transcription_model: str,\n    noise_reduction: bool,\n    aggressive_noise_reduction: bool,\n    chunk_index: int,\n    total_chunks: int,\n) -> str:\n    return "|".join(\n        [\n            CACHE_VERSION,\n            "transcript_chunk",\n            audio_digest,\n            transcription_model,\n            str(bool(noise_reduction)),\n            str(bool(aggressive_noise_reduction)),\n            str(chunk_index),\n            str(total_chunks),\n        ]\n    )\n\n\ndef _summary_section_cache_key(\n    section: str,\n    summary_model: str,\n    section_index: int,\n    total_sections: int,\n) -> str:\n    digest = hashlib.sha256(section.encode("utf-8")).hexdigest()\n    return "|".join(\n        [\n            CACHE_VERSION,\n            "summary_section",\n            digest,\n            summary_model,\n            str(section_index),\n            str(total_sections),\n        ]\n    )\n\n\n'


def fail(message: str) -> None:
    print("UPDATE_FAILED")
    print(message)
    input("Press Enter to close...")
    raise SystemExit(1)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"수정 위치를 찾지 못했습니다: {label}")
    return text.replace(old, new, 1)


def replace_function_block(
    text: str,
    function_name: str,
    next_function_name: str,
    replacement: str,
) -> str:
    start_marker = f"def {function_name}("
    end_marker = f"def {next_function_name}("
    start = text.find(start_marker)
    if start < 0:
        raise RuntimeError(f"함수 시작점을 찾지 못했습니다: {function_name}")
    end = text.find(end_marker, start + len(start_marker))
    if end < 0:
        raise RuntimeError(
            f"다음 함수 시작점을 찾지 못했습니다: {next_function_name}"
        )
    return text[:start] + replacement.rstrip() + "\n\n\n" + text[end:]


def load_cumulative_v810(root: Path):
    path = root / "payload" / "cumulative_v810.py"
    spec = importlib.util.spec_from_file_location("oasis_cumulative_v810", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("v8.1 누적 업데이트 모듈을 불러오지 못했습니다.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def apply_v810_if_needed(root: Path, current_version: str) -> None:
    if current_version in {"v8.1.0", "8.1.0", "v8.2.0", "8.2.0"}:
        return
    module = load_cumulative_v810(root)

    app_path = root / "app.py"
    app_path.write_text(
        module.patch_app(app_path.read_text(encoding="utf-8")),
        encoding="utf-8",
        newline="\n",
    )
    enterprise_path = root / "enterprise_center.py"
    enterprise_path.write_text(
        module.patch_enterprise(
            enterprise_path.read_text(encoding="utf-8")
        ),
        encoding="utf-8",
        newline="\n",
    )
    copilot_path = root / "consulting_copilot.py"
    copilot_path.write_text(
        module.patch_copilot(
            copilot_path.read_text(encoding="utf-8")
        ),
        encoding="utf-8",
        newline="\n",
    )


def patch_consultation_journal(text: str) -> str:
    constants_old = (
        'MAX_API_FILE_BYTES = 24 * 1024 * 1024\n'
        'DEFAULT_TRANSCRIPTION_MODEL = "gpt-4o-mini-transcribe"\n'
    )
    constants_new = (
        'MAX_API_FILE_BYTES = 24 * 1024 * 1024\n'
        'LONG_AUDIO_SEGMENT_SECONDS = 600\n'
        'SUMMARY_SECTION_CHARS = 16000\n'
        'SUMMARY_SECTION_OVERLAP = 800\n'
        'TRANSCRIPTION_RETRY_COUNT = 3\n'
        'DEFAULT_TRANSCRIPTION_MODEL = "gpt-4o-mini-transcribe"\n'
    )
    if "LONG_AUDIO_SEGMENT_SECONDS" not in text:
        text = replace_once(
            text,
            constants_old,
            constants_new,
            "장시간 녹음 설정값",
        )

    if "def _chunk_transcript_cache_key(" not in text:
        marker = "def _journal_cache_key(\n"
        pos = text.find(marker)
        if pos < 0:
            raise RuntimeError("상담일지 캐시 함수 위치를 찾지 못했습니다.")
        text = text[:pos] + CACHE_HELPERS + text[pos:]

    text = replace_function_block(
        text,
        "_split_audio_if_needed",
        "_audio_minutes_from_bytes",
        SPLIT_AUDIO_CODE,
    )
    text = replace_function_block(
        text,
        "_transcribe_chunk",
        "transcribe_audio",
        TRANSCRIBE_CHUNK_CODE,
    )
    text = replace_function_block(
        text,
        "transcribe_audio",
        "summarize_consultation",
        TRANSCRIBE_AUDIO_CODE,
    )

    if "def _compact_transcript_for_summary(" not in text:
        marker = "def summarize_consultation(\n"
        pos = text.find(marker)
        if pos < 0:
            raise RuntimeError("상담일지 요약 함수 위치를 찾지 못했습니다.")
        text = text[:pos] + SUMMARY_HELPERS + text[pos:]

    old_signature = (
        "def summarize_consultation(\n"
        "    transcript: str,\n"
        "    company_name: str,\n"
        "    business_no: str,\n"
        "    consultant_name: str,\n"
        ") -> dict[str, Any]:\n"
    )
    new_signature = (
        "def summarize_consultation(\n"
        "    transcript: str,\n"
        "    company_name: str,\n"
        "    business_no: str,\n"
        "    consultant_name: str,\n"
        '    user_id: str = "",\n'
        ") -> dict[str, Any]:\n"
    )
    if old_signature in text:
        text = text.replace(old_signature, new_signature, 1)

    model_old = (
        '    model = _read_secret("OPENAI_SUMMARY_MODEL", DEFAULT_SUMMARY_MODEL)\n'
        "    topic_taxonomy = _consulting_topics_prompt()\n"
    )
    model_new = (
        '    model = _read_secret("OPENAI_SUMMARY_MODEL", DEFAULT_SUMMARY_MODEL)\n'
        "    topic_taxonomy = _consulting_topics_prompt()\n"
        "    analysis_transcript = _compact_transcript_for_summary(\n"
        "        transcript, company_name, model, api_key, user_id=user_id\n"
        "    )\n"
    )
    if "analysis_transcript = _compact_transcript_for_summary(" not in text:
        text = replace_once(text, model_old, model_new, "긴 녹취 텍스트 요약")

    text = text.replace(
        "\n녹취:\n{transcript}\n",
        "\n녹취 또는 장시간 녹취 구간요약:\n{analysis_transcript}\n",
        1,
    )

    old_call = (
        "    transcript = transcribe_audio(\n"
        "        audio_bytes,\n"
        "        filename,\n"
        "        company_name,\n"
        "        progress_callback=progress_callback,\n"
        "        noise_reduction=noise_reduction,\n"
        "        aggressive_noise_reduction=aggressive_noise_reduction,\n"
        "    )\n"
    )
    new_call = (
        "    transcript = transcribe_audio(\n"
        "        audio_bytes,\n"
        "        filename,\n"
        "        company_name,\n"
        "        progress_callback=progress_callback,\n"
        "        noise_reduction=noise_reduction,\n"
        "        aggressive_noise_reduction=aggressive_noise_reduction,\n"
        "        user_id=user_id,\n"
        "        audio_digest=digest,\n"
        "    )\n"
    )
    if old_call in text:
        text = text.replace(old_call, new_call, 1)

    old_summary_call = (
        "    journal = summarize_consultation(\n"
        "        transcript,\n"
        "        company_name,\n"
        "        business_no,\n"
        "        consultant_name,\n"
        "    )\n"
    )
    new_summary_call = (
        "    journal = summarize_consultation(\n"
        "        transcript,\n"
        "        company_name,\n"
        "        business_no,\n"
        "        consultant_name,\n"
        "        user_id=user_id,\n"
        "    )\n"
    )
    if old_summary_call in text:
        text = text.replace(old_summary_call, new_summary_call, 1)

    return text


def create_backup(root: Path) -> Path:
    backup = (
        root / "_oasis_backups"
        / ("before_v8.2.0_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    )
    backup.mkdir(parents=True, exist_ok=False)
    for relative in TARGET_FILES:
        source = root / relative
        if source.exists():
            destination = backup / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    return backup


def rollback(root: Path, backup: Path) -> None:
    for relative in TARGET_FILES:
        source = backup / relative
        destination = root / relative
        if source.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)


def main() -> None:
    root = Path.cwd()
    if not (root / "app.py").exists():
        fail("app.py가 있는 OASIS 프로젝트 폴더에서 실행해주세요.")

    version_path = root / "VERSION.txt"
    current_version = (
        version_path.read_text(encoding="utf-8-sig").strip()
        if version_path.exists() else ""
    )
    if current_version and current_version not in SUPPORTED_VERSIONS:
        fail(
            f"현재 버전 {current_version}에서는 "
            f"{TARGET_VERSION}을 적용할 수 없습니다."
        )

    backup = create_backup(root)
    try:
        apply_v810_if_needed(root, current_version)

        journal_path = root / "consultation_journal.py"
        journal_path.write_text(
            patch_consultation_journal(
                journal_path.read_text(encoding="utf-8")
            ),
            encoding="utf-8",
            newline="\n",
        )
        version_path.write_text(TARGET_VERSION + "\n", encoding="utf-8")

        changelog = root / "payload" / "CHANGELOG.md"
        if changelog.exists():
            shutil.copy2(changelog, root / "CHANGELOG.md")

        for filename in [
            "app.py",
            "enterprise_center.py",
            "consulting_copilot.py",
            "consultation_journal.py",
        ]:
            path = root / filename
            if path.exists():
                py_compile.compile(str(path), doraise=True)

        if (root / "system_precheck.py").exists():
            sys.path.insert(0, str(root))
            from system_precheck import run_precheck
            report = run_precheck(root, save_report=True)
            if report.get("status") != "PASS":
                errors = [
                    item for item in report.get("checks", [])
                    if not item.get("ok") and item.get("level") == "error"
                ]
                summary = "; ".join(
                    f"{item.get('item')}: {item.get('message')}"
                    for item in errors[:8]
                )
                raise RuntimeError("사전점검 실패: " + summary)

    except Exception as exc:
        rollback(root, backup)
        print("UPDATE_ROLLED_BACK")
        print(f"BACKUP={backup}")
        fail(f"{type(exc).__name__}: {exc}")

    print("UPDATE_OK")
    print(f"VERSION={TARGET_VERSION}")
    print(f"BACKUP={backup}")
    print("CUMULATIVE_UPDATE=YES")
    print("LONG_AUDIO_SPLIT=10_MINUTES")
    print("CHUNK_CACHE=ENABLED")
    print("FAILED_CHUNK_RETRY=3")
    print("LONG_TRANSCRIPT_SUMMARY=ENABLED")
    print("PRECHECK=PASS")
    print("SQL_REQUIRED=NO")
    input("Press Enter to close...")


if __name__ == "__main__":
    main()
