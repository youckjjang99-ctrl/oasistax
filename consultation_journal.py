from __future__ import annotations

import hashlib
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
DEFAULT_TRANSCRIPTION_MODEL = "gpt-4o-mini-transcribe"
DEFAULT_SUMMARY_MODEL = "gpt-5-mini"
CACHE_VERSION = "v1"

CONSULTING_TOPIC_TAXONOMY = [
    "정책자금",
    "고용지원금",
    "통합고용세액공제",
    "통합투자세액공제",
    "연구인력개발비세액공제",
    "경정청구",
    "법인세·소득세 절세",
    "이익소각",
    "자기주식 취득·소각",
    "가지급금 정리",
    "가수금 정리",
    "미처분이익잉여금 관리",
    "배당정책",
    "임원퇴직금",
    "유족보상금",
    "정관개정",
    "주주총회·이사회 의사결정",
    "가업승계",
    "상속·증여",
    "비상장주식 평가",
    "명의신탁주식",
    "차명주식 정리",
    "법인전환",
    "개인사업자 법인전환",
    "합병·분할·조직재편",
    "특허·상표·기업부설연구소",
    "벤처·이노비즈·메인비즈 인증",
    "R&D 정부지원사업",
    "TIPS·LIPS",
    "수출·판로지원",
    "시설투자·기계구입·차량구입",
    "공장등록·스마트공장",
    "재무구조 개선",
    "부채비율·현금흐름 관리",
    "대표자 보장·법인보험",
    "CEO정기보험·경영인정기보험",
    "퇴직재원·승계재원 마련",
    "노무·근로계약·취업규칙",
    "중대재해·산업안전",
    "4대보험·보수총액",
    "기장·세무조정",
    "기업가치평가",
    "투자유치·재무제표 개선",
]


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



def _cache_path(user_id: str) -> Path:
    return get_user_dirs(user_id)["base"] / "consultation_ai_cache.json"


def _load_cache(user_id: str) -> dict[str, Any]:
    path = _cache_path(user_id)
    if not path.exists():
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_cache(user_id: str, cache: dict[str, Any]) -> None:
    path = _cache_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    # 최근 사용 200개까지만 보관해 파일이 무한히 커지는 것을 막는다.
    sorted_items = sorted(
        cache.items(),
        key=lambda item: str(
            item[1].get("updated_at", "")
            if isinstance(item[1], dict)
            else ""
        ),
        reverse=True,
    )[:200]

    path.write_text(
        json.dumps(
            dict(sorted_items),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _audio_hash(audio_bytes: bytes) -> str:
    return hashlib.sha256(audio_bytes).hexdigest()


def _transcript_cache_key(
    audio_digest: str,
    transcription_model: str,
    noise_reduction: bool,
    aggressive_noise_reduction: bool,
) -> str:
    return "|".join(
        [
            CACHE_VERSION,
            "transcript",
            audio_digest,
            transcription_model,
            str(bool(noise_reduction)),
            str(bool(aggressive_noise_reduction)),
        ]
    )


def _journal_cache_key(
    transcript: str,
    summary_model: str,
    company_name: str,
) -> str:
    transcript_digest = hashlib.sha256(
        transcript.encode("utf-8")
    ).hexdigest()

    return "|".join(
        [
            CACHE_VERSION,
            "journal",
            transcript_digest,
            summary_model,
            company_name.strip(),
        ]
    )


def _get_cached_value(
    user_id: str,
    key: str,
    field: str,
) -> Any:
    cache = _load_cache(user_id)
    record = cache.get(key, {})
    if not isinstance(record, dict):
        return None
    return record.get(field)


def _set_cached_value(
    user_id: str,
    key: str,
    field: str,
    value: Any,
    metadata: dict[str, Any] | None = None,
) -> None:
    cache = _load_cache(user_id)
    record = cache.get(key, {})
    if not isinstance(record, dict):
        record = {}

    record[field] = value
    record["updated_at"] = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    if metadata:
        record["metadata"] = metadata

    cache[key] = record
    _save_cache(user_id, cache)


def _delete_cached_value(
    user_id: str,
    key: str,
) -> None:
    cache = _load_cache(user_id)
    if key in cache:
        cache.pop(key, None)
        _save_cache(user_id, cache)


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




def _optimize_audio_for_api(
    source_path: Path,
    output_path: Path,
) -> Path:
    """
    음성인식에 필요한 대역만 남기고 모노·16kHz·48kbps로 압축한다.
    대화 인식 품질을 유지하면서 업로드 용량과 전송시간을 크게 줄인다.
    """
    if not _ffmpeg_available():
        return source_path

    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(source_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "aac",
            "-b:a",
            "48k",
            str(output_path),
        ],
        capture_output=True,
        text=True,
        timeout=1200,
    )

    if result.returncode != 0 or not output_path.exists():
        return source_path

    return output_path


def _preprocess_audio(
    source_path: Path,
    output_path: Path,
    aggressive: bool = False,
) -> Path:
    """
    카페 배경음·에어컨·저주파 소음·작은 음량을 완화한다.
    원본은 보존하고 전처리 복사본만 사용한다.
    """
    if not _ffmpeg_available():
        return source_path

    if aggressive:
        audio_filter = (
            "highpass=f=100,"
            "lowpass=f=7800,"
            "afftdn=nf=-30:tn=1,"
            "dynaudnorm=f=200:g=12,"
            "loudnorm=I=-18:TP=-2:LRA=11"
        )
    else:
        audio_filter = (
            "highpass=f=80,"
            "lowpass=f=8200,"
            "afftdn=nf=-24:tn=1,"
            "dynaudnorm=f=250:g=8"
        )

    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(source_path),
            "-vn",
            "-af",
            audio_filter,
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "aac",
            "-b:a",
            "48k",
            str(output_path),
        ],
        capture_output=True,
        text=True,
        timeout=1200,
    )
    if result.returncode != 0 or not output_path.exists():
        return source_path

    return output_path


def _consulting_topics_prompt() -> str:
    return ", ".join(CONSULTING_TOPIC_TAXONOMY)


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
        f"한국어 기업 컨설팅 상담 녹음입니다. 업체명은 {company_name or '미확인'}입니다. "
        "다음 분야의 전문용어를 문맥에 맞게 정확히 받아쓰세요: "
        f"{_consulting_topics_prompt()}. "
        "특히 이익소각, 자기주식, 가지급금, 가업승계, 정관개정, 임원퇴직금, "
        "비상장주식평가, 경영인정기보험, 정책자금, 고용지원금, 세액공제 용어를 "
        "일반 단어로 잘못 바꾸지 마세요. "
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
                "model": _read_secret(
                    "OPENAI_TRANSCRIPTION_MODEL",
                    DEFAULT_TRANSCRIPTION_MODEL,
                ),
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
    noise_reduction: bool = True,
    aggressive_noise_reduction: bool = False,
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

        processing_path = original_path
        if noise_reduction:
            processing_path = _preprocess_audio(
                original_path,
                temp_dir / "noise_reduced.m4a",
                aggressive=aggressive_noise_reduction,
            )

        processing_path = _optimize_audio_for_api(
            processing_path,
            temp_dir / "api_optimized.m4a",
        )

        chunks = _split_audio_if_needed(processing_path, temp_dir)
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
    topic_taxonomy = _consulting_topics_prompt()
    prompt = f"""
당신은 한국 중소기업·법인 컨설팅 CRM의 상담일지 작성자입니다.
아래 녹취를 근거로 사실만 정리하고, 녹취에 없는 내용을 만들어내지 마세요.
정책자금 중심으로만 해석하지 말고 세무·법무·상법·승계·보험·노무·재무 주제를 모두 열어두고 분석하세요.

업체명: {company_name}
사업자등록번호: {business_no}
상담자: {consultant_name}
상담일: {datetime.now().strftime('%Y-%m-%d')}

분석 가능한 주요 주제:
{topic_taxonomy}

반드시 아래 JSON 객체만 출력하세요.
{{
  "consultation_title": "상담 제목",
  "summary": "전체 상담 요약",
  "detected_topics": [
    {{
      "topic": "분류된 상담주제",
      "confidence": 0,
      "evidence": "녹취에서 판단한 근거",
      "recommended_review": "추가 검토 방향"
    }}
  ],
  "client_needs": ["고객의 니즈"],
  "key_discussions": ["주요 논의사항"],
  "recommended_solutions": ["추천 검토사항"],
  "required_documents": ["추가 필요서류"],
  "client_promises": ["고객이 제출하거나 확인하기로 한 사항"],
  "consultant_tasks": ["상담자가 해야 할 후속조치"],
  "unresolved_questions": ["답변되지 않았거나 다음 상담에서 확인할 질문"],
  "risks_and_checks": ["세무·상법·노무·보험·재무상 위험요소와 확인사항"],
  "next_actions": ["다음 액션"],
  "next_contact_date": "YYYY-MM-DD 또는 빈 문자열",
  "crm_status": "신규|상담중|자료수집|신청완료|계약완료|보류 중 하나",
  "pipeline_stage": "신규|초기상담|자료수집|제안준비|제안완료|정책자금 진행|고용지원금 진행|주가평가 진행|계약완료|보류 중 하나",
  "crm_memo": "CRM 상담메모에 저장할 5~10문장 요약",
  "follow_up_message": "상담 후 고객에게 보낼 짧고 정중한 카카오톡 문안"
}}

주제 분류 원칙:
- 이익소각과 자기주식 취득·소각을 혼동하지 마세요.
- 가지급금·가수금·미처분이익잉여금을 구분하세요.
- 가업승계는 상속·증여·주식가치·대표자 유고재원과 연결해 보되, 녹취 근거가 있을 때만 기록하세요.
- 정관개정은 임원퇴직금·유족보상금·주식양도제한·배당·주총·이사회 규정 등 구체 내용을 분리하세요.
- 보험은 단순 보장성보험과 법인 재무목적 보험을 구분하세요.
- 법률·세무 결론을 단정하지 말고 필요한 서류와 확인사항을 제시하세요.
- confidence는 0~100 정수로 작성하세요.

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



def transcribe_audio_with_cache(
    user_id: str,
    audio_bytes: bytes,
    filename: str,
    company_name: str,
    progress_callback=None,
    noise_reduction: bool = True,
    aggressive_noise_reduction: bool = False,
    reuse_cache: bool = True,
) -> tuple[str, bool, str]:
    transcription_model = _read_secret(
        "OPENAI_TRANSCRIPTION_MODEL",
        DEFAULT_TRANSCRIPTION_MODEL,
    )
    digest = _audio_hash(audio_bytes)
    cache_key = _transcript_cache_key(
        digest,
        transcription_model,
        noise_reduction,
        aggressive_noise_reduction,
    )

    if reuse_cache:
        cached_transcript = _get_cached_value(
            user_id,
            cache_key,
            "transcript",
        )
        if isinstance(cached_transcript, str) and cached_transcript.strip():
            return cached_transcript, True, cache_key

    transcript = transcribe_audio(
        audio_bytes,
        filename,
        company_name,
        progress_callback=progress_callback,
        noise_reduction=noise_reduction,
        aggressive_noise_reduction=aggressive_noise_reduction,
    )

    _set_cached_value(
        user_id,
        cache_key,
        "transcript",
        transcript,
        metadata={
            "filename": filename,
            "company_name": company_name,
            "model": transcription_model,
            "size_bytes": len(audio_bytes),
        },
    )
    return transcript, False, cache_key


def summarize_consultation_with_cache(
    user_id: str,
    transcript: str,
    company_name: str,
    business_no: str,
    consultant_name: str,
    reuse_cache: bool = True,
) -> tuple[dict[str, Any], bool, str]:
    summary_model = _read_secret(
        "OPENAI_SUMMARY_MODEL",
        DEFAULT_SUMMARY_MODEL,
    )
    cache_key = _journal_cache_key(
        transcript,
        summary_model,
        company_name,
    )

    if reuse_cache:
        cached_journal = _get_cached_value(
            user_id,
            cache_key,
            "journal",
        )
        if isinstance(cached_journal, dict) and cached_journal:
            return dict(cached_journal), True, cache_key

    journal = summarize_consultation(
        transcript,
        company_name,
        business_no,
        consultant_name,
    )

    _set_cached_value(
        user_id,
        cache_key,
        "journal",
        journal,
        metadata={
            "company_name": company_name,
            "business_no": business_no,
            "model": summary_model,
        },
    )
    return journal, False, cache_key


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
    topic_names = [
        item.get("topic", "")
        for item in journal.get("detected_topics", []) or []
        if isinstance(item, dict) and item.get("topic")
    ]
    sections = [
        f"상담요약: {journal.get('summary', '')}",
        "상담주제: " + ", ".join(topic_names),
        "고객 니즈: " + ", ".join(journal.get("client_needs", []) or []),
        "주요 논의: " + ", ".join(journal.get("key_discussions", []) or []),
        "고객 약속: " + ", ".join(journal.get("client_promises", []) or []),
        "상담자 업무: " + ", ".join(journal.get("consultant_tasks", []) or []),
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

    cache_col1, cache_col2 = st.columns(2)
    with cache_col1:
        reuse_transcript_cache = st.checkbox(
            "같은 녹음파일 녹취 재사용",
            value=True,
            key=f"reuse_transcript_cache_{business_no}",
            help="같은 파일과 같은 노이즈 옵션이면 음성 API를 다시 호출하지 않습니다.",
        )
    with cache_col2:
        reuse_journal_cache = st.checkbox(
            "같은 녹취 상담일지 재사용",
            value=True,
            key=f"reuse_journal_cache_{business_no}",
            help="같은 녹취와 같은 모델이면 요약 API를 다시 호출하지 않습니다.",
        )

    option_col1, option_col2 = st.columns(2)
    with option_col1:
        noise_reduction = st.checkbox(
            "카페·배경소음 감소 전처리",
            value=True,
            key=f"noise_reduction_{business_no}",
            help="저주파 소음, 에어컨, 잔잔한 배경음, 작은 음량을 보정합니다.",
        )
    with option_col2:
        aggressive_noise_reduction = st.checkbox(
            "강한 노이즈 제거",
            value=False,
            key=f"aggressive_noise_reduction_{business_no}",
            help="커피머신·주변 대화가 큰 경우 사용합니다. 음성이 다소 건조해질 수 있습니다.",
        )

    uploaded = st.file_uploader(
        "상담 녹음파일",
        type=SUPPORTED_AUDIO_TYPES,
        key=f"consultation_audio_{business_no}",
        help="mp3, mp4, mpeg, mpga, m4a, wav, webm 지원",
    )

    st.info(
        "비용 절감 모드: 동일 녹음은 녹취·상담일지 결과를 재사용하고, "
        "새 파일도 음성인식용 저용량 포맷으로 자동 압축합니다."
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
            transcript, transcript_cached, transcript_cache_key = (
                transcribe_audio_with_cache(
                    user_id,
                    audio_bytes,
                    uploaded.name,
                    company_name,
                    progress_callback=update_progress,
                    noise_reduction=noise_reduction,
                    aggressive_noise_reduction=aggressive_noise_reduction,
                    reuse_cache=reuse_transcript_cache,
                )
            )

            if transcript_cached:
                progress.progress(0.65)
                status_box.info(
                    "기존 녹취록을 재사용했습니다. 상담일지를 확인하고 있습니다."
                )
            else:
                status_box.info("상담일지를 정리하고 있습니다.")

            journal, journal_cached, journal_cache_key = (
                summarize_consultation_with_cache(
                    user_id,
                    transcript,
                    company_name,
                    business_no,
                    consultant_name,
                    reuse_cache=reuse_journal_cache,
                )
            )

            journal["_cache_info"] = {
                "transcript_cached": transcript_cached,
                "journal_cached": journal_cached,
                "transcript_cache_key": transcript_cache_key,
                "journal_cache_key": journal_cache_key,
            }

            st.session_state[f"consultation_journal_{business_no}"] = journal
            progress.progress(1.0)

            if transcript_cached and journal_cached:
                status_box.success(
                    "API를 호출하지 않고 기존 녹취·상담일지를 즉시 불러왔습니다."
                )
            elif transcript_cached:
                status_box.success(
                    "기존 녹취를 재사용하고 상담일지만 새로 생성했습니다."
                )
            else:
                status_box.success(
                    "새 녹음의 녹취와 상담일지 초안이 생성되었습니다."
                )
        except Exception as exc:
            st.error(f"상담일지 생성 중 오류가 발생했습니다: {exc}")

    journal_key = f"consultation_journal_{business_no}"
    draft = st.session_state.get(journal_key)
    if not isinstance(draft, dict):
        return

    cache_info = draft.get("_cache_info", {})
    if cache_info.get("transcript_cached") or cache_info.get("journal_cached"):
        saved_parts = []
        if cache_info.get("transcript_cached"):
            saved_parts.append("음성 녹취 API")
        if cache_info.get("journal_cached"):
            saved_parts.append("상담일지 생성 API")
        st.success(
            "캐시 재사용으로 " + ", ".join(saved_parts) + " 호출을 절약했습니다."
        )

    st.markdown("##### 자동 분류된 상담주제")
    detected_topics = draft.get("detected_topics", []) or []
    if detected_topics:
        topic_rows = []
        for item in detected_topics:
            if isinstance(item, dict):
                topic_rows.append({
                    "상담주제": item.get("topic", ""),
                    "신뢰도": f"{item.get('confidence', 0)}%",
                    "판단근거": item.get("evidence", ""),
                    "추가 검토": item.get("recommended_review", ""),
                })
        if topic_rows:
            st.dataframe(
                topic_rows,
                hide_index=True,
                use_container_width=True,
            )
    else:
        st.info("자동 분류된 상담주제가 없습니다.")

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
    client_promises = st.text_area(
        "고객이 약속한 제출·확인사항",
        value=_list_text(draft.get("client_promises", [])),
        height=100,
        key=f"journal_client_promises_{business_no}",
    )
    consultant_tasks = st.text_area(
        "상담자 후속업무",
        value=_list_text(draft.get("consultant_tasks", [])),
        height=100,
        key=f"journal_consultant_tasks_{business_no}",
    )
    unresolved_questions = st.text_area(
        "다음 상담에서 확인할 질문",
        value=_list_text(draft.get("unresolved_questions", [])),
        height=100,
        key=f"journal_unresolved_questions_{business_no}",
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
    follow_up_message = st.text_area(
        "상담 후 고객에게 보낼 카카오톡 문안",
        value=str(draft.get("follow_up_message", "") or ""),
        height=120,
        key=f"journal_follow_up_message_{business_no}",
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
        "녹취는 유지하고 상담일지만 다시 생성",
        use_container_width=True,
        key=f"regenerate_journal_only_{business_no}",
        help="음성 API를 다시 호출하지 않고 현재 녹취록으로 상담일지만 새로 만듭니다.",
    ):
        try:
            status_box = st.empty()
            status_box.info("현재 녹취록으로 상담일지만 다시 작성하고 있습니다.")
            regenerated = summarize_consultation(
                transcript,
                company_name,
                business_no,
                consultant_name,
            )
            regenerated["_cache_info"] = {
                "transcript_cached": True,
                "journal_cached": False,
            }
            st.session_state[journal_key] = regenerated
            status_box.success(
                "음성 재변환 없이 상담일지만 다시 생성했습니다."
            )
            st.rerun()
        except Exception as exc:
            st.error(f"상담일지 재생성 중 오류가 발생했습니다: {exc}")

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
                "client_promises": _split_list_text(client_promises),
                "consultant_tasks": _split_list_text(consultant_tasks),
                "unresolved_questions": _split_list_text(unresolved_questions),
                "next_actions": _split_list_text(actions),
                "next_contact_date": next_date.strip(),
                "follow_up_message": follow_up_message,
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
