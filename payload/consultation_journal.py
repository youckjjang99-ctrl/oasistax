from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
import streamlit as st

from cloud_sync import sync_crm_record
from crm import (
    append_timeline_event,
    get_customer_record,
    upsert_customer_record,
)
from crm_enhancements import (
    get_crm_profile,
    merge_profile_into_crm_record,
    save_crm_profile,
)
from utils import get_user_dirs


SUPPORTED_AUDIO_TYPES = [
    "mp3",
    "mp4",
    "mpeg",
    "mpga",
    "m4a",
    "wav",
    "webm",
]
MAX_API_FILE_BYTES = 24 * 1024 * 1024
TRANSCRIPTION_MODEL = "gpt-4o-transcribe"
DEFAULT_SUMMARY_MODEL = "gpt-5-mini"


def _read_secret(name: str, default: str = "") -> str:
    value = os.environ.get(name, "")
    if value:
        return value.strip()

    try:
        if name in st.secrets:
            return str(st.secrets[name]).strip()
    except Exception:
        pass

    return default


def _journal_path(user_id: str) -> Path:
    return get_user_dirs(user_id)["base"] / "consultation_journals.json"


def _load_journals(user_id: str) -> list[dict[str, Any]]:
    path = _journal_path(user_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_journals(user_id: str, journals: list[dict[str, Any]]) -> None:
    path = _journal_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(journals[:500], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _extract_response_text(response_data: dict[str, Any]) -> str:
    if isinstance(response_data.get("output_text"), str):
        return response_data["output_text"].strip()

    texts = []
    for output in response_data.get("output", []) or []:
        if not isinstance(output, dict):
            continue
        for content in output.get("content", []) or []:
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str):
                texts.append(text)
    return "\n".join(texts).strip()


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        value = json.loads(cleaned)
        return value if isinstance(value, dict) else {}
    except Exception:
        pass

    match = re.search(r"\{.*\}", cleaned, re.S)
    if not match:
        raise ValueError("상담일지 JSON 결과를 해석하지 못했습니다.")

    value = json.loads(match.group(0))
    return value if isinstance(value, dict) else {}


def _ffmpeg_available() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _audio_duration_seconds(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "오디오 길이를 확인하지 못했습니다.")
    return float(result.stdout.strip())


def _split_audio_if_needed(path: Path, output_dir: Path) -> list[Path]:
    if path.stat().st_size <= MAX_API_FILE_BYTES:
        return [path]

    if not _ffmpeg_available():
        raise RuntimeError(
            "25MB를 초과하는 녹음파일을 분할하려면 ffmpeg가 필요합니다. "
            "Streamlit Cloud 재부팅 후 다시 시도해주세요."
        )

    duration = _audio_duration_seconds(path)
    target_ratio = MAX_API_FILE_BYTES / max(path.stat().st_size, 1)
    segment_seconds = max(120, int(duration * target_ratio * 0.82))
    segment_seconds = min(segment_seconds, 900)

    output_pattern = output_dir / "audio_part_%03d.m4a"
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(path),
            "-vn",
            "-c:a",
            "aac",
            "-b:a",
            "64k",
            "-f",
            "segment",
            "-segment_time",
            str(segment_seconds),
            "-reset_timestamps",
            "1",
            str(output_pattern),
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "녹음파일 분할에 실패했습니다: "
            + (result.stderr[-1000:] if result.stderr else "ffmpeg 오류")
        )

    chunks = sorted(output_dir.glob("audio_part_*.m4a"))
    if not chunks:
        raise RuntimeError("분할된 오디오 파일을 찾지 못했습니다.")

    oversized = [chunk for chunk in chunks if chunk.stat().st_size > 25 * 1024 * 1024]
    if oversized:
        raise RuntimeError(
            "일부 분할파일이 25MB를 초과했습니다. 원본을 더 낮은 음질로 압축해주세요."
        )
    return chunks


def _transcribe_chunk(
    api_key: str,
    path: Path,
    company_name: str,
    chunk_index: int,
    total_chunks: int,
) -> str:
    prompt = (
        f"한국어 기업 상담 녹음입니다. 업체명은 {company_name or '미확인'}입니다. "
        "정책자금, 세무, 보험, 고용지원금, 주가평가, 재무상담 관련 용어를 "
        "가능한 정확하게 받아쓰세요. "
        f"전체 {total_chunks}개 중 {chunk_index}번째 구간입니다."
    )

    with path.open("rb") as audio_file:
        response = requests.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            files={
                "file": (
                    path.name,
                    audio_file,
                    "audio/mp4" if path.suffix.lower() == ".m4a" else "application/octet-stream",
                )
            },
            data={
                "model": TRANSCRIPTION_MODEL,
                "response_format": "json",
                "language": "ko",
                "prompt": prompt,
            },
            timeout=900,
        )

    if not response.ok:
        raise RuntimeError(
            f"음성 변환 실패 HTTP {response.status_code}: {response.text[:800]}"
        )

    data = response.json()
    text = data.get("text", "")
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("음성 변환 결과가 비어 있습니다.")
    return text.strip()


def transcribe_audio(
    audio_bytes: bytes,
    filename: str,
    company_name: str,
    progress_callback=None,
) -> str:
    api_key = _read_secret("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY가 설정되지 않았습니다. "
            "Streamlit Cloud의 Settings → Secrets에 등록해주세요."
        )

    suffix = Path(filename).suffix.lower() or ".m4a"
    with tempfile.TemporaryDirectory(prefix="oasis_audio_") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        original_path = temp_dir / f"original{suffix}"
        original_path.write_bytes(audio_bytes)

        chunks = _split_audio_if_needed(original_path, temp_dir)
        transcript_parts = []

        for index, chunk in enumerate(chunks, start=1):
            if progress_callback:
                progress_callback(index, len(chunks), chunk.name)
            transcript_parts.append(
                _transcribe_chunk(
                    api_key,
                    chunk,
                    company_name,
                    index,
                    len(chunks),
                )
            )

    return "\n\n".join(
        f"[구간 {index}]\n{text}"
        for index, text in enumerate(transcript_parts, start=1)
    )


def summarize_consultation(
    transcript: str,
    company_name: str,
    business_no: str,
    consultant_name: str,
) -> dict[str, Any]:
    api_key = _read_secret("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY가 설정되지 않았습니다.")

    model = _read_secret("OPENAI_SUMMARY_MODEL", DEFAULT_SUMMARY_MODEL)
    prompt = f"""
당신은 한국 중소기업 컨설팅 CRM의 상담일지 작성자입니다.
아래 녹취를 근거로 사실만 정리하고, 녹취에 없는 내용을 만들어내지 마세요.

업체명: {company_name}
사업자등록번호: {business_no}
상담자: {consultant_name}
상담일: {datetime.now().strftime('%Y-%m-%d')}

반드시 아래 JSON 객체만 출력하세요.
{{
  "consultation_title": "상담 제목",
  "summary": "전체 상담 요약",
  "client_needs": ["고객의 니즈"],
  "key_discussions": ["주요 논의사항"],
  "recommended_solutions": ["추천 검토사항"],
  "required_documents": ["추가 필요서류"],
  "risks_and_checks": ["위험요소 및 확인사항"],
  "next_actions": ["다음 액션"],
  "next_contact_date": "YYYY-MM-DD 또는 빈 문자열",
  "crm_status": "신규|상담중|자료수집|신청완료|계약완료|보류 중 하나",
  "pipeline_stage": "신규|초기상담|자료수집|제안준비|제안완료|정책자금 진행|고용지원금 진행|주가평가 진행|계약완료|보류 중 하나",
  "crm_memo": "CRM 상담메모에 저장할 5~10문장 요약"
}}

녹취:
{transcript}
""".strip()

    response = requests.post(
        "https://api.openai.com/v1/responses",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "input": prompt,
        },
        timeout=900,
    )
    if not response.ok:
        raise RuntimeError(
            f"상담일지 생성 실패 HTTP {response.status_code}: {response.text[:800]}"
        )

    result = _extract_json(_extract_response_text(response.json()))
    result["transcript"] = transcript
    result["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return result


def _list_text(value: Any) -> str:
    if isinstance(value, list):
        return "\n".join(f"- {item}" for item in value if str(item).strip())
    return str(value or "").strip()


def _split_list_text(value: str) -> list[str]:
    items = []
    for line in str(value or "").splitlines():
        cleaned = re.sub(r"^\s*[-•\d.)]+\s*", "", line).strip()
        if cleaned:
            items.append(cleaned)
    return items


def _journal_to_timeline_detail(journal: dict[str, Any]) -> str:
    sections = [
        f"상담요약: {journal.get('summary', '')}",
        "고객 니즈: " + ", ".join(journal.get("client_needs", []) or []),
        "주요 논의: " + ", ".join(journal.get("key_discussions", []) or []),
        "다음 액션: " + ", ".join(journal.get("next_actions", []) or []),
    ]
    return "\n".join(section for section in sections if section.split(":", 1)[-1].strip())


def save_consultation_journal(
    user_id: str,
    customer_key: str,
    company_name: str,
    business_no: str,
    consultant_name: str,
    journal: dict[str, Any],
    current_crm: dict[str, Any],
) -> tuple[bool, str]:
    record = dict(journal)
    record.update(
        {
            "journal_id": datetime.now().strftime("%Y%m%d%H%M%S%f"),
            "company_name": company_name,
            "business_no": business_no,
            "consultant_name": consultant_name,
            "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    )

    journals = _load_journals(user_id)
    journals.insert(0, record)
    _save_journals(user_id, journals)

    title = record.get("consultation_title") or "녹음 상담일지"
    append_timeline_event(
        user_id,
        customer_key,
        title,
        _journal_to_timeline_detail(record),
    )

    next_actions = record.get("next_actions", []) or []
    next_action = next_actions[0] if next_actions else current_crm.get("next_action", "없음")
    next_date = record.get("next_contact_date", "") or current_crm.get("next_date", "")
    status = record.get("crm_status", "") or current_crm.get("status", "상담중")
    memo = record.get("crm_memo", "") or record.get("summary", "")

    ok, message = upsert_customer_record(
        user_id,
        customer_key,
        company_name,
        business_no,
        status,
        next_action,
        next_date,
        memo,
    )
    if not ok:
        return False, message

    profile = get_crm_profile(user_id, customer_key, business_no)
    updated_profile = save_crm_profile(
        user_id,
        customer_key,
        record.get("pipeline_stage", "") or profile.get("pipeline_stage", "초기상담"),
        str(profile.get("priority", "3")),
        profile.get("assigned_manager", consultant_name),
    )

    updated_crm = get_customer_record(user_id, customer_key)
    updated_crm = merge_profile_into_crm_record(updated_crm, updated_profile)
    sync_crm_record(user_id, business_no, updated_crm)
    return True, "상담일지와 CRM 내용을 저장했습니다."


def render_audio_consultation_journal(
    user_id: str,
    customer_key: str,
    company_name: str,
    business_no: str,
    consultant_name: str,
    current_crm: dict[str, Any],
) -> None:
    st.markdown("#### 녹음파일 상담일지 자동작성")
    st.caption(
        "통화·미팅 녹음파일을 업로드하면 녹취, 상담요약, 고객 니즈, "
        "다음 액션을 작성하고 CRM에 저장합니다."
    )

    if not _read_secret("OPENAI_API_KEY"):
        st.warning(
            "먼저 Streamlit Cloud → Settings → Secrets에 "
            '`OPENAI_API_KEY = "sk-..."`를 등록해야 합니다.'
        )

    uploaded = st.file_uploader(
        "상담 녹음파일",
        type=SUPPORTED_AUDIO_TYPES,
        key=f"consultation_audio_{business_no}",
        help="mp3, mp4, mpeg, mpga, m4a, wav, webm 지원",
    )

    if uploaded is not None:
        size_mb = len(uploaded.getvalue()) / 1024 / 1024
        st.write(f"파일명: **{uploaded.name}** · 크기: **{size_mb:.1f}MB**")
        if size_mb > 25:
            st.info(
                "25MB를 초과한 파일은 ffmpeg로 자동 분할한 뒤 순서대로 녹취합니다."
            )

    if st.button(
        "녹취 및 상담일지 생성",
        type="primary",
        use_container_width=True,
        disabled=uploaded is None,
        key=f"generate_consultation_journal_{business_no}",
    ):
        try:
            progress = st.progress(0)
            status_box = st.empty()

            def update_progress(index: int, total: int, name: str) -> None:
                progress.progress(min(index / max(total, 1), 1.0))
                status_box.info(f"녹취 중: {index}/{total} 구간")

            audio_bytes = uploaded.getvalue()
            transcript = transcribe_audio(
                audio_bytes,
                uploaded.name,
                company_name,
                progress_callback=update_progress,
            )
            status_box.info("상담일지를 정리하고 있습니다.")
            journal = summarize_consultation(
                transcript,
                company_name,
                business_no,
                consultant_name,
            )
            st.session_state[f"consultation_journal_{business_no}"] = journal
            progress.progress(1.0)
            status_box.success("상담일지 초안이 생성되었습니다.")
        except Exception as exc:
            st.error(f"상담일지 생성 중 오류가 발생했습니다: {exc}")

    journal_key = f"consultation_journal_{business_no}"
    draft = st.session_state.get(journal_key)
    if not isinstance(draft, dict):
        return

    st.markdown("##### 상담일지 검토·수정")
    title = st.text_input(
        "상담 제목",
        value=str(draft.get("consultation_title", "") or ""),
        key=f"journal_title_{business_no}",
    )
    summary = st.text_area(
        "상담 요약",
        value=str(draft.get("summary", "") or ""),
        height=140,
        key=f"journal_summary_{business_no}",
    )
    needs = st.text_area(
        "고객 니즈",
        value=_list_text(draft.get("client_needs", [])),
        height=110,
        key=f"journal_needs_{business_no}",
    )
    discussions = st.text_area(
        "주요 논의사항",
        value=_list_text(draft.get("key_discussions", [])),
        height=110,
        key=f"journal_discussions_{business_no}",
    )
    solutions = st.text_area(
        "추천 검토사항",
        value=_list_text(draft.get("recommended_solutions", [])),
        height=110,
        key=f"journal_solutions_{business_no}",
    )
    documents = st.text_area(
        "추가 필요서류",
        value=_list_text(draft.get("required_documents", [])),
        height=100,
        key=f"journal_documents_{business_no}",
    )
    checks = st.text_area(
        "위험요소·확인사항",
        value=_list_text(draft.get("risks_and_checks", [])),
        height=100,
        key=f"journal_checks_{business_no}",
    )
    actions = st.text_area(
        "다음 액션",
        value=_list_text(draft.get("next_actions", [])),
        height=100,
        key=f"journal_actions_{business_no}",
    )
    next_date = st.text_input(
        "다음 연락 예정일",
        value=str(draft.get("next_contact_date", "") or ""),
        placeholder="YYYY-MM-DD",
        key=f"journal_next_date_{business_no}",
    )
    crm_memo = st.text_area(
        "CRM 저장 메모",
        value=str(draft.get("crm_memo", "") or ""),
        height=130,
        key=f"journal_crm_memo_{business_no}",
    )

    with st.expander("전체 녹취 보기", expanded=False):
        transcript = st.text_area(
            "녹취록",
            value=str(draft.get("transcript", "") or ""),
            height=360,
            key=f"journal_transcript_{business_no}",
        )

    if st.button(
        "상담일지 및 CRM 저장",
        use_container_width=True,
        key=f"save_consultation_journal_{business_no}",
    ):
        edited = dict(draft)
        edited.update(
            {
                "consultation_title": title,
                "summary": summary,
                "client_needs": _split_list_text(needs),
                "key_discussions": _split_list_text(discussions),
                "recommended_solutions": _split_list_text(solutions),
                "required_documents": _split_list_text(documents),
                "risks_and_checks": _split_list_text(checks),
                "next_actions": _split_list_text(actions),
                "next_contact_date": next_date.strip(),
                "crm_memo": crm_memo,
                "transcript": transcript,
            }
        )
        ok, message = save_consultation_journal(
            user_id,
            customer_key,
            company_name,
            business_no,
            consultant_name,
            edited,
            current_crm,
        )
        if ok:
            st.success(message)
            st.session_state.pop(journal_key, None)
            st.rerun()
        else:
            st.error(message)

    journals = [
        item
        for item in _load_journals(user_id)
        if item.get("business_no") == business_no
    ]
    if journals:
        with st.expander(
            f"저장된 상담일지 {len(journals)}건",
            expanded=False,
        ):
            for item in journals[:20]:
                st.markdown(
                    f"**{item.get('saved_at', '')} · "
                    f"{item.get('consultation_title', '상담일지')}**"
                )
                st.write(item.get("summary", ""))
                st.divider()
