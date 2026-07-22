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
from cloud_db import CloudDatabase, cloud_is_configured
from ai_usage import (
    estimate_summary_cost,
    estimate_transcription_cost,
    record_ai_usage,
)
from crm import (
    append_timeline_event,
    get_customer_record,
    make_customer_key,
    upsert_customer_record,
)
from crm_enhancements import (
    get_crm_profile,
    merge_profile_into_crm_record,
    save_crm_profile,
)
from matching_preferences import (
    INTEREST_OPTIONS,
    get_matching_preferences,
    save_matching_preferences,
)
from customer_history import save_customer_event
from consultation_audio_storage import (
    create_signed_audio_url,
    delete_audio,
    link_audio_to_journal,
    list_company_audio,
    normalize_business_no,
    storage_is_configured,
    upload_audio,
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
LONG_AUDIO_SEGMENT_SECONDS = 600
SUMMARY_SECTION_CHARS = 16000
SUMMARY_SECTION_OVERLAP = 800
TRANSCRIPTION_RETRY_COUNT = 3
DEFAULT_TRANSCRIPTION_MODEL = "gpt-4o-mini-transcribe"
DEFAULT_SUMMARY_MODEL = "gpt-5-mini"
CACHE_VERSION = "v1"
CONSULTATION_JOURNAL_TABLE = "oasis_consultation_journals"
CONSULTATION_AI_CACHE_TABLE = "oasis_consultation_ai_cache"

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


def _chunk_transcript_cache_key(
    audio_digest: str,
    transcription_model: str,
    noise_reduction: bool,
    aggressive_noise_reduction: bool,
    chunk_index: int,
    total_chunks: int,
) -> str:
    return "|".join(
        [
            CACHE_VERSION,
            "transcript_chunk",
            audio_digest,
            transcription_model,
            str(bool(noise_reduction)),
            str(bool(aggressive_noise_reduction)),
            str(chunk_index),
            str(total_chunks),
        ]
    )


def _summary_section_cache_key(
    section: str,
    summary_model: str,
    section_index: int,
    total_sections: int,
) -> str:
    digest = hashlib.sha256(section.encode("utf-8")).hexdigest()
    return "|".join(
        [
            CACHE_VERSION,
            "summary_section",
            digest,
            summary_model,
            str(section_index),
            str(total_sections),
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


def _load_cloud_cache_record(
    user_id: str,
    key: str,
) -> dict[str, Any]:
    if not cloud_is_configured():
        return {}
    try:
        rows = CloudDatabase().select(
            CONSULTATION_AI_CACHE_TABLE,
            filters={
                "owner_user_id": user_id,
                "cache_key": key,
            },
            limit=1,
        )
    except Exception:
        return {}
    if not rows or not isinstance(rows[0], dict):
        return {}
    payload = rows[0].get("cache_data", {})
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {}
    return payload if isinstance(payload, dict) else {}


def _save_cloud_cache_record(
    user_id: str,
    key: str,
    record: dict[str, Any],
) -> None:
    if not cloud_is_configured():
        return
    cache_type = "transcript" if "|transcript|" in key else "journal"
    CloudDatabase().upsert(
        CONSULTATION_AI_CACHE_TABLE,
        [{
            "owner_user_id": user_id,
            "cache_key": key,
            "cache_type": cache_type,
            "cache_data": record,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }],
        on_conflict="owner_user_id,cache_key",
    )


def _get_cached_value(
    user_id: str,
    key: str,
    field: str,
) -> Any:
    cache = _load_cache(user_id)
    record = cache.get(key, {})
    if isinstance(record, dict) and record.get(field) not in (None, "", [], {}):
        return record.get(field)

    cloud_record = _load_cloud_cache_record(user_id, key)
    if not cloud_record:
        return None

    # 클라우드 캐시를 로컬에도 복원해 다음 조회는 네트워크 없이 처리한다.
    cache[key] = cloud_record
    _save_cache(user_id, cache)
    return cloud_record.get(field)


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

    try:
        _save_cloud_cache_record(user_id, key, record)
    except Exception:
        # 클라우드 캐시 저장 실패가 상담 생성 자체를 막지 않도록 한다.
        pass


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


def _load_local_journals(user_id: str) -> list[dict[str, Any]]:
    path = _journal_path(user_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _load_cloud_journals(
    user_id: str,
    business_no: str = "",
    limit: int = 500,
) -> list[dict[str, Any]]:
    if not cloud_is_configured():
        return []
    try:
        filters = {"owner_user_id": user_id}
        rows = CloudDatabase().select(
            CONSULTATION_JOURNAL_TABLE,
            filters=filters,
            order="saved_at.desc",
            limit=limit,
        )
    except Exception:
        return []

    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        payload = row.get("journal_data", {})
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        record = dict(payload) if isinstance(payload, dict) else {}
        if business_no and normalize_business_no(record.get("business_no", "")) != normalize_business_no(business_no):
            continue
        for key in (
            "journal_id", "company_name", "business_no",
            "consultant_name", "saved_at",
        ):
            if row.get(key) not in (None, ""):
                record[key] = row.get(key)
        result.append(record)
    return result


def _find_transcript_in_saved_journals(
    user_id: str,
    business_no: str,
    audio_digest: str,
) -> str:
    """v6.3.1 이전에 저장된 상담일지에서 동일 원본 음성의 녹취록을 복구한다."""
    for record in _load_cloud_journals(user_id, business_no=business_no, limit=500):
        audio_storage = record.get("_audio_storage", {})
        if not isinstance(audio_storage, dict):
            continue
        audio_record = audio_storage.get("record", {})
        if not isinstance(audio_record, dict):
            continue
        saved_digest = str(audio_record.get("audio_sha256", "")).strip()
        transcript = str(record.get("transcript", "") or "").strip()
        if saved_digest and saved_digest == audio_digest and transcript:
            return transcript
    return ""


def _find_journal_by_transcript(
    user_id: str,
    business_no: str,
    company_name: str,
    transcript: str,
) -> dict[str, Any]:
    target_digest = hashlib.sha256(transcript.encode("utf-8")).hexdigest()
    for record in _load_cloud_journals(user_id, business_no=business_no, limit=500):
        saved_transcript = str(record.get("transcript", "") or "").strip()
        if not saved_transcript:
            continue
        saved_digest = hashlib.sha256(saved_transcript.encode("utf-8")).hexdigest()
        if saved_digest != target_digest:
            continue
        saved_company = str(record.get("company_name", "") or "").strip()
        if company_name.strip() and saved_company and saved_company != company_name.strip():
            continue
        return dict(record)
    return {}


def _merge_journal_rows(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for group in groups:
        for item in group:
            if not isinstance(item, dict):
                continue
            key = str(item.get("journal_id", "")).strip()
            if not key:
                key = "|".join([
                    str(item.get("business_no", "")),
                    str(item.get("saved_at", "")),
                    str(item.get("consultation_title", "")),
                ])
            if key not in merged:
                merged[key] = dict(item)
            else:
                merged[key].update(
                    {k: v for k, v in item.items() if v not in (None, "", [], {})}
                )
    return sorted(
        merged.values(),
        key=lambda row: str(row.get("saved_at", "")),
        reverse=True,
    )


def _load_journals(user_id: str) -> list[dict[str, Any]]:
    return _merge_journal_rows(
        _load_cloud_journals(user_id),
        _load_local_journals(user_id),
    )


def _save_cloud_journal(user_id: str, record: dict[str, Any]) -> None:
    if not cloud_is_configured():
        return
    cloud_record = {
        "journal_id": str(record.get("journal_id", "")),
        "owner_user_id": user_id,
        "company_name": str(record.get("company_name", "")),
        "business_no": str(record.get("business_no", "")),
        "consultant_name": str(record.get("consultant_name", "")),
        "consultation_title": str(record.get("consultation_title", "") or "녹음 상담일지"),
        "summary": str(record.get("summary", "")),
        "saved_at": str(record.get("saved_at", "")),
        "journal_data": record,
    }
    CloudDatabase().upsert(
        CONSULTATION_JOURNAL_TABLE,
        [cloud_record],
        on_conflict="journal_id",
    )


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


def _split_audio_if_needed(
    path: Path,
    output_dir: Path,
) -> list[Path]:
    """Split long audio by duration as well as file size."""
    if not _ffmpeg_available():
        if path.stat().st_size <= MAX_API_FILE_BYTES:
            return [path]
        raise RuntimeError(
            "장시간 또는 24MB 초과 녹음을 처리하려면 ffmpeg가 필요합니다."
        )

    duration = _audio_duration_seconds(path)
    needs_time_split = duration > LONG_AUDIO_SEGMENT_SECONDS
    needs_size_split = path.stat().st_size > MAX_API_FILE_BYTES

    if not needs_time_split and not needs_size_split:
        return [path]

    segment_seconds = LONG_AUDIO_SEGMENT_SECONDS
    if needs_size_split:
        target_ratio = MAX_API_FILE_BYTES / max(path.stat().st_size, 1)
        size_based_seconds = max(120, int(duration * target_ratio * 0.80))
        segment_seconds = min(segment_seconds, size_based_seconds)

    output_pattern = output_dir / "audio_part_%03d.m4a"
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(path), "-vn",
            "-ac", "1", "-ar", "16000",
            "-c:a", "aac", "-b:a", "48k",
            "-f", "segment",
            "-segment_time", str(segment_seconds),
            "-reset_timestamps", "1",
            str(output_pattern),
        ],
        capture_output=True,
        text=True,
        timeout=1200,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "녹음파일 분할에 실패했습니다: "
            + (result.stderr[-1000:] if result.stderr else "ffmpeg 오류")
        )

    chunks = sorted(output_dir.glob("audio_part_*.m4a"))
    if not chunks:
        raise RuntimeError("분할된 녹음파일을 찾지 못했습니다.")

    oversized = [
        chunk
        for chunk in chunks
        if chunk.stat().st_size > MAX_API_FILE_BYTES
    ]
    if oversized:
        raise RuntimeError("분할된 일부 파일이 24MB를 초과했습니다.")

    return chunks


def _audio_minutes_from_bytes(
    audio_bytes: bytes,
    filename: str,
) -> float:
    suffix = Path(filename).suffix.lower() or ".m4a"
    if not _ffmpeg_available():
        return 0.0
    try:
        with tempfile.TemporaryDirectory(
            prefix="oasis_duration_"
        ) as temp_dir_name:
            path = Path(temp_dir_name) / f"audio{suffix}"
            path.write_bytes(audio_bytes)
            return max(
                _audio_duration_seconds(path),
                0.0,
            ) / 60.0
    except Exception:
        return 0.0



def _prepare_fast_cloud_audio(
    audio_bytes: bytes,
    filename: str,
) -> tuple[bytes, str, str, bool]:
    """
    Supabase 보관용 음성을 모노·16kHz·48kbps m4a로 압축한다.
    브라우저에서 Streamlit로 들어온 뒤의 2차 업로드 시간을 줄인다.
    변환 실패 시 원본을 그대로 반환한다.
    """
    if not _ffmpeg_available():
        return (
            audio_bytes,
            filename,
            "application/octet-stream",
            False,
        )

    suffix = Path(filename).suffix.lower() or ".m4a"
    try:
        with tempfile.TemporaryDirectory(
            prefix="oasis_cloud_audio_"
        ) as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            source = temp_dir / f"source{suffix}"
            output = temp_dir / "cloud_optimized.m4a"
            source.write_bytes(audio_bytes)

            result = subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(source),
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "48k",
                    str(output),
                ],
                capture_output=True,
                text=True,
                timeout=1200,
            )

            if (
                result.returncode != 0
                or not output.exists()
                or output.stat().st_size <= 0
            ):
                return (
                    audio_bytes,
                    filename,
                    "application/octet-stream",
                    False,
                )

            optimized_name = (
                f"{Path(filename).stem}_cloud_optimized.m4a"
            )
            return (
                output.read_bytes(),
                optimized_name,
                "audio/mp4",
                True,
            )
    except Exception:
        return (
            audio_bytes,
            filename,
            "application/octet-stream",
            False,
        )


def _transcribe_chunk(
    api_key: str,
    path: Path,
    company_name: str,
    chunk_index: int,
    total_chunks: int,
) -> str:
    prompt = (
        "한국어 기업 컨설팅 상담 녹음입니다. "
        f"업체명은 {company_name or '미확인'}입니다. "
        "기업 상담 전문용어를 문맥에 맞게 정확히 받아쓰세요. "
        "이익소각, 자기주식, 가지급금, 가업승계, 정관개정, "
        "임원퇴직금, 비상장주식평가, 경영인정기보험, 정책자금, "
        "고용지원금, 세액공제 용어를 일반 단어로 바꾸지 마세요. "
        f"전체 {total_chunks}개 중 {chunk_index}번째 구간입니다."
    )

    last_error = ""
    for attempt in range(1, TRANSCRIPTION_RETRY_COUNT + 1):
        try:
            with path.open("rb") as audio_file:
                response = requests.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    files={
                        "file": (
                            path.name,
                            audio_file,
                            "audio/mp4"
                            if path.suffix.lower() == ".m4a"
                            else "application/octet-stream",
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
        except requests.RequestException as exc:
            last_error = str(exc)
            if attempt < TRANSCRIPTION_RETRY_COUNT:
                import time
                time.sleep(attempt * 2)
                continue
            raise RuntimeError(
                f"{chunk_index}/{total_chunks} 구간 연결 실패: {last_error}"
            ) from exc

        if response.ok:
            value = response.json().get("text", "")
            return value.strip() if isinstance(value, str) else ""

        last_error = f"HTTP {response.status_code}: {response.text[:800]}"
        retryable = (
            response.status_code >= 500
            or response.status_code in {408, 409, 429}
        )
        if retryable and attempt < TRANSCRIPTION_RETRY_COUNT:
            import time
            time.sleep(attempt * 3)
            continue
        break

    raise RuntimeError(
        f"{chunk_index}/{total_chunks} 구간 음성 변환 실패: {last_error}"
    )


def transcribe_audio(
    audio_bytes: bytes,
    filename: str,
    company_name: str,
    progress_callback=None,
    noise_reduction: bool = True,
    aggressive_noise_reduction: bool = False,
    user_id: str = "",
    audio_digest: str = "",
) -> str:
    api_key = _read_secret("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY가 설정되지 않았습니다.")

    transcription_model = _read_secret(
        "OPENAI_TRANSCRIPTION_MODEL",
        DEFAULT_TRANSCRIPTION_MODEL,
    )
    digest = audio_digest or _audio_hash(audio_bytes)
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
        skipped_silence_chunks = 0

        for index, chunk in enumerate(chunks, start=1):
            if progress_callback:
                progress_callback(
                    index,
                    len(chunks),
                    f"{index}/{len(chunks)} 구간 처리 중",
                )

            chunk_key = _chunk_transcript_cache_key(
                digest,
                transcription_model,
                noise_reduction,
                aggressive_noise_reduction,
                index,
                len(chunks),
            )

            chunk_text = ""
            if user_id:
                cached_chunk = _get_cached_value(
                    user_id,
                    chunk_key,
                    "chunk_transcript",
                )
                if isinstance(cached_chunk, str):
                    chunk_text = cached_chunk.strip()

            if not chunk_text:
                chunk_text = _transcribe_chunk(
                    api_key,
                    chunk,
                    company_name,
                    index,
                    len(chunks),
                )
                if user_id:
                    _set_cached_value(
                        user_id,
                        chunk_key,
                        "chunk_transcript",
                        chunk_text,
                        metadata={
                            "filename": filename,
                            "company_name": company_name,
                            "chunk_index": index,
                            "total_chunks": len(chunks),
                            "model": transcription_model,
                        },
                    )

            if not chunk_text:
                skipped_silence_chunks += 1
                continue

            transcript_parts.append((index, chunk_text))

    if not transcript_parts:
        raise RuntimeError(
            "녹음파일 전체에서 인식 가능한 음성을 찾지 못했습니다."
        )

    transcript = "\n\n".join(
        f"[구간 {chunk_index}]\n{chunk_text}"
        for chunk_index, chunk_text in transcript_parts
    )
    if skipped_silence_chunks:
        transcript += (
            "\n\n[처리 안내]\n"
            f"음성이 없는 구간 {skipped_silence_chunks}개는 자동 제외했습니다."
        )
    return transcript


def _split_text_sections(transcript: str) -> list[str]:
    if len(transcript) <= SUMMARY_SECTION_CHARS:
        return [transcript]

    sections = []
    start = 0
    while start < len(transcript):
        end = min(start + SUMMARY_SECTION_CHARS, len(transcript))
        if end < len(transcript):
            boundary = transcript.rfind(
                "\n",
                start + SUMMARY_SECTION_CHARS // 2,
                end,
            )
            if boundary > start:
                end = boundary

        section = transcript[start:end].strip()
        if section:
            sections.append(section)

        if end >= len(transcript):
            break
        start = max(end - SUMMARY_SECTION_OVERLAP, start + 1)

    return sections


def _summarize_transcript_section(
    api_key: str,
    model: str,
    section: str,
    section_index: int,
    total_sections: int,
    company_name: str,
) -> str:
    prompt = f"""
한국 중소기업·법인 컨설팅 상담 녹취 일부입니다.
전체 {total_sections}개 텍스트 구간 중 {section_index}번째입니다.
업체명: {company_name or '미확인'}

최종 상담일지를 위해 다음 항목만 간결하게 정리하세요.
- 고객이 말한 사실과 수치
- 고객 니즈와 질문
- 컨설턴트가 제안한 내용
- 정책자금·고용지원금·세액공제 관련 근거
- 정관·주가평가·등기·가지급금·승계·보험 언급
- 고객과 컨설턴트 후속조치
- 추가 확인사항
녹취에 없는 내용은 만들지 마세요.

녹취:
{section}
""".strip()

    response = requests.post(
        "https://api.openai.com/v1/responses",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={"model": model, "input": prompt},
        timeout=900,
    )
    if not response.ok:
        raise RuntimeError(
            f"긴 녹취 {section_index}/{total_sections} 구간 요약 실패 "
            f"HTTP {response.status_code}: {response.text[:500]}"
        )
    return _extract_response_text(response.json()).strip()


def _compact_transcript_for_summary(
    transcript: str,
    company_name: str,
    model: str,
    api_key: str,
    user_id: str = "",
) -> str:
    sections = _split_text_sections(transcript)
    if len(sections) == 1:
        return transcript

    notes = []
    for index, section in enumerate(sections, start=1):
        cache_key = _summary_section_cache_key(
            section,
            model,
            index,
            len(sections),
        )
        note = ""
        if user_id:
            cached_note = _get_cached_value(
                user_id,
                cache_key,
                "section_summary",
            )
            if isinstance(cached_note, str):
                note = cached_note.strip()

        if not note:
            note = _summarize_transcript_section(
                api_key,
                model,
                section,
                index,
                len(sections),
                company_name,
            )
            if user_id:
                _set_cached_value(
                    user_id,
                    cache_key,
                    "section_summary",
                    note,
                    metadata={
                        "company_name": company_name,
                        "section_index": index,
                        "total_sections": len(sections),
                        "model": model,
                    },
                )

        notes.append(
            f"[장시간 녹취 요약 {index}/{len(sections)}]\n{note}"
        )
    return "\n\n".join(notes)


def summarize_consultation(
    transcript: str,
    company_name: str,
    business_no: str,
    consultant_name: str,
    user_id: str = "",
) -> dict[str, Any]:
    api_key = _read_secret("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY가 설정되지 않았습니다.")

    model = _read_secret("OPENAI_SUMMARY_MODEL", DEFAULT_SUMMARY_MODEL)
    topic_taxonomy = _consulting_topics_prompt()
    analysis_transcript = _compact_transcript_for_summary(
        transcript, company_name, model, api_key, user_id=user_id
    )
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
  "follow_up_message": "상담 후 고객에게 보낼 짧고 정중한 카카오톡 문안",
  "policy_fund_recommendation": {{
    "eligible": true,
    "confidence": 0,
    "matching_keywords": [
      "녹취에서 확인된 정책자금 매칭용 핵심 키워드"
    ],
    "interest_fields": [
      "운전자금|시설자금|기계·설비 구입|차량 구입|신규채용|고용유지|수출|판로|R&D|창업|스마트공장 중 해당 항목"
    ],
    "evidence": [
      "녹취에서 판단한 정책자금 관련 근거"
    ],
    "fund_purpose_hint": "자금 사용 목적 추정 또는 빈 문자열",
    "recommended_questions": [
      "정책자금 매칭 정확도를 높이기 위해 추가로 확인할 질문"
    ]
  }}
}}

주제 분류 원칙:
- 이익소각과 자기주식 취득·소각을 혼동하지 마세요.
- 가지급금·가수금·미처분이익잉여금을 구분하세요.
- 가업승계는 상속·증여·주식가치·대표자 유고재원과 연결해 보되, 녹취 근거가 있을 때만 기록하세요.
- 정관개정은 임원퇴직금·유족보상금·주식양도제한·배당·주총·이사회 규정 등 구체 내용을 분리하세요.
- 보험은 단순 보장성보험과 법인 재무목적 보험을 구분하세요.
- 법률·세무 결론을 단정하지 말고 필요한 서류와 확인사항을 제시하세요.
- confidence는 0~100 정수로 작성하세요.
- 정책자금과 직접 관련된 발언이 없으면 policy_fund_recommendation.eligible은 false로 작성하세요.
- 단순히 기업이라는 이유만으로 정책자금 대상이라고 판단하지 마세요.
- 시설투자, 기계구입, 차량구입, 신규채용, 연구개발, 수출, 판로확대, 운전자금 부족, 공장 증설, 스마트공장 등의 구체적 발언을 근거로 키워드를 작성하세요.
- matching_keywords는 정책자금 공고문 검색에 실제 도움이 되는 짧은 단어 또는 구문으로 작성하세요.
- 이익소각, 가지급금, 가업승계, 정관개정만 논의된 경우에는 정책자금 키워드로 억지 반영하지 마세요.

녹취 또는 장시간 녹취 구간요약:
{analysis_transcript}
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

    response_data = response.json()
    result = _extract_json(
        _extract_response_text(response_data)
    )
    usage = response_data.get("usage", {})
    if not isinstance(usage, dict):
        usage = {}
    input_tokens = int(
        usage.get(
            "input_tokens",
            usage.get("prompt_tokens", 0),
        )
        or 0
    )
    output_tokens = int(
        usage.get(
            "output_tokens",
            usage.get("completion_tokens", 0),
        )
        or 0
    )
    result["_api_usage"] = {
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "estimated_cost_usd": estimate_summary_cost(
            input_tokens,
            output_tokens,
            model,
        ),
    }
    result["transcript"] = transcript
    result["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return result



def transcribe_audio_with_cache(
    user_id: str,
    user_name: str,
    business_no: str,
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
        if not (isinstance(cached_transcript, str) and cached_transcript.strip()):
            cached_transcript = _find_transcript_in_saved_journals(
                user_id,
                business_no,
                digest,
            )
            if cached_transcript:
                _set_cached_value(
                    user_id,
                    cache_key,
                    "transcript",
                    cached_transcript,
                    metadata={
                        "filename": filename,
                        "company_name": company_name,
                        "model": transcription_model,
                        "restored_from": "saved_cloud_journal",
                    },
                )

        if isinstance(cached_transcript, str) and cached_transcript.strip():
            audio_minutes = _audio_minutes_from_bytes(
                audio_bytes,
                filename,
            )
            record_ai_usage(
                user_id=user_id,
                user_name=user_name,
                feature="녹음 상담일지",
                operation="음성 녹취",
                model=transcription_model,
                company_name=company_name,
                business_no=business_no,
                cached=True,
                audio_minutes=audio_minutes,
                saved_cost_usd=estimate_transcription_cost(
                    audio_minutes,
                    transcription_model,
                ),
                metadata={"filename": filename},
            )
            return cached_transcript, True, cache_key

    audio_minutes = _audio_minutes_from_bytes(
        audio_bytes,
        filename,
    )
    transcript = transcribe_audio(
        audio_bytes,
        filename,
        company_name,
        progress_callback=progress_callback,
        noise_reduction=noise_reduction,
        aggressive_noise_reduction=aggressive_noise_reduction,
        user_id=user_id,
        audio_digest=digest,
    )

    record_ai_usage(
        user_id=user_id,
        user_name=user_name,
        feature="녹음 상담일지",
        operation="음성 녹취",
        model=transcription_model,
        company_name=company_name,
        business_no=business_no,
        cached=False,
        audio_minutes=audio_minutes,
        estimated_cost_usd=estimate_transcription_cost(
            audio_minutes,
            transcription_model,
        ),
        metadata={
            "filename": filename,
            "size_bytes": len(audio_bytes),
        },
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
    user_name: str,
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
        if not (isinstance(cached_journal, dict) and cached_journal):
            cached_journal = _find_journal_by_transcript(
                user_id,
                business_no,
                company_name,
                transcript,
            )
            if cached_journal:
                _set_cached_value(
                    user_id,
                    cache_key,
                    "journal",
                    cached_journal,
                    metadata={
                        "company_name": company_name,
                        "business_no": business_no,
                        "model": summary_model,
                        "restored_from": "saved_cloud_journal",
                    },
                )

        if isinstance(cached_journal, dict) and cached_journal:
            api_usage = cached_journal.get(
                "_api_usage",
                {},
            )
            if not isinstance(api_usage, dict):
                api_usage = {}
            record_ai_usage(
                user_id=user_id,
                user_name=user_name,
                feature="녹음 상담일지",
                operation="상담일지 생성",
                model=summary_model,
                company_name=company_name,
                business_no=business_no,
                cached=True,
                input_tokens=int(
                    api_usage.get("input_tokens", 0)
                    or 0
                ),
                output_tokens=int(
                    api_usage.get("output_tokens", 0)
                    or 0
                ),
                saved_cost_usd=float(
                    api_usage.get(
                        "estimated_cost_usd",
                        0,
                    )
                    or 0
                ),
            )
            return dict(cached_journal), True, cache_key

    journal = summarize_consultation(
        transcript,
        company_name,
        business_no,
        consultant_name,
        user_id=user_id,
    )

    api_usage = journal.get("_api_usage", {})
    if not isinstance(api_usage, dict):
        api_usage = {}
    record_ai_usage(
        user_id=user_id,
        user_name=user_name,
        feature="녹음 상담일지",
        operation="상담일지 생성",
        model=summary_model,
        company_name=company_name,
        business_no=business_no,
        cached=False,
        input_tokens=int(
            api_usage.get("input_tokens", 0)
            or 0
        ),
        output_tokens=int(
            api_usage.get("output_tokens", 0)
            or 0
        ),
        estimated_cost_usd=float(
            api_usage.get("estimated_cost_usd", 0)
            or 0
        ),
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



POLICY_INTEREST_ALIASES = {
    "운전자금": "운전자금",
    "시설자금": "시설자금",
    "시설투자": "시설자금",
    "기계구입": "기계·설비 구입",
    "설비구입": "기계·설비 구입",
    "기계·설비": "기계·설비 구입",
    "차량구입": "차량 구입",
    "신규채용": "신규채용",
    "채용": "신규채용",
    "고용유지": "고용유지",
    "수출": "수출",
    "판로": "판로",
    "R&D": "R&D",
    "연구개발": "R&D",
    "창업": "창업",
    "스마트공장": "스마트공장",
}


def _normalize_keyword_list(value: Any) -> list[str]:
    if isinstance(value, list):
        raw_items = value
    else:
        raw_items = re.split(
            r"[,\\n;/|]+",
            str(value or ""),
        )

    result: list[str] = []
    seen: set[str] = set()

    for item in raw_items:
        cleaned = re.sub(
            r"\\s+",
            " ",
            str(item or "").strip(),
        )
        if not cleaned:
            continue

        normalized_key = cleaned.lower()
        if normalized_key in seen:
            continue

        seen.add(normalized_key)
        result.append(cleaned)

    return result


def _normalize_interest_fields(value: Any) -> list[str]:
    raw_items = _normalize_keyword_list(value)
    normalized: list[str] = []

    for item in raw_items:
        candidate = item
        if candidate not in INTEREST_OPTIONS:
            for alias, mapped in POLICY_INTEREST_ALIASES.items():
                if alias.lower() in candidate.lower():
                    candidate = mapped
                    break

        if (
            candidate in INTEREST_OPTIONS
            and candidate not in normalized
        ):
            normalized.append(candidate)

    return normalized


_POLICY_KEYWORD_RULES = {
    "운전자금": ["운전자금", "운영자금", "원재료", "인건비", "매입자금", "매출채권"],
    "시설자금": ["시설자금", "시설투자", "공장신축", "공장증축"],
    "기계·설비 구입": ["기계", "설비", "장비", "생산라인", "자동화설비"],
    "차량 구입": ["차량", "화물차", "트럭", "영업용차량"],
    "수출": ["수출", "해외진출", "해외판로", "바이어"],
    "연구개발": ["연구개발", "R&D", "연구소", "기업부설연구소"],
    "특허·인증": ["특허", "상표", "벤처", "이노비즈", "메인비즈", "인증"],
    "신규채용": ["신규채용", "채용계획", "직원채용", "인원충원"],
    "고용유지": ["고용유지", "고용안정", "휴업", "근로시간단축"],
    "창업·사업화": ["창업", "사업화", "신사업", "신제품"],
    "온라인 마케팅": ["온라인마케팅", "온라인 마케팅", "광고", "홍보"],
    "판로·유통": ["판로", "유통", "입점", "납품", "거래처확대"],
}


def _journal_search_text(journal: dict[str, Any]) -> str:
    values = [
        journal.get("summary", ""),
        journal.get("consultation_title", ""),
        journal.get("transcript", ""),
        journal.get("crm_memo", ""),
        journal.get("client_needs", []),
        journal.get("key_discussions", []),
        journal.get("recommended_solutions", []),
        journal.get("next_actions", []),
    ]
    return json.dumps(values, ensure_ascii=False).lower()


def _fallback_policy_signal(journal: dict[str, Any]) -> dict[str, Any]:
    text = _journal_search_text(journal)
    keywords: list[str] = []
    interests: list[str] = []

    for interest, aliases in _POLICY_KEYWORD_RULES.items():
        matched = [alias for alias in aliases if alias.lower() in text]
        if not matched:
            continue
        if interest not in interests:
            interests.append(interest)
        for alias in matched:
            if alias not in keywords:
                keywords.append(alias)

    policy_terms = [
        "정책자금", "중진공", "소진공", "신용보증기금", "기술보증기금",
        "지역신보", "보증서", "융자", "대출", "지원금", "정부지원",
    ]
    policy_hits = [term for term in policy_terms if term.lower() in text]
    for term in policy_hits:
        if term not in keywords:
            keywords.append(term)

    eligible = bool(interests or policy_hits)
    confidence = min(95, 55 + len(interests) * 8 + len(policy_hits) * 5) if eligible else 0
    return {
        "eligible": eligible,
        "confidence": confidence,
        "matching_keywords": keywords,
        "interest_fields": interests,
        "source": "rule_fallback",
    }


def _policy_signal(journal: dict[str, Any]) -> dict[str, Any]:
    value = journal.get("policy_fund_recommendation", {})
    if isinstance(value, dict):
        has_keywords = bool(value.get("matching_keywords") or value.get("interest_fields"))
        if bool(value.get("eligible", False)) and has_keywords:
            return value

    fallback = _fallback_policy_signal(journal)
    if isinstance(value, dict) and value:
        merged = dict(value)
        if not merged.get("matching_keywords"):
            merged["matching_keywords"] = fallback.get("matching_keywords", [])
        if not merged.get("interest_fields"):
            merged["interest_fields"] = fallback.get("interest_fields", [])
        if not merged.get("eligible"):
            merged["eligible"] = fallback.get("eligible", False)
        if int(merged.get("confidence", 0) or 0) < int(fallback.get("confidence", 0) or 0):
            merged["confidence"] = fallback.get("confidence", 0)
        return merged
    return fallback


def merge_policy_matching_preferences(
    user_id: str,
    business_no: str,
    company_name: str,
    journal: dict[str, Any],
) -> dict[str, Any]:
    """
    상담일지에서 정책자금 관련 신호를 찾아 기존 설정에 추가한다.

    - 기존 키워드와 관심분야는 삭제하지 않는다.
    - AI 신뢰도가 60점 이상인 경우에만 자동 반영한다.
    - 동일 키워드는 중복 저장하지 않는다.
    """
    signal = _policy_signal(journal)
    confidence = int(signal.get("confidence", 0) or 0)
    eligible = bool(signal.get("eligible", False))

    result = {
        "updated": False,
        "confidence": confidence,
        "added_keywords": [],
        "added_interests": [],
        "message": "",
    }

    if not eligible or confidence < 60:
        result["message"] = (
            "정책자금 관련성이 낮아 매칭키워드에는 자동 반영하지 않았습니다."
        )
        return result

    suggested_keywords = _normalize_keyword_list(
        signal.get("matching_keywords", [])
    )
    suggested_interests = _normalize_interest_fields(
        signal.get("interest_fields", [])
    )

    if not suggested_keywords and not suggested_interests:
        result["message"] = (
            "정책자금 관련 신호는 있으나 추가할 키워드를 찾지 못했습니다."
        )
        return result

    current = get_matching_preferences(
        user_id,
        business_no,
    )
    if not isinstance(current, dict):
        current = {}

    current_keywords = _normalize_keyword_list(
        current.get("매칭키워드", [])
    )
    current_interests = _normalize_interest_fields(
        current.get("관심지원분야", [])
    )
    current_exclusions = _normalize_keyword_list(
        current.get("제외키워드", [])
    )

    keyword_keys = {
        item.lower()
        for item in current_keywords
    }
    added_keywords = []

    for keyword in suggested_keywords:
        if keyword.lower() in keyword_keys:
            continue
        current_keywords.append(keyword)
        keyword_keys.add(keyword.lower())
        added_keywords.append(keyword)

    added_interests = []

    for interest in suggested_interests:
        if interest in current_interests:
            continue
        current_interests.append(interest)
        added_interests.append(interest)

    if not added_keywords and not added_interests:
        result["message"] = (
            "추천된 정책자금 키워드가 이미 매칭설정에 반영되어 있습니다."
        )
        return result

    save_matching_preferences(
        user_id,
        business_no,
        company_name=company_name,
        matching_keywords=", ".join(current_keywords),
        interest_fields=current_interests,
        exclusion_keywords=", ".join(current_exclusions),
        fund_purpose=str(
            current.get("자금사용목적", "")
            or ""
        ),
        planned_amount=str(
            current.get("투자예정금액", "")
            or ""
        ),
        planned_timing=str(
            current.get("투자예정시기", "")
            or ""
        ),
    )

    result.update(
        {
            "updated": True,
            "added_keywords": added_keywords,
            "added_interests": added_interests,
            "message": (
                f"정책자금 매칭키워드 {len(added_keywords)}개와 "
                f"관심분야 {len(added_interests)}개를 자동 반영했습니다."
            ),
        }
    )
    return result


def _journal_to_timeline_detail(journal: dict[str, Any]) -> str:
    topic_names = [
        item.get("topic", "")
        for item in journal.get("detected_topics", []) or []
        if isinstance(item, dict) and item.get("topic")
    ]
    policy_signal = _policy_signal(journal)
    policy_keywords = _normalize_keyword_list(
        policy_signal.get(
            "matching_keywords",
            [],
        )
    )

    sections = [
        f"상담요약: {journal.get('summary', '')}",
        "상담주제: " + ", ".join(topic_names),
        "정책자금 키워드: " + ", ".join(policy_keywords),
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
    _save_journals(user_id, _merge_journal_rows(journals))

    cloud_save_warning = ""
    try:
        _save_cloud_journal(user_id, record)
    except Exception as exc:
        cloud_save_warning = f" 클라우드 상담일지 저장 확인 필요: {exc}"

    audio_storage = record.get("_audio_storage", {})
    if isinstance(audio_storage, dict):
        audio_record = audio_storage.get("record", {})
        if isinstance(audio_record, dict):
            link_audio_to_journal(
                audio_id=str(audio_record.get("audio_id", "")),
                journal_id=str(record.get("journal_id", "")),
                consultation_title=str(
                    record.get("consultation_title", "")
                    or "녹음 상담일지"
                ),
                summary=str(record.get("summary", "") or ""),
            )

    title = record.get("consultation_title") or "녹음 상담일지"
    history_title = f"{str(record.get('saved_at', ''))[:10]} 상담 · {title}"
    history_detail = _journal_to_timeline_detail(record)
    append_timeline_event(user_id, customer_key, history_title, history_detail)
    save_customer_event(
        user_id=user_id,
        business_no=business_no,
        company_name=company_name,
        event_id=str(record.get("journal_id", "")),
        event_title=history_title,
        event_detail=history_detail,
        occurred_at=str(record.get("saved_at", "")),
        source="consultation",
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

    policy_result = {
        "updated": False,
        "message": "",
    }

    # v6.4.0: 상담일지 저장 시 정책자금 신호가 있으면 항상 자동 반영
    try:
        policy_result = merge_policy_matching_preferences(
                user_id,
                business_no,
                company_name,
            record,
        )
    except Exception as exc:
        policy_result = {
            "updated": False,
            "message": (
                "상담일지는 저장했지만 정책자금 키워드 자동반영 중 "
                f"오류가 발생했습니다: {exc}"
            ),
        }

    message = "상담일지·CRM·기업히스토리를 저장했습니다."
    if policy_result.get("message"):
        message += " " + str(policy_result["message"])
    if cloud_save_warning:
        message += cloud_save_warning

    return True, message




def _journal_relink_state_path(user_id: str) -> Path:
    return get_user_dirs(user_id)["base"] / "consultation_relink_state.json"


def _load_journal_relink_state(user_id: str) -> set[str]:
    path = _journal_relink_state_path(user_id)
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(item) for item in data if str(item).strip()} if isinstance(data, list) else set()
    except Exception:
        return set()


def _save_journal_relink_state(user_id: str, journal_ids: set[str]) -> None:
    path = _journal_relink_state_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(sorted(journal_ids), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def relink_saved_consultation_journals(
    user_id: str,
    customer_key: str,
    business_no: str,
    company_name: str,
) -> dict[str, Any]:
    journals = _load_journals(user_id)
    normalized = normalize_business_no(business_no)
    matched: list[dict[str, Any]] = []

    for item in journals:
        if not isinstance(item, dict):
            continue
        item_no = normalize_business_no(item.get("business_no", ""))
        item_company = str(item.get("company_name", "") or "").strip()
        if normalized and item_no == normalized:
            matched.append(item)
        elif not normalized and company_name and item_company == company_name.strip():
            matched.append(item)

    matched = sorted(matched, key=lambda row: str(row.get("saved_at", "")))
    processed = _load_journal_relink_state(user_id)
    keyword_added: list[str] = []
    interest_added: list[str] = []
    history_added = 0
    errors: list[str] = []

    for record in matched:
        journal_id = str(record.get("journal_id", "") or "").strip()
        if not journal_id:
            journal_id = hashlib.sha256(
                json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()

        try:
            result = merge_policy_matching_preferences(
                user_id,
                business_no,
                company_name,
                record,
            )
            keyword_added.extend(result.get("added_keywords", []) or [])
            interest_added.extend(result.get("added_interests", []) or [])
        except Exception as exc:
            errors.append(f"키워드 반영 실패({journal_id}): {exc}")

        if journal_id not in processed:
            try:
                title = str(record.get("consultation_title", "") or "녹음 상담일지")
                saved_at = str(record.get("saved_at", "") or "")[:10]
                history_title = f"{saved_at} 상담 · {title}"
                history_detail = _journal_to_timeline_detail(record)
                append_timeline_event(user_id, customer_key, history_title, history_detail)
                save_customer_event(
                    user_id=user_id,
                    business_no=business_no,
                    company_name=company_name,
                    event_id=journal_id,
                    event_title=history_title,
                    event_detail=history_detail,
                    occurred_at=str(record.get("saved_at", "")),
                    source="consultation",
                )
                processed.add(journal_id)
                history_added += 1
            except Exception as exc:
                errors.append(f"히스토리 반영 실패({journal_id}): {exc}")

    _save_journal_relink_state(user_id, processed)

    return {
        "journal_count": len(matched),
        "keyword_count": len({str(item) for item in keyword_added}),
        "interest_count": len({str(item) for item in interest_added}),
        "history_count": history_added,
        "errors": errors,
    }


def get_company_consultation_context(
    user_id: str,
    business_no: str,
    company_name: str = "",
    limit: int = 10,
) -> dict[str, Any]:
    # AI 종합진단에 사용할 기업별 상담 맥락
    normalized = normalize_business_no(business_no)
    journals = _load_journals(user_id)
    matched = []

    for item in journals:
        if not isinstance(item, dict):
            continue
        item_no = normalize_business_no(item.get("business_no", ""))
        item_company = str(item.get("company_name", "") or "").strip()
        if normalized and item_no == normalized:
            matched.append(item)
        elif not normalized and company_name and item_company == company_name.strip():
            matched.append(item)

    matched = sorted(
        matched,
        key=lambda row: str(row.get("saved_at", "")),
        reverse=True,
    )[:max(1, int(limit))]

    def unique(values: list[Any]) -> list[str]:
        result: list[str] = []
        for value in values:
            source = value if isinstance(value, list) else [value]
            for item in source:
                cleaned = str(item or "").strip()
                if cleaned and cleaned not in result:
                    result.append(cleaned)
        return result

    if not matched:
        return {
            "count": 0,
            "latest_saved_at": "",
            "latest_summary": "",
            "matching_keywords": [],
            "interest_fields": [],
            "client_needs": [],
            "key_discussions": [],
            "next_actions": [],
            "journals": [],
        }

    keywords, interests, needs, discussions, actions = [], [], [], [], []
    for item in matched:
        signal = _policy_signal(item)
        keywords.extend(_normalize_keyword_list(signal.get("matching_keywords", [])))
        interests.extend(_normalize_interest_fields(signal.get("interest_fields", [])))
        needs.append(item.get("client_needs", []))
        discussions.append(item.get("key_discussions", []))
        actions.append(item.get("next_actions", []))

    latest = matched[0]
    return {
        "count": len(matched),
        "latest_saved_at": str(latest.get("saved_at", "") or ""),
        "latest_summary": str(latest.get("summary", "") or ""),
        "matching_keywords": unique(keywords),
        "interest_fields": unique(interests),
        "client_needs": unique(needs),
        "key_discussions": unique(discussions),
        "next_actions": unique(actions),
        "journals": matched,
    }

def render_saved_consultation_journals(
    user_id: str,
    business_no: str = "",
    company_name: str = "",
) -> None:
    journals = _load_journals(user_id)
    if business_no:
        target_business_no = normalize_business_no(business_no)
        journals = [
            item for item in journals
            if normalize_business_no(item.get("business_no", "")) == target_business_no
        ]
    if company_name and not business_no:
        journals = [
            item for item in journals
            if str(item.get("company_name", "")) == str(company_name)
        ]

    st.markdown("#### 녹음파일 상담일지 보기")
    st.caption(
        "저장된 상담일지를 클라우드와 로컬 기록에서 통합 조회합니다. "
        "상담 저장 시 추출된 정책자금 키워드는 기업별 매칭설정에 반영됩니다."
    )
    if not journals:
        st.info("이 기업에 저장된 녹음 상담일지가 없습니다.")
        return

    search = st.text_input(
        "상담일지 검색",
        placeholder="제목, 요약, 상담내용, 담당자 검색",
        key=f"journal_search_{business_no or 'all'}",
    ).strip().lower()
    if search:
        journals = [
            item for item in journals
            if search in json.dumps(item, ensure_ascii=False).lower()
        ]

    st.caption(f"조회 결과 {len(journals)}건")

    if business_no or company_name:
        if st.button(
            "기존 상담일지 전체 재연동",
            use_container_width=True,
            key=f"relink_saved_journals_{business_no or company_name}",
        ):
            customer_key = make_customer_key(company_name, business_no)
            result = relink_saved_consultation_journals(
                user_id=user_id,
                customer_key=customer_key,
                business_no=business_no,
                company_name=company_name,
            )
            message = (
                f"상담일지 {result['journal_count']}건 확인 · "
                f"매칭키워드 {result['keyword_count']}개 추가 · "
                f"관심분야 {result['interest_count']}개 추가 · "
                f"기업히스토리 {result['history_count']}건 복구"
            )
            st.session_state[
                f"journal_relink_notice_{business_no or company_name}"
            ] = message
            st.session_state[
                f"journal_relink_errors_{business_no or company_name}"
            ] = result.get("errors", [])
            st.rerun()

    notice_key = f"journal_relink_notice_{business_no or company_name}"
    error_key = f"journal_relink_errors_{business_no or company_name}"
    if st.session_state.get(notice_key):
        st.success(st.session_state.pop(notice_key))
        for error in st.session_state.pop(error_key, [])[:5]:
            st.caption(error)

    for index, item in enumerate(journals[:100]):
        title = str(item.get("consultation_title", "") or "녹음 상담일지")
        saved_at = str(item.get("saved_at", ""))
        consultant = str(item.get("consultant_name", "") or "담당자 미확인")
        label = f"{saved_at} · {title} · {consultant}"
        with st.expander(label, expanded=index == 0):
            if item.get("summary"):
                st.markdown("**상담 요약**")
                st.write(item.get("summary"))

            policy_signal = _policy_signal(item)
            policy_keywords = _normalize_keyword_list(
                policy_signal.get("matching_keywords", [])
            )
            interests = _normalize_interest_fields(
                policy_signal.get("interest_fields", [])
            )
            if policy_keywords or interests:
                st.markdown("**정책자금 자동 추출**")
                if policy_keywords:
                    st.write("매칭키워드: " + ", ".join(policy_keywords))
                if interests:
                    st.write("관심분야: " + ", ".join(interests))

            detail_sections = [
                ("고객 니즈", item.get("client_needs", [])),
                ("주요 논의", item.get("key_discussions", [])),
                ("추천 솔루션", item.get("recommended_solutions", [])),
                ("필요 서류", item.get("required_documents", [])),
                ("상담자 업무", item.get("consultant_tasks", [])),
                ("다음 액션", item.get("next_actions", [])),
            ]
            for heading, values in detail_sections:
                if values:
                    st.markdown(f"**{heading}**")
                    if isinstance(values, list):
                        for value in values:
                            st.write(f"- {value}")
                    else:
                        st.write(values)

            if item.get("next_contact_date"):
                st.write("다음 연락일: " + str(item.get("next_contact_date")))
            if item.get("follow_up_message"):
                st.markdown("**후속 안내문**")
                st.write(item.get("follow_up_message"))
            if item.get("transcript"):
                with st.expander("전체 녹취록", expanded=False):
                    st.write(item.get("transcript"))


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

    if storage_is_configured():
        st.success(
            "원본 녹음파일은 Supabase Storage에 영구 보관되며 "
            "버전 업데이트·Streamlit 재부팅 후에도 유지됩니다."
        )
    else:
        st.warning(
            "Supabase Storage 설정이 없어 원본 녹음파일은 현재 세션에서만 처리됩니다. "
            "상담일지와 녹취록은 기존 방식으로 저장됩니다."
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

    storage_col1, storage_col2 = st.columns(2)
    with storage_col1:
        fast_cloud_storage = st.checkbox(
            "빠른 클라우드 보관",
            value=True,
            key=f"fast_cloud_storage_{business_no}",
            help=(
                "원본을 음성인식용 고효율 m4a로 압축해 Supabase에 저장합니다. "
                "78MB 파일도 보통 훨씬 작아져 2차 업로드가 빨라집니다."
            ),
        )
    with storage_col2:
        st.caption(
            "체크 해제 시 원본 파일을 그대로 보관하지만 업로드 시간이 길어집니다."
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
        "비용·시간 절감 모드: 동일 녹음은 기존 결과를 재사용하고, "
        "녹취를 먼저 진행한 뒤 Supabase에는 저용량 압축본을 저장합니다. "
        "녹음을 늦게 종료해 생긴 무음 구간은 자동 제외합니다. "
        "단, PC에서 Streamlit로 처음 파일을 올리는 시간은 인터넷 업로드 속도에 따라 달라집니다."
    )

    if uploaded is not None:
        size_mb = len(uploaded.getvalue()) / 1024 / 1024
        st.write(f"파일명: **{uploaded.name}** · 크기: **{size_mb:.1f}MB**")
        if size_mb > 25:
            st.info(
                "25MB를 초과한 파일은 ffmpeg로 자동 분할한 뒤 순서대로 녹취합니다."
            )

    job_key = f"consultation_job_{business_no}"
    running_key = f"consultation_running_{business_no}"
    error_key = f"consultation_job_error_{business_no}"

    pending_job = st.session_state.get(job_key)

    # 이전 실행이 비정상 종료되면서 running 값만 남은 경우 자동 복구한다.
    if (
        bool(st.session_state.get(running_key))
        and not isinstance(pending_job, dict)
    ):
        st.session_state[running_key] = False

    is_running = bool(st.session_state.get(running_key))
    is_pending = isinstance(pending_job, dict)

    uploaded_bytes = b""
    upload_ready = False
    upload_status_message = "녹음파일을 선택해주세요."

    if uploaded is not None:
        try:
            uploaded_bytes = uploaded.getvalue()
            upload_ready = len(uploaded_bytes) > 0
            if upload_ready:
                upload_status_message = (
                    f"업로드 완료 · {len(uploaded_bytes) / 1024 / 1024:.1f}MB"
                )
            else:
                upload_status_message = (
                    "파일 정보는 표시되지만 내용이 아직 준비되지 않았습니다."
                )
        except Exception as upload_exc:
            upload_ready = False
            upload_status_message = (
                f"업로드 파일 확인 중 오류: {upload_exc}"
            )

    status_col, reset_col = st.columns([4, 1])
    with status_col:
        if is_running or is_pending:
            st.info("상담일지를 생성하고 있습니다. 중복 실행은 차단됩니다.")
        elif upload_ready:
            st.success(upload_status_message)
        elif uploaded is not None:
            st.warning(upload_status_message)
        else:
            st.caption(upload_status_message)

    with reset_col:
        if st.button(
            "상태 초기화",
            use_container_width=True,
            key=f"reset_consultation_state_{business_no}",
            help="이전 작업상태가 남아 버튼이 비활성화될 때 사용합니다.",
        ):
            st.session_state.pop(job_key, None)
            st.session_state[running_key] = False
            st.session_state.pop(error_key, None)
            st.rerun()

    button_label = (
        "상담일지 생성 중..."
        if is_running or is_pending
        else "녹취 및 상담일지 생성"
    )

    clicked = st.button(
        button_label,
        type="primary",
        use_container_width=True,
        disabled=(
            not upload_ready
            or is_running
            or is_pending
        ),
        key=f"generate_consultation_journal_{business_no}",
    )

    if clicked and upload_ready:
        # 첫 번째 실행에서는 작업자료만 세션에 저장하고 즉시 rerun한다.
        # 다음 실행에서 진행상태 UI를 먼저 표시한 뒤 실제 작업을 시작한다.
        st.session_state[job_key] = {
            "audio_bytes": uploaded_bytes,
            "filename": uploaded.name,
            "content_type": (
                uploaded.type
                if getattr(uploaded, "type", None)
                else "application/octet-stream"
            ),
            "fast_cloud_storage": bool(
                fast_cloud_storage
            ),
            "noise_reduction": bool(noise_reduction),
            "aggressive_noise_reduction": bool(
                aggressive_noise_reduction
            ),
            "reuse_transcript_cache": bool(
                reuse_transcript_cache
            ),
            "reuse_journal_cache": bool(
                reuse_journal_cache
            ),
            "queued_at": datetime.now().isoformat(
                timespec="seconds"
            ),
        }
        st.session_state[running_key] = False
        st.session_state.pop(error_key, None)
        st.rerun()

    pending_job = st.session_state.get(job_key)
    if isinstance(pending_job, dict):
        queued_at_text = str(pending_job.get("queued_at", "") or "")
        try:
            queued_at = datetime.fromisoformat(queued_at_text)
            if (
                datetime.now() - queued_at
            ).total_seconds() > 1800:
                st.session_state.pop(job_key, None)
                st.session_state[running_key] = False
                st.session_state[error_key] = (
                    "이전 상담일지 작업이 30분 이상 완료되지 않아 "
                    "자동으로 초기화했습니다. 다시 실행해주세요."
                )
                st.rerun()
        except Exception:
            pass

        st.session_state[running_key] = True

        with st.status(
            "녹음파일 상담일지를 생성하고 있습니다.",
            expanded=True,
        ) as generation_status:
            progress = st.progress(0)
            stage_message = st.empty()

            st.info(
                "작업이 끝날 때까지 이 화면을 닫지 마세요. "
                "중복 실행을 막기 위해 생성 버튼은 잠시 비활성화됩니다."
            )

            try:
                audio_bytes = pending_job["audio_bytes"]
                filename = pending_job["filename"]

                progress.progress(0.03)
                stage_message.info(
                    "1단계 · 녹음파일을 확인하고 있습니다."
                )

                if not audio_bytes:
                    raise RuntimeError(
                        "업로드된 녹음파일의 내용이 비어 있습니다."
                    )

                audio_storage_result = {
                    "stored": False,
                    "message": "클라우드 보관 대기 중",
                }

                progress.progress(0.08)
                stage_message.info(
                    "2단계 · 캐시와 음성 전처리 조건을 확인하고 있습니다."
                )

                def update_progress(
                    index: int,
                    total: int,
                    name: str,
                ) -> None:
                    ratio = index / max(total, 1)
                    visible_progress = 0.12 + (ratio * 0.53)
                    progress.progress(
                        min(visible_progress, 0.65)
                    )
                    stage_message.info(
                        f"3단계 · 음성을 녹취하고 있습니다. "
                        f"({index}/{total} 구간)"
                    )

                transcript, transcript_cached, transcript_cache_key = (
                    transcribe_audio_with_cache(
                        user_id,
                        consultant_name,
                        business_no,
                        audio_bytes,
                        filename,
                        company_name,
                        progress_callback=update_progress,
                        noise_reduction=bool(
                            pending_job.get(
                                "noise_reduction",
                                True,
                            )
                        ),
                        aggressive_noise_reduction=bool(
                            pending_job.get(
                                "aggressive_noise_reduction",
                                False,
                            )
                        ),
                        reuse_cache=bool(
                            pending_job.get(
                                "reuse_transcript_cache",
                                True,
                            )
                        ),
                    )
                )

                if transcript_cached:
                    progress.progress(0.66)
                    stage_message.info(
                        "3단계 · 기존 녹취록을 불러왔습니다."
                    )
                else:
                    progress.progress(0.66)
                    stage_message.success(
                        "3단계 · 음성 녹취가 완료되었습니다."
                    )

                progress.progress(0.72)
                stage_message.info(
                    "4단계 · 상담주제와 고객 니즈를 분석하고 있습니다."
                )

                journal, journal_cached, journal_cache_key = (
                    summarize_consultation_with_cache(
                        user_id,
                        consultant_name,
                        transcript,
                        company_name,
                        business_no,
                        consultant_name,
                        reuse_cache=bool(
                            pending_job.get(
                                "reuse_journal_cache",
                                True,
                            )
                        ),
                    )
                )

                progress.progress(0.88)
                stage_message.info(
                    "5단계 · 녹음파일을 클라우드에 보관하고 있습니다."
                )

                storage_bytes = audio_bytes
                storage_filename = filename
                storage_content_type = str(
                    pending_job.get(
                        "content_type",
                        "application/octet-stream",
                    )
                )
                compressed_for_storage = False

                if bool(
                    pending_job.get(
                        "fast_cloud_storage",
                        True,
                    )
                ):
                    (
                        storage_bytes,
                        storage_filename,
                        storage_content_type,
                        compressed_for_storage,
                    ) = _prepare_fast_cloud_audio(
                        audio_bytes,
                        filename,
                    )

                try:
                    audio_storage_result = upload_audio(
                        user_id=user_id,
                        user_name=consultant_name,
                        company_name=company_name,
                        business_no=business_no,
                        filename=storage_filename,
                        audio_bytes=storage_bytes,
                        content_type=storage_content_type,
                        original_audio_sha256=_audio_hash(audio_bytes),
                        original_filename=filename,
                        original_size_bytes=len(audio_bytes),
                    )
                    audio_storage_result[
                        "compressed_for_storage"
                    ] = compressed_for_storage
                    audio_storage_result[
                        "original_filename"
                    ] = filename
                    audio_storage_result[
                        "original_size_bytes"
                    ] = len(audio_bytes)
                    audio_storage_result[
                        "stored_size_bytes"
                    ] = len(storage_bytes)
                except Exception as storage_exc:
                    audio_storage_result = {
                        "stored": False,
                        "message": (
                            "클라우드 보관은 실패했지만 녹취와 상담일지 생성은 완료했습니다. "
                            f"원인: {storage_exc}"
                        ),
                        "error": str(storage_exc),
                    }
                    stage_message.warning(
                        audio_storage_result["message"]
                    )

                progress.progress(0.96)
                stage_message.info(
                    "6단계 · 상담일지 초안을 화면에 준비하고 있습니다."
                )

                journal["_cache_info"] = {
                    "transcript_cached": transcript_cached,
                    "journal_cached": journal_cached,
                    "transcript_cache_key": transcript_cache_key,
                    "journal_cache_key": journal_cache_key,
                }
                journal["_audio_storage"] = audio_storage_result

                st.session_state[
                    f"consultation_journal_{business_no}"
                ] = journal

                progress.progress(1.0)

                if transcript_cached and journal_cached:
                    completion_message = (
                        "기존 녹취와 상담일지를 재사용해 "
                        "API 호출 없이 완료했습니다."
                    )
                elif transcript_cached:
                    completion_message = (
                        "기존 녹취를 재사용하고 상담일지만 "
                        "새로 생성했습니다."
                    )
                else:
                    completion_message = (
                        "녹취와 상담일지 초안 생성이 완료되었습니다."
                    )

                stage_message.success(completion_message)
                generation_status.update(
                    label="상담일지 초안 생성 완료",
                    state="complete",
                    expanded=True,
                )

                st.session_state.pop(job_key, None)
                st.session_state[running_key] = False
                st.session_state.pop(error_key, None)

            except Exception as exc:
                error_message = str(exc)
                st.session_state[error_key] = error_message
                st.session_state.pop(job_key, None)
                st.session_state[running_key] = False

                progress.empty()
                stage_message.error(
                    "상담일지 생성이 중단되었습니다."
                )
                generation_status.update(
                    label="상담일지 생성 실패",
                    state="error",
                    expanded=True,
                )

                if (
                    "insufficient_quota" in error_message
                    or "exceeded your current quota"
                    in error_message
                ):
                    st.error(
                        "OpenAI API 잔액 또는 월 사용한도가 부족합니다. "
                        "OpenAI Platform의 Billing과 Usage limit을 "
                        "확인한 뒤 다시 실행해주세요."
                    )
                elif "429" in error_message:
                    st.error(
                        "OpenAI 요청 한도에 도달했습니다. "
                        "잠시 후 다시 실행해주세요."
                    )
                else:
                    st.error(
                        f"상담일지 생성 중 오류가 발생했습니다: "
                        f"{error_message}"
                    )

        # 성공한 경우에만 초안 화면을 다시 그린다.
        # 실패 시에는 현재 화면의 상세 오류를 유지한다.
        if (
            not st.session_state.get(running_key)
            and not st.session_state.get(error_key)
        ):
            st.rerun()

    previous_error = st.session_state.get(error_key)
    if previous_error and not st.session_state.get(running_key):
        error_text = str(previous_error)

        if (
            "insufficient_quota" in error_text
            or "exceeded your current quota" in error_text
        ):
            st.error(
                "OpenAI API 잔액 또는 월 사용한도가 부족합니다. "
                "OpenAI Platform의 Billing과 Usage limit을 확인해주세요."
            )
        elif "429" in error_text:
            st.error(
                "OpenAI 요청 한도에 도달했습니다. 잠시 후 다시 실행해주세요."
            )
        elif (
            "Supabase" in error_text
            or "Storage" in error_text
            or "storage" in error_text
        ):
            st.error(
                "원본 녹음파일의 Supabase 저장 과정에서 오류가 발생했습니다."
            )
            st.code(error_text)
        else:
            st.error(
                "상담일지 생성이 중단되었습니다."
            )
            st.code(error_text)

        if st.button(
            "오류 메시지 닫기",
            use_container_width=True,
            key=f"dismiss_consultation_error_{business_no}",
        ):
            st.session_state.pop(error_key, None)
            st.rerun()

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

    policy_signal = _policy_signal(draft)
    if policy_signal:
        st.markdown("##### 정책자금 매칭 신호")
        policy_confidence = int(
            policy_signal.get("confidence", 0)
            or 0
        )
        policy_eligible = bool(
            policy_signal.get("eligible", False)
        )

        signal_col1, signal_col2 = st.columns(2)
        signal_col1.metric(
            "정책자금 관련성",
            (
                "있음"
                if policy_eligible
                else "낮음"
            ),
        )
        signal_col2.metric(
            "AI 신뢰도",
            f"{policy_confidence}%",
        )

        suggested_keywords = _normalize_keyword_list(
            policy_signal.get(
                "matching_keywords",
                [],
            )
        )
        suggested_interests = _normalize_interest_fields(
            policy_signal.get(
                "interest_fields",
                [],
            )
        )

        if suggested_keywords:
            st.write(
                "**추천 매칭키워드:** "
                + ", ".join(suggested_keywords)
            )
        if suggested_interests:
            st.write(
                "**추천 관심분야:** "
                + ", ".join(suggested_interests)
            )

        evidence_items = _normalize_keyword_list(
            policy_signal.get("evidence", [])
        )
        if evidence_items:
            with st.expander(
                "정책자금 판단 근거",
                expanded=False,
            ):
                for evidence in evidence_items:
                    st.write(f"- {evidence}")

        recommended_questions = _normalize_keyword_list(
            policy_signal.get(
                "recommended_questions",
                [],
            )
        )
        if recommended_questions:
            with st.expander(
                "추가 확인 질문",
                expanded=False,
            ):
                for question in recommended_questions:
                    st.write(f"- {question}")

        auto_policy_keywords = st.checkbox(
            "상담일지 저장 시 정책자금 추천키워드를 자동 반영",
            value=(
                policy_eligible
                and policy_confidence >= 60
            ),
            key=f"auto_policy_keywords_{business_no}",
            help=(
                "기존 키워드는 삭제하지 않고 새로운 추천키워드만 "
                "추가합니다. 신뢰도 60점 미만은 자동 반영하지 않습니다."
            ),
        )
    else:
        auto_policy_keywords = False

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
            api_usage = regenerated.get(
                "_api_usage",
                {},
            )
            if not isinstance(api_usage, dict):
                api_usage = {}
            record_ai_usage(
                user_id=user_id,
                user_name=consultant_name,
                feature="녹음 상담일지",
                operation="상담일지 재생성",
                model=str(
                    api_usage.get(
                        "model",
                        _read_secret(
                            "OPENAI_SUMMARY_MODEL",
                            DEFAULT_SUMMARY_MODEL,
                        ),
                    )
                ),
                company_name=company_name,
                business_no=business_no,
                cached=False,
                input_tokens=int(
                    api_usage.get("input_tokens", 0)
                    or 0
                ),
                output_tokens=int(
                    api_usage.get("output_tokens", 0)
                    or 0
                ),
                estimated_cost_usd=float(
                    api_usage.get(
                        "estimated_cost_usd",
                        0,
                    )
                    or 0
                ),
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
                "_auto_policy_keywords": bool(
                    auto_policy_keywords
                ),
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

    cloud_audio_rows = list_company_audio(
        user_id,
        business_no,
        company_name=company_name,
    )
    if cloud_audio_rows:
        with st.expander(
            f"클라우드 녹음 히스토리 {len(cloud_audio_rows)}건",
            expanded=False,
        ):
            for index, audio_item in enumerate(
                cloud_audio_rows,
                start=1,
            ):
                title_text = (
                    audio_item.get("consultation_title")
                    or audio_item.get("original_filename")
                    or f"상담녹음 {index}"
                )
                st.markdown(
                    f"**{audio_item.get('created_at', '')} · {title_text}**"
                )
                st.caption(
                    f"파일: {audio_item.get('original_filename', '-')} · "
                    f"크기: {int(audio_item.get('size_bytes', 0) or 0) / 1024 / 1024:.1f}MB"
                )
                if audio_item.get("summary"):
                    st.write(audio_item.get("summary"))

                signed_url = create_signed_audio_url(
                    str(audio_item.get("storage_path", "")),
                    expires_in=3600,
                )
                if signed_url:
                    st.audio(signed_url)

                delete_key = (
                    f"delete_cloud_audio_"
                    f"{audio_item.get('audio_id', index)}"
                )
                if st.button(
                    "이 녹음 삭제",
                    key=delete_key,
                ):
                    ok, message = delete_audio(
                        str(audio_item.get("audio_id", "")),
                        str(audio_item.get("storage_path", "")),
                    )
                    if ok:
                        st.success(message)
                        st.rerun()
                    else:
                        st.error(message)
                st.divider()

    journals = [
        item
        for item in _load_journals(user_id)
        if normalize_business_no(item.get("business_no", ""))
        == normalize_business_no(business_no)
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
