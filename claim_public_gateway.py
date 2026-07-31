from __future__ import annotations

import hashlib
import hmac
import html
import importlib
import inspect
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from claim_remote_crypto import ClaimRemoteCrypto, ClaimRemoteCryptoError


SESSION_COOKIE_NAME = "oasis_claim_session"
SESSION_VERSION = 1
CONSENT_VERSION = "claim-remote-customer-v1-2026-07-31"
MAX_SESSION_SECONDS = 24 * 60 * 60
MAX_FORM_BODY_BYTES = 8 * 1024
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,512}$")
_PHONE_PATTERN = re.compile(r"^01(?:0|1|6|7|8|9)\d{7,8}$")
_RRN_PATTERN = re.compile(r"^\d{13}$")


class ClaimPublicGatewayError(RuntimeError):
    """A public-safe gateway error.

    The message must never contain a provider response, token, or customer
    information because it can be rendered in a browser.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 400,
        error_code: str = "CLAIM_PUBLIC_ERROR",
    ):
        super().__init__(str(message or "요청을 처리하지 못했습니다."))
        self.status_code = int(status_code)
        self.error_code = str(error_code or "CLAIM_PUBLIC_ERROR")


class ClaimPublicService(Protocol):
    """Boundary between the public web surface and the durable claim worker."""

    def open_invite(self, invite_token: str) -> Mapping[str, Any]:
        """Validate and mark an invite opened, then return a public snapshot."""

    def submit_customer(
        self,
        *,
        owner_ref: str,
        invite_id: str,
        invite_token: str,
        name: str,
        phone: str,
        resident_number: str,
        consent_version: str,
        consents: Mapping[str, bool],
    ) -> Mapping[str, Any]:
        """Persist consent securely and enqueue the first Hometax auth step."""

    def get_status(
        self,
        *,
        owner_ref: str,
        invite_id: str,
        invite_token: str,
    ) -> Mapping[str, Any]:
        """Return a public-safe progress snapshot for one invite."""


@dataclass(frozen=True)
class ClaimPublicSettings:
    cookie_name: str = SESSION_COOKIE_NAME
    secure_cookies: bool = True
    session_seconds: int = MAX_SESSION_SECONDS
    service_factory: str = ""

    @classmethod
    def from_environment(cls) -> "ClaimPublicSettings":
        cookie_name = str(
            os.environ.get("CLAIM_PUBLIC_COOKIE_NAME", SESSION_COOKIE_NAME)
            or SESSION_COOKIE_NAME
        ).strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", cookie_name):
            cookie_name = SESSION_COOKIE_NAME
        raw_seconds = str(
            os.environ.get(
                "CLAIM_PUBLIC_SESSION_SECONDS",
                MAX_SESSION_SECONDS,
            )
            or MAX_SESSION_SECONDS
        )
        try:
            session_seconds = int(raw_seconds)
        except ValueError:
            session_seconds = MAX_SESSION_SECONDS
        return cls(
            cookie_name=cookie_name,
            secure_cookies=True,
            session_seconds=max(300, min(session_seconds, MAX_SESSION_SECONDS)),
            service_factory=str(
                os.environ.get("CLAIM_PUBLIC_SERVICE_FACTORY", "") or ""
            ).strip(),
        )


class RepositoryClaimPublicService:
    """Small adapter for ClaimRemoteRepository plus an injected submitter.

    The repository intentionally does not know how to create a Tilko claim
    case.  The injected callback owns that business operation and can enqueue
    the durable job atomically.  This keeps the public gateway independent
    from Streamlit and leaves the existing direct-input path untouched.
    """

    def __init__(
        self,
        repository: Any,
        submitter: Callable[..., Mapping[str, Any]],
        *,
        status_reader: Callable[..., Mapping[str, Any]] | None = None,
    ):
        self.repository = repository
        self.submitter = submitter
        self.status_reader = status_reader

    def open_invite(self, invite_token: str) -> Mapping[str, Any]:
        record = dict(self.repository.mark_invite_opened(invite_token) or {})
        secure_payload: dict[str, Any] = {}
        ciphertext = str(record.get("secure_payload_ciphertext", "") or "")
        if ciphertext:
            secure_payload = dict(
                self.repository.decrypt_payload(ciphertext) or {}
            )
        return {
            "invite_id": str(record.get("id", "") or ""),
            "owner_ref": str(
                record.get("owner_user_id")
                or getattr(self.repository, "owner_user_id", "")
                or ""
            ),
            "status": str(record.get("status", "opened") or "opened"),
            "expires_at": record.get("expires_at"),
            "name": str(
                secure_payload.get("name")
                or secure_payload.get("recipient_name")
                or ""
            ),
            "phone": str(
                secure_payload.get("phone")
                or secure_payload.get("recipient_phone")
                or ""
            ),
        }

    def submit_customer(self, **payload: Any) -> Mapping[str, Any]:
        return dict(self.submitter(repository=self.repository, **payload) or {})

    def get_status(
        self,
        *,
        owner_ref: str,
        invite_id: str,
        invite_token: str,
    ) -> Mapping[str, Any]:
        if self.status_reader is not None:
            return dict(
                self.status_reader(
                    repository=self.repository,
                    owner_ref=owner_ref,
                    invite_id=invite_id,
                    invite_token=invite_token,
                )
                or {}
            )
        record = self.repository.get_invite(invite_token)
        if not record:
            raise ClaimPublicGatewayError(
                "인증 요청을 찾을 수 없습니다.",
                status_code=404,
                error_code="INVITE_NOT_FOUND",
            )
        return {
            "invite_id": str(record.get("id", "") or ""),
            "status": str(record.get("status", "") or ""),
            "progress": 0,
        }


class _UnavailableClaimPublicService:
    def __init__(self, reason: str = ""):
        self.reason = str(reason or "")

    def _raise(self) -> None:
        raise ClaimPublicGatewayError(
            "현재 인증 서비스를 준비하고 있습니다. 잠시 후 다시 시도해 주세요.",
            status_code=503,
            error_code="SERVICE_NOT_READY",
        )

    def open_invite(self, invite_token: str) -> Mapping[str, Any]:
        self._raise()
        return {}

    def submit_customer(self, **payload: Any) -> Mapping[str, Any]:
        self._raise()
        return {}

    def get_status(self, **payload: Any) -> Mapping[str, Any]:
        self._raise()
        return {}


class _InviteTokenRedactionMiddleware:
    """Exchange /c/<token> without leaving the raw token in access logs.

    Uvicorn formats its access line from the ASGI scope after the application
    returns.  Mutating the scope before routing therefore replaces the secret
    path with a fixed internal route.  The token is kept only in request state.
    """

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any):
        if scope.get("type") == "http":
            path = str(scope.get("path", "") or "")
            if path.startswith("/c/") and path != "/c/_exchange":
                token = path[3:]
                state = scope.setdefault("state", {})
                state["claim_invite_token"] = token
                scope["path"] = "/c/_exchange"
                scope["raw_path"] = b"/c/_exchange"
                scope["query_string"] = b""
        await self.app(scope, receive, send)


def _load_factory(path: str) -> Any:
    module_name, separator, attribute = str(path or "").partition(":")
    if not separator or not module_name or not attribute:
        raise RuntimeError("CLAIM_PUBLIC_SERVICE_FACTORY 형식이 올바르지 않습니다.")
    module = importlib.import_module(module_name)
    factory = getattr(module, attribute)
    return factory()


def _build_default_service(settings: ClaimPublicSettings) -> ClaimPublicService:
    try:
        if settings.service_factory:
            return _load_factory(settings.service_factory)
        remote_service = importlib.import_module("claim_remote_service")
        factory = getattr(remote_service, "create_public_claim_service")
        return factory()
    except Exception as exc:
        # The reason is retained for local health diagnostics only and is
        # intentionally never returned by an HTTP response.
        return _UnavailableClaimPublicService(type(exc).__name__)


async def _service_call(method: Callable[..., Any], **kwargs: Any) -> Any:
    if inspect.iscoroutinefunction(method):
        return await method(**kwargs)
    result = await run_in_threadpool(lambda: method(**kwargs))
    if inspect.isawaitable(result):
        return await result
    return result


def _safe_int(value: Any, *, minimum: int, maximum: int) -> int:
    try:
        selected = int(value)
    except (TypeError, ValueError):
        selected = minimum
    return max(minimum, min(selected, maximum))


def _epoch(value: Any, *, fallback: int) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value or "").strip()
    if not text:
        return fallback
    try:
        selected = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return int(selected.timestamp())
    except ValueError:
        return fallback


def _normalize_phone(value: str) -> str:
    return re.sub(r"\D+", "", str(value or ""))


def _normalize_resident_number(value: str) -> str:
    return re.sub(r"\D+", "", str(value or ""))


def _validate_name(value: str) -> str:
    selected = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(selected) < 2 or len(selected) > 50:
        raise ClaimPublicGatewayError(
            "이름을 정확히 입력해 주세요.",
            error_code="INVALID_NAME",
        )
    if any(ord(character) < 32 for character in selected):
        raise ClaimPublicGatewayError(
            "이름을 정확히 입력해 주세요.",
            error_code="INVALID_NAME",
        )
    return selected


def _validate_phone(value: str) -> str:
    selected = _normalize_phone(value)
    if not _PHONE_PATTERN.fullmatch(selected):
        raise ClaimPublicGatewayError(
            "휴대전화번호를 정확히 입력해 주세요.",
            error_code="INVALID_PHONE",
        )
    return selected


def _validate_resident_number(value: str) -> str:
    selected = _normalize_resident_number(value)
    if not _RRN_PATTERN.fullmatch(selected) or selected[6] not in "12345678":
        raise ClaimPublicGatewayError(
            "주민등록번호 13자리를 정확히 입력해 주세요.",
            error_code="INVALID_RESIDENT_NUMBER",
        )
    return selected


def _public_snapshot(value: Mapping[str, Any] | None) -> dict[str, Any]:
    source = dict(value or {})
    status = str(source.get("status", "opened") or "opened").lower()[:40]
    stage = str(source.get("stage", "") or "").lower()[:80]
    progress = _safe_int(source.get("progress", 0), minimum=0, maximum=100)
    complete = bool(
        source.get("complete")
        or status in {"complete", "completed", "partial"}
    )
    submitted = bool(
        source.get("submitted")
        or status
        in {
            "submitted",
            "queued",
            "running",
            "waiting",
            "retry",
            "complete",
            "completed",
            "partial",
            "failed",
        }
    )
    labels = {
        "opened": "고객정보 입력 대기",
        "submitted": "국세청 홈택스 인증 요청 준비",
        "queued": "국세청 홈택스 인증 요청 준비",
        "running": "자료 수집 진행 중",
        "waiting": "고객 인증 확인 중",
        "retry": "자료 재확인 중",
        "complete": "자료 수집 완료",
        "completed": "자료 수집 완료",
        "partial": "일부 자료 수집 완료",
        "failed": "인증 또는 수집 확인 필요",
        "expired": "인증 요청 만료",
        "cancelled": "인증 요청 취소",
    }
    message = str(source.get("message") or source.get("safe_message") or "")
    message = re.sub(r"[\x00-\x1f\x7f]+", " ", message).strip()[:240]
    return {
        "status": status,
        "stage": stage,
        "progress": progress,
        "submitted": submitted,
        "complete": complete,
        "label": labels.get(status, "인증 진행 상태 확인"),
        "message": message,
    }


def _pack_session(
    crypto: ClaimRemoteCrypto,
    *,
    owner_ref: str,
    invite_id: str,
    invite_token: str,
    csrf_token: str,
    expires_at: int,
) -> str:
    subject = _session_subject(owner_ref, invite_id)
    signed = crypto.create_session_token(
        invite_id=subject,
        csrf_token=csrf_token,
        expires_at=expires_at,
    )
    return crypto.encrypt_json(
        {
            "v": SESSION_VERSION,
            "owner_ref": owner_ref,
            "invite_id": invite_id,
            "invite_token": invite_token,
            "signed": signed,
        }
    )


def _unpack_session(
    crypto: ClaimRemoteCrypto,
    value: str,
) -> dict[str, Any]:
    try:
        envelope = crypto.decrypt_json(value)
        if int(envelope.get("v", 0) or 0) != SESSION_VERSION:
            raise ClaimRemoteCryptoError("세션 버전이 올바르지 않습니다.")
        signed = crypto.verify_session_token(
            str(envelope.get("signed", "") or "")
        )
        owner_ref = str(envelope.get("owner_ref", "") or "").strip()
        invite_id = str(envelope.get("invite_id", "") or "").strip()
        if (
            not owner_ref
            or len(owner_ref) > 200
            or any(ord(character) < 32 for character in owner_ref)
            or not invite_id
            or len(invite_id) > 100
            or any(ord(character) < 32 for character in invite_id)
        ):
            raise ClaimRemoteCryptoError("인증 요청 대상이 올바르지 않습니다.")
        expected_subject = _session_subject(owner_ref, invite_id)
        if not hmac.compare_digest(
            str(signed.get("invite_id", "") or ""),
            expected_subject,
        ):
            raise ClaimRemoteCryptoError("인증 요청 대상이 일치하지 않습니다.")
        invite_token = str(envelope.get("invite_token", "") or "")
        if not _TOKEN_PATTERN.fullmatch(invite_token):
            raise ClaimRemoteCryptoError("인증 링크가 올바르지 않습니다.")
        return {
            "owner_ref": owner_ref,
            "invite_id": invite_id,
            "csrf": str(signed.get("csrf", "") or ""),
            "exp": int(signed.get("exp", 0) or 0),
            "invite_token": invite_token,
        }
    except (ClaimRemoteCryptoError, TypeError, ValueError) as exc:
        raise ClaimPublicGatewayError(
            "인증 세션이 만료되었거나 올바르지 않습니다. 안내 메시지의 링크를 다시 열어 주세요.",
            status_code=401,
            error_code="INVALID_SESSION",
        ) from exc


def _session_subject(owner_ref: str, invite_id: str) -> str:
    """Bind both identifiers into the signed session without delimiter risks."""

    owner = str(owner_ref or "").encode("utf-8")
    invite = str(invite_id or "").encode("utf-8")
    digest = hashlib.sha256(
        len(owner).to_bytes(4, "big") + owner + invite
    ).hexdigest()
    return f"claim:{digest}"


def _page(
    *,
    title: str,
    body: str,
    refresh_seconds: int | None = None,
) -> HTMLResponse:
    refresh = (
        f'<meta http-equiv="refresh" content="{int(refresh_seconds)}">'
        if refresh_seconds
        else ""
    )
    document = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <meta name="robots" content="noindex,nofollow,noarchive">
  {refresh}
  <title>{html.escape(title)}</title>
  <style>
    :root {{ color-scheme: light; --navy:#0b2d63; --blue:#1769e0;
      --line:#dce5f0; --muted:#607089; --bg:#f4f7fb; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:#13223a;
      font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans KR",
      "Apple SD Gothic Neo",sans-serif; line-height:1.55; }}
    main {{ width:min(100%,560px); min-height:100vh; margin:0 auto;
      padding:calc(22px + env(safe-area-inset-top)) 18px
      calc(32px + env(safe-area-inset-bottom)); background:#fff; }}
    .brand {{ color:var(--navy); font-size:14px; font-weight:800;
      letter-spacing:.04em; margin-bottom:28px; }}
    h1 {{ margin:0 0 10px; color:var(--navy); font-size:28px;
      line-height:1.25; letter-spacing:-.035em; }}
    .lead {{ margin:0 0 28px; color:var(--muted); font-size:15px; }}
    .card {{ border:1px solid var(--line); border-radius:18px;
      padding:20px; box-shadow:0 10px 32px rgba(23,55,96,.07); }}
    label {{ display:block; margin:17px 0 7px; font-size:14px;
      font-weight:700; }}
    input[type=text],input[type=tel],input[type=password] {{
      width:100%; height:52px; border:1px solid #cfd9e6; border-radius:12px;
      padding:0 14px; background:#fff; color:#14233a; font-size:16px; }}
    input:focus {{ outline:3px solid rgba(23,105,224,.15);
      border-color:var(--blue); }}
    .check {{ display:grid; grid-template-columns:24px 1fr; gap:10px;
      align-items:start; margin-top:16px; color:#34445b; font-size:14px; }}
    .check input {{ width:20px; height:20px; margin:2px 0 0; }}
    .notice {{ margin-top:18px; padding:14px; border-radius:12px;
      background:#f1f6fd; color:#42546d; font-size:13px; }}
    button {{ width:100%; min-height:54px; margin-top:22px; border:0;
      border-radius:13px; background:linear-gradient(135deg,#0b51b7,#1676ed);
      color:#fff; font-size:17px; font-weight:800; cursor:pointer; }}
    .error {{ margin:0 0 18px; padding:13px 14px; border-radius:12px;
      background:#fff0f1; color:#aa2434; font-size:14px; }}
    .progress {{ height:10px; overflow:hidden; margin:18px 0 9px;
      border-radius:999px; background:#e8eef6; }}
    .progress span {{ display:block; height:100%; border-radius:inherit;
      background:linear-gradient(90deg,#0d55c5,#2784f2); }}
    .status {{ margin-top:10px; color:var(--muted); font-size:14px; }}
    .foot {{ margin-top:26px; color:#7b899d; font-size:12px; text-align:center; }}
  </style>
</head>
<body><main>
  <div class="brand">OASIS TAX &amp; ACCOUNTING</div>
  {body}
  <div class="foot">공용 기기에서는 인증 완료 후 브라우저를 닫아 주세요.</div>
</main></body>
</html>"""
    return HTMLResponse(document)


def _form_body(
    *,
    csrf_token: str,
    name: str = "",
    phone: str = "",
    error: str = "",
) -> str:
    error_html = (
        f'<div class="error" role="alert">{html.escape(error)}</div>'
        if error
        else ""
    )
    return f"""
<h1>간편인증을 시작합니다</h1>
<p class="lead">고객님이 직접 정보를 확인한 뒤 국세청 홈택스와 근로복지공단 인증을 순서대로 진행합니다.</p>
{error_html}
<form class="card" method="post" action="/claim/submit" autocomplete="on">
  <input type="hidden" name="csrf_token" value="{html.escape(csrf_token, quote=True)}">
  <label for="name">이름</label>
  <input id="name" name="name" type="text" minlength="2" maxlength="50"
    value="{html.escape(name, quote=True)}" autocomplete="name" required>
  <label for="phone">휴대전화번호</label>
  <input id="phone" name="phone" type="tel" inputmode="tel" maxlength="20"
    value="{html.escape(phone, quote=True)}" autocomplete="tel"
    placeholder="010-0000-0000" required>
  <label for="resident_number">주민등록번호</label>
  <input id="resident_number" name="resident_number" type="password"
    inputmode="numeric" minlength="13" maxlength="14" autocomplete="off"
    placeholder="숫자 13자리" required>
  <div class="notice">입력한 주민등록번호와 인증 임시정보는 인증·수집 처리에만 사용하며, 작업 완료 또는 만료 시 삭제합니다.</div>
  <label class="check">
    <input name="privacy_consent" type="checkbox" value="yes" required>
    <span>개인정보 및 고유식별정보 수집·이용, 인증과 자료 수집 처리에 동의합니다.</span>
  </label>
  <label class="check">
    <input name="third_party_consent" type="checkbox" value="yes" required>
    <span>국세청 홈택스·근로복지공단 인증 및 자료 조회를 위한 제3자 제공·처리에 동의합니다.</span>
  </label>
  <button type="submit">동의하고 홈택스 인증 요청</button>
</form>"""


def _status_body(snapshot: Mapping[str, Any]) -> str:
    public = _public_snapshot(snapshot)
    message_html = (
        f'<p class="status">{html.escape(public["message"])}</p>'
        if public["message"]
        else ""
    )
    return f"""
<h1>{html.escape(public["label"])}</h1>
<p class="lead">화면을 닫아도 인증 확인과 자료 수집은 안전하게 계속됩니다.</p>
<section class="card" aria-live="polite">
  <strong>자료 수집 진행률 {public["progress"]}%</strong>
  <div class="progress" role="progressbar" aria-valuemin="0"
    aria-valuemax="100" aria-valuenow="{public["progress"]}">
    <span style="width:{public["progress"]}%"></span>
  </div>
  {message_html}
</section>"""


def _error_page(message: str, *, status_code: int) -> HTMLResponse:
    response = _page(
        title="인증 요청 확인",
        body=(
            "<h1>인증 요청을 확인해 주세요</h1>"
            f'<div class="error" role="alert">{html.escape(message)}</div>'
        ),
    )
    response.status_code = int(status_code)
    return response


def _security_headers(response: Any) -> Any:
    response.headers["Cache-Control"] = (
        "no-store, no-cache, must-revalidate, max-age=0, private"
    )
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(), payment=()"
    )
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; "
        "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    )
    response.headers["Strict-Transport-Security"] = (
        "max-age=31536000; includeSubDomains"
    )
    return response


def create_app(
    *,
    service: ClaimPublicService | None = None,
    crypto: ClaimRemoteCrypto | None = None,
    settings: ClaimPublicSettings | None = None,
) -> FastAPI:
    selected_settings = settings or ClaimPublicSettings.from_environment()
    crypto_error = ""
    if crypto is None:
        try:
            crypto = ClaimRemoteCrypto.from_environment()
        except ClaimRemoteCryptoError as exc:
            crypto_error = type(exc).__name__
    selected_service = service or _build_default_service(selected_settings)

    application = FastAPI(
        title="OASIS 고객 인증",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    application.state.claim_service = selected_service
    application.state.claim_crypto = crypto
    application.state.crypto_error = crypto_error
    application.state.settings = selected_settings
    application.add_middleware(_InviteTokenRedactionMiddleware)

    @application.middleware("http")
    async def secure_responses(request: Request, call_next: Callable[..., Any]):
        try:
            response = await call_next(request)
        except ClaimPublicGatewayError as exc:
            if request.url.path.startswith("/api/"):
                response = JSONResponse(
                    {
                        "error": exc.error_code,
                        "message": str(exc),
                    },
                    status_code=exc.status_code,
                )
            else:
                response = _error_page(
                    str(exc),
                    status_code=exc.status_code,
                )
        except Exception:
            if request.url.path.startswith("/api/"):
                response = JSONResponse(
                    {
                        "error": "CLAIM_PUBLIC_INTERNAL_ERROR",
                        "message": "요청을 처리하지 못했습니다.",
                    },
                    status_code=500,
                )
            else:
                response = _error_page(
                    "요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.",
                    status_code=500,
                )
        return _security_headers(response)

    def current_session(request: Request) -> dict[str, Any]:
        if crypto is None:
            raise ClaimPublicGatewayError(
                "현재 인증 서비스를 준비하고 있습니다.",
                status_code=503,
                error_code="CRYPTO_NOT_READY",
            )
        raw_cookie = str(
            request.cookies.get(selected_settings.cookie_name, "") or ""
        )
        if not raw_cookie:
            raise ClaimPublicGatewayError(
                "안내 메시지의 인증 링크를 먼저 열어 주세요.",
                status_code=401,
                error_code="SESSION_REQUIRED",
            )
        return _unpack_session(crypto, raw_cookie)

    @application.get("/health")
    async def health() -> JSONResponse:
        ready = crypto is not None and not isinstance(
            selected_service,
            _UnavailableClaimPublicService,
        )
        return JSONResponse(
            {"status": "ok" if ready else "not_ready"},
            status_code=200 if ready else 503,
        )

    @application.get("/c/_exchange", include_in_schema=False)
    async def exchange_invite(request: Request) -> RedirectResponse:
        if crypto is None:
            raise ClaimPublicGatewayError(
                "현재 인증 서비스를 준비하고 있습니다.",
                status_code=503,
                error_code="CRYPTO_NOT_READY",
            )
        invite_token = str(
            request.scope.get("state", {}).get("claim_invite_token", "") or ""
        )
        if not _TOKEN_PATTERN.fullmatch(invite_token):
            raise ClaimPublicGatewayError(
                "인증 링크가 올바르지 않습니다.",
                status_code=404,
                error_code="INVALID_INVITE_LINK",
            )
        try:
            invite = dict(
                await _service_call(
                    selected_service.open_invite,
                    invite_token=invite_token,
                )
                or {}
            )
        except ClaimPublicGatewayError:
            raise
        except Exception as exc:
            raise ClaimPublicGatewayError(
                "인증 링크가 만료되었거나 사용할 수 없습니다.",
                status_code=404,
                error_code="INVITE_UNAVAILABLE",
            ) from exc
        invite_id = str(invite.get("invite_id") or invite.get("id") or "")
        owner_ref = str(
            invite.get("owner_ref") or invite.get("owner_user_id") or ""
        ).strip()
        if not invite_id or len(invite_id) > 100:
            raise ClaimPublicGatewayError(
                "인증 요청을 확인하지 못했습니다.",
                status_code=404,
                error_code="INVITE_UNAVAILABLE",
            )
        if not owner_ref or len(owner_ref) > 200:
            raise ClaimPublicGatewayError(
                "인증 요청의 담당자를 확인하지 못했습니다.",
                status_code=404,
                error_code="INVITE_OWNER_UNAVAILABLE",
            )
        now = int(time.time())
        maximum_expiry = now + selected_settings.session_seconds
        expiry = min(
            _epoch(invite.get("expires_at"), fallback=maximum_expiry),
            maximum_expiry,
        )
        if expiry <= now:
            raise ClaimPublicGatewayError(
                "인증 링크의 유효시간이 지났습니다.",
                status_code=410,
                error_code="INVITE_EXPIRED",
            )
        csrf_token = crypto.generate_csrf_token()
        session_value = _pack_session(
            crypto,
            owner_ref=owner_ref,
            invite_id=invite_id,
            invite_token=invite_token,
            csrf_token=csrf_token,
            expires_at=expiry,
        )
        response = RedirectResponse("/claim", status_code=303)
        response.set_cookie(
            selected_settings.cookie_name,
            session_value,
            max_age=max(1, expiry - now),
            expires=max(1, expiry - now),
            path="/",
            secure=selected_settings.secure_cookies,
            httponly=True,
            samesite="lax",
        )
        return response

    @application.get("/claim")
    async def claim_page(request: Request) -> HTMLResponse:
        session = current_session(request)
        try:
            snapshot = dict(
                await _service_call(
                    selected_service.get_status,
                    owner_ref=session["owner_ref"],
                    invite_id=session["invite_id"],
                    invite_token=session["invite_token"],
                )
                or {}
            )
        except ClaimPublicGatewayError:
            raise
        except Exception as exc:
            raise ClaimPublicGatewayError(
                "인증 진행 상태를 확인하지 못했습니다.",
                status_code=503,
                error_code="STATUS_UNAVAILABLE",
            ) from exc
        public = _public_snapshot(snapshot)
        if public["submitted"]:
            return _page(
                title="인증 진행 상태",
                body=_status_body(public),
                refresh_seconds=None if public["complete"] else 5,
            )
        return _page(
            title="고객정보 입력",
            body=_form_body(
                csrf_token=session["csrf"],
                name=str(snapshot.get("name", "") or "")[:50],
                phone=str(snapshot.get("phone", "") or "")[:20],
            ),
        )

    @application.post("/claim/submit")
    async def submit_claim(request: Request) -> Any:
        session = current_session(request)
        content_type = str(request.headers.get("content-type", "") or "").lower()
        if not content_type.startswith("application/x-www-form-urlencoded"):
            raise ClaimPublicGatewayError(
                "요청 형식이 올바르지 않습니다.",
                status_code=415,
                error_code="INVALID_CONTENT_TYPE",
            )
        body = await request.body()
        if len(body) > MAX_FORM_BODY_BYTES:
            raise ClaimPublicGatewayError(
                "입력 내용이 너무 깁니다.",
                status_code=413,
                error_code="FORM_TOO_LARGE",
            )
        try:
            form = parse_qs(
                body.decode("utf-8"),
                keep_blank_values=True,
                strict_parsing=False,
                max_num_fields=12,
            )
        except (UnicodeDecodeError, ValueError) as exc:
            raise ClaimPublicGatewayError(
                "입력 내용을 확인하지 못했습니다.",
                error_code="INVALID_FORM",
            ) from exc

        def field(name: str) -> str:
            values = form.get(name, [])
            return str(values[-1] if values else "")

        csrf_value = field("csrf_token")
        if not hmac.compare_digest(str(session["csrf"]), csrf_value):
            raise ClaimPublicGatewayError(
                "보안 확인값이 만료되었습니다. 안내 링크를 다시 열어 주세요.",
                status_code=403,
                error_code="CSRF_FAILED",
            )

        raw_name = field("name")
        raw_phone = field("phone")
        try:
            name = _validate_name(raw_name)
            phone = _validate_phone(raw_phone)
            resident_number = _validate_resident_number(
                field("resident_number")
            )
            privacy_consent = field("privacy_consent") == "yes"
            third_party_consent = field("third_party_consent") == "yes"
            if not privacy_consent or not third_party_consent:
                raise ClaimPublicGatewayError(
                    "필수 동의 내용을 모두 확인해 주세요.",
                    error_code="CONSENT_REQUIRED",
                )
        except ClaimPublicGatewayError as exc:
            response = _page(
                title="고객정보 입력",
                body=_form_body(
                    csrf_token=session["csrf"],
                    name=raw_name[:50],
                    phone=raw_phone[:20],
                    error=str(exc),
                ),
            )
            response.status_code = exc.status_code
            return response

        try:
            await _service_call(
                selected_service.submit_customer,
                owner_ref=session["owner_ref"],
                invite_id=session["invite_id"],
                invite_token=session["invite_token"],
                name=name,
                phone=phone,
                resident_number=resident_number,
                consent_version=CONSENT_VERSION,
                consents={
                    "privacy_and_unique_identifier": privacy_consent,
                    "third_party_processing": third_party_consent,
                },
            )
        except ClaimPublicGatewayError:
            raise
        except Exception as exc:
            raise ClaimPublicGatewayError(
                "인증 요청을 시작하지 못했습니다. 잠시 후 다시 시도해 주세요.",
                status_code=503,
                error_code="SUBMIT_FAILED",
            ) from exc
        return RedirectResponse("/claim", status_code=303)

    @application.get("/api/claim/status")
    async def claim_status(request: Request) -> JSONResponse:
        session = current_session(request)
        try:
            snapshot = await _service_call(
                selected_service.get_status,
                owner_ref=session["owner_ref"],
                invite_id=session["invite_id"],
                invite_token=session["invite_token"],
            )
        except ClaimPublicGatewayError:
            raise
        except Exception as exc:
            raise ClaimPublicGatewayError(
                "인증 진행 상태를 확인하지 못했습니다.",
                status_code=503,
                error_code="STATUS_UNAVAILABLE",
            ) from exc
        return JSONResponse(_public_snapshot(snapshot))

    return application


app = create_app()
