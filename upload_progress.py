"""Small, synchronous upload feedback; never infer that a save succeeded.

The card is emitted before any file/network work. Terminal feedback can survive
Streamlit's next rerun; only explicit completion may produce a success state.
No customer document content or credentials are stored in this UI state.
"""
from __future__ import annotations

from html import escape
from time import monotonic
from typing import Any

import streamlit as st


_PREFIX = "_oasis_upload_feedback:"
_STYLES = """
<style>
.oasis-upload-card{padding:18px 20px;margin:12px 0 16px;border:1px solid #cbdcf5;
 border-radius:16px;background:linear-gradient(120deg,#f1f7ff,#fff);color:#183354;
 box-shadow:0 6px 20px rgba(20,65,120,.05);overflow-wrap:anywhere}
.oasis-upload-head{display:flex;align-items:center;gap:10px;font-weight:750;font-size:1rem}
.oasis-upload-icon{display:inline-flex;align-items:center;justify-content:center;
 width:25px;height:25px;flex-shrink:0;border-radius:50%;background:#0b5bd3;color:white}
.oasis-upload-card[data-state=running] .oasis-upload-icon{width:20px;height:20px;
 background:transparent;border:3px solid #c5d9f9;border-top-color:#0b5bd3;
 animation:oasis-upload-spin 1s linear infinite}
.oasis-upload-detail{margin:8px 0 0 35px;font-size:.9rem;color:#53657e;line-height:1.6}
.oasis-upload-steps{display:flex;flex-wrap:wrap;gap:6px;margin-top:14px}
.oasis-upload-step{border-radius:7px;padding:4px 8px;background:#eaf0f8;
 color:#64748b;font-size:.79rem;line-height:1.5}
.oasis-upload-step.active{background:#0b5bd3;color:white;font-weight:700}
.oasis-upload-step.done{background:#e5f4ec;color:#17633c}
.oasis-upload-card[data-state=complete]{background:#f2faf5;border-color:#baddc7}
.oasis-upload-card[data-state=complete] .oasis-upload-icon{background:#177447}
.oasis-upload-card[data-state=warning]{background:#fffbef;border-color:#e8d7a3}
.oasis-upload-card[data-state=warning] .oasis-upload-icon{background:#9a5b08}
.oasis-upload-card[data-state=error]{background:#fff5f5;border-color:#edc7cc}
.oasis-upload-card[data-state=error] .oasis-upload-icon{background:#bd3547}
.oasis-upload-ready{margin:8px 0 12px;padding:10px 14px;border:1px solid #dce5f0;
 border-radius:10px;color:#52627a;background:#f8fafc;font-size:.86rem}
@keyframes oasis-upload-spin{to{transform:rotate(360deg)}}
@media(prefers-reduced-motion:reduce){.oasis-upload-card[data-state=running] .oasis-upload-icon{animation:none}}
@media(max-width:600px){.oasis-upload-card{padding:15px}.oasis-upload-detail{margin-left:0}.oasis-upload-step{font-size:.76rem}}
</style>
"""


def _card(message: str, detail: str, state: str, steps=(), active: str = "") -> str:
    state = state if state in {"running", "complete", "warning", "error"} else "error"
    icon = {"running": "", "complete": "✓", "warning": "!", "error": "!"}[state]
    active_index = steps.index(active) if active in steps else -1
    chips = []
    for index, label in enumerate(steps):
        style = "done" if state == "complete" or (active_index >= 0 and index < active_index) else ""
        if label == active and state == "running":
            style = "active"
        chips.append(f'<span class="oasis-upload-step {style}">{index + 1}. {escape(str(label))}</span>')
    return (
        f'<div class="oasis-upload-card" data-state="{state}" role="status" aria-live="polite" aria-atomic="true">'
        f'<div class="oasis-upload-head"><span class="oasis-upload-icon" aria-hidden="true">{icon}</span>'
        f'<span>{escape(str(message))}</span></div>'
        + (f'<div class="oasis-upload-detail">{escape(str(detail))}</div>' if detail else "")
        + (f'<div class="oasis-upload-steps">{"".join(chips)}</div>' if chips else "")
        + '</div>'
    )


def remember_upload_feedback(key: str, message: str, detail: str = "", *, state="complete", ui=st) -> None:
    """Save a scoped, display-only outcome. No uploaded bytes belong here."""
    if state not in {"complete", "warning", "error"}:
        raise ValueError("Only terminal upload feedback can be remembered")
    ui.session_state[_PREFIX + key] = {"message": str(message), "detail": str(detail), "state": state}


def render_upload_feedback(key: str, *, ui=st) -> None:
    """Display the last explicit outcome once after a workflow-triggered rerun."""
    result = ui.session_state.pop(_PREFIX + key, None)
    if isinstance(result, dict):
        ui.markdown(_STYLES + _card(result.get("message", ""), result.get("detail", ""), result.get("state", "error")), unsafe_allow_html=True)


def render_upload_ready(uploaded: Any, *, action_hint="아래 분석·등록 버튼을 눌러 진행하세요.", ui=st) -> None:
    """Browser transfer is complete, but analysis/registration has not started."""
    if uploaded is None:
        return
    files = uploaded if isinstance(uploaded, (list, tuple)) else [uploaded]
    if not files:
        return
    # Don't reread large audio files just to display their size.
    size = sum(int(getattr(item, "size", 0) or 0) for item in files)
    size_label = f" · {size / 1024 / 1024:.1f}MB" if size else ""
    label = f"파일 {len(files)}개 준비됨{size_label} · {action_hint}"
    ui.markdown(_STYLES + f'<div class="oasis-upload-ready" role="status">{escape(label)}</div>', unsafe_allow_html=True)


class UploadProgress:
    def __init__(self, key: str, title: str, *, steps=(), ui=st):
        self.key, self.title, self.steps, self.ui = key, title, tuple(steps), ui
        self.started = monotonic()
        self.finished = False
        self.placeholder = None
        self.active = self.steps[0] if self.steps else ""

    def __enter__(self):
        self.ui.session_state.pop(_PREFIX + self.key, None)
        self.placeholder = self.ui.empty()
        self.step(self.active or "처리 준비", "작업이 끝날 때까지 이 화면을 유지해 주세요. 처리 결과를 아래에 안내합니다.")
        return self

    def step(self, label: str, detail: str = "") -> None:
        if self.finished:
            raise RuntimeError("Upload progress already finished")
        self.active = label
        self._render(f"{self.title} · {label} 중", detail, "running")

    def _render(self, message: str, detail: str, state: str) -> None:
        if self.placeholder is not None:
            self.placeholder.markdown(_STYLES + _card(message, detail, state, self.steps, self.active), unsafe_allow_html=True)

    def _finish(self, state: str, message: str, detail: str, persist: bool) -> None:
        self.finished = True
        elapsed = max(0, int(monotonic() - self.started))
        detail = f"{detail} · 처리시간 {elapsed}초" if detail else f"처리시간 {elapsed}초"
        self._render(message, detail, state)
        if persist:
            remember_upload_feedback(self.key, message, detail, state=state, ui=self.ui)

    def complete(self, message: str, detail: str = "", *, persist=False) -> None:
        self._finish("complete", message, detail, persist)

    def warning(self, message: str, detail: str = "", *, persist=False) -> None:
        self._finish("warning", message, detail, persist)

    def error(self, message: str, detail: str = "", *, persist=False) -> None:
        self._finish("error", message, detail, persist)

    def __exit__(self, exc_type, exc, traceback):
        if not self.finished:
            # Streamlit's stop/rerun inherit BaseException, not Exception.
            # They must not turn an incomplete operation into a green success.
            if exc_type is not None and issubclass(exc_type, Exception):
                self.error("처리를 완료하지 못했습니다.", "원본은 삭제하지 않았습니다. 아래 오류 안내를 확인한 뒤 다시 시도해 주세요.")
            else:
                self.warning("처리 결과를 확인해 주세요.", "저장 완료가 확인되지 않았습니다. 아래 안내를 확인해 주세요.")
        return False
