from __future__ import annotations

import inspect
from pathlib import Path

import work_inbox


ROOT = Path(__file__).resolve().parents[1]
TASK_ID = "10000000-0000-4000-8000-000000000001"
USER_ID = "owner" + "\x40" + "example.invalid"


def _install_sources(
    monkeypatch,
    *,
    automated_result=None,
    automated_summary=None,
    crm_due=None,
    sales_result=None,
):
    calls: list[tuple[str, tuple, dict]] = []

    def list_tasks(*args, **kwargs):
        calls.append(("tasks", args, kwargs))
        return automated_result or {"ok": True, "tasks": []}

    def get_summary(*args, **kwargs):
        calls.append(("summary", args, kwargs))
        return automated_summary or {"ok": True, "summary": {}}

    def get_due(*args, **kwargs):
        calls.append(("crm", args, kwargs))
        if isinstance(crm_due, Exception):
            raise crm_due
        return crm_due or {"overdue": [], "today": [], "week": []}

    def list_assignments(*args, **kwargs):
        calls.append(("sales", args, kwargs))
        return sales_result or {"ok": True, "assignments": []}

    monkeypatch.setattr(
        work_inbox.work_task_repository,
        "list_my_work_tasks",
        list_tasks,
    )
    monkeypatch.setattr(
        work_inbox.work_task_repository,
        "get_my_work_task_summary",
        get_summary,
    )
    monkeypatch.setattr(work_inbox.crm, "get_due_action_summary", get_due)
    monkeypatch.setattr(
        work_inbox.work_task_repository,
        "list_my_sales_followups",
        list_assignments,
    )
    return calls


def test_build_composes_all_three_sources_without_exposing_source_details(
    monkeypatch,
):
    private_phone = "010" + "-1111" + "-2222"
    private_business_no = "123" + "-45" + "-67890"
    database = object()
    calls = _install_sources(
        monkeypatch,
        automated_result={
            "ok": True,
            "tasks": [
                {
                    "task_id": TASK_ID,
                    "task_type": "guidance_followup",
                    "title": "fixed safe title",
                    "priority": "high",
                    "status": "pending",
                    "due_at": "2026-08-07T00:00:00Z",
                    "task_version": 2,
                    "phone": private_phone,
                }
            ],
        },
        automated_summary={
            "ok": True,
            "summary": {
                "open_count": 1,
                "overdue_count": 0,
                "today_count": 0,
                "week_count": 1,
                "in_progress_count": 0,
                "completed_today_count": 0,
            },
        },
        crm_due={
            "overdue": [
                {
                    "company_name": "CRM company",
                    "next_action": "follow up",
                    "next_date": "2026-08-04",
                    "status": "consulting",
                    "business_no": private_business_no,
                    "phone": private_phone,
                }
            ],
            "today": [],
            "week": [],
        },
        sales_result={
            "ok": True,
            "assignments": [
                {
                    "assignment_id": "50000000-0000-4000-8000-000000000001",
                    "company_name": "Sales company",
                    "next_contact_at": "2026-08-05T00:30:00Z",
                    "own_memo": "private memo",
                    "private_detail": "assignment-private-id",
                    "phone": private_phone,
                }
            ],
        },
    )

    result = work_inbox.build_work_inbox(
        USER_ID,
        db=database,
        today="2026-08-05",
    )

    assert result["ok"] is True
    assert [item["source"] for item in result["items"]] == [
        "automated",
        "crm",
        "sales",
    ]
    assert result["summary"] == {
        "open_count": 3,
        "overdue_count": 1,
        "today_count": 1,
        "week_count": 1,
        "in_progress_count": 0,
        "completed_today_count": 0,
        "upcoming_count": 0,
    }
    assert result["warnings"] == []
    serialized = repr(result)
    assert private_phone not in serialized
    assert private_business_no not in serialized
    assert "private memo" not in serialized
    assert "assignment-private-id" not in serialized

    automated, crm_item, sales_item = result["items"]
    assert set(automated) == {
        "source",
        "title",
        "category",
        "company_name",
        "status",
        "status_label",
        "priority",
        "due_at",
        "due_bucket",
        "task_id",
        "task_version",
        "task_type",
        "route",
    }
    local_keys = {
        "source",
        "title",
        "category",
        "company_name",
        "status",
        "status_label",
        "priority",
        "due_at",
        "due_bucket",
        "route",
    }
    assert set(crm_item) == local_keys
    assert set(sales_item) == local_keys

    assert calls == [
        (
            "tasks",
            (USER_ID,),
            {"limit": 500, "offset": 0, "db": database},
        ),
        ("summary", (USER_ID,), {"db": database}),
        (
            "crm",
            (USER_ID,),
            {"today": "2026-08-05"},
        ),
        (
            "sales",
            (USER_ID,),
            {
                "limit": 1000,
                "after_next_contact_at": None,
                "after_assignment_id": None,
                "db": database,
            },
        ),
    ]


def test_build_pages_all_automated_and_sales_rows(monkeypatch):
    automated_rows = [
        {
            "task_id": f"10000000-0000-4000-8000-{index:012d}",
            "task_type": "guidance_followup",
            "title": f"task {index}",
            "priority": "normal",
            "status": "pending",
            "due_at": "2026-08-05T00:00:00Z",
            "task_version": 1,
        }
        for index in range(1, 502)
    ]
    sales_rows = [
        {
            "assignment_id": f"50000000-0000-4000-8000-{index:012d}",
            "company_name": f"company {index}",
            "next_contact_at": "2026-08-05T00:00:00Z",
        }
        for index in range(1, 1002)
    ]
    automated_offsets = []
    sales_offsets = []

    def list_tasks(_user_id, *, limit, offset, db=None):
        automated_offsets.append(offset)
        return {
            "ok": True,
            "tasks": automated_rows[offset : offset + limit],
            "total_count": len(automated_rows),
        }

    def list_sales(
        _user_id,
        *,
        limit,
        after_next_contact_at,
        after_assignment_id,
        db=None,
    ):
        offset = 0
        if after_assignment_id is not None:
            offset = next(
                index
                for index, row in enumerate(sales_rows, start=1)
                if row["assignment_id"] == after_assignment_id
            )
        sales_offsets.append(offset)
        return {
            "ok": True,
            "assignments": sales_rows[offset : offset + limit],
        }

    monkeypatch.setattr(
        work_inbox.work_task_repository,
        "list_my_work_tasks",
        list_tasks,
    )
    monkeypatch.setattr(
        work_inbox.work_task_repository,
        "get_my_work_task_summary",
        lambda *_args, **_kwargs: {
            "ok": True,
            "summary": {
                "open_count": len(automated_rows),
                "overdue_count": 0,
                "today_count": len(automated_rows),
                "week_count": 0,
                "in_progress_count": 0,
                "completed_today_count": 0,
            },
        },
    )
    monkeypatch.setattr(
        work_inbox.crm,
        "get_due_action_summary",
        lambda *_args, **_kwargs: {"overdue": [], "today": [], "week": []},
    )
    monkeypatch.setattr(
        work_inbox.work_task_repository,
        "list_my_sales_followups",
        list_sales,
    )

    result = work_inbox.build_work_inbox(USER_ID, today="2026-08-05")

    assert result["ok"] is True
    assert len(result["items"]) == 1502
    assert result["summary"]["open_count"] == 1502
    assert result["summary"]["today_count"] == 1502
    assert automated_offsets == [0, 500]
    assert sales_offsets == [0, 1000]


def test_due_buckets_use_korean_calendar_boundaries(monkeypatch):
    _install_sources(
        monkeypatch,
        automated_result={
            "ok": True,
            "tasks": [
                {
                    "task_id": TASK_ID,
                    "task_type": "claim_tax_review",
                    "title": "first",
                    "priority": "normal",
                    "status": "pending",
                    "due_at": "2026-08-04T15:30:00Z",
                    "task_version": 1,
                },
                {
                    "task_id": "20000000-0000-4000-8000-000000000002",
                    "task_type": "guidance_followup",
                    "title": "second",
                    "priority": "normal",
                    "status": "pending",
                    "due_at": "2026-08-05T15:30:00Z",
                    "task_version": 1,
                },
            ],
        },
    )

    result = work_inbox.build_work_inbox(
        USER_ID,
        today="2026-08-05",
    )

    buckets = {item["title"]: item["due_bucket"] for item in result["items"]}
    assert buckets == {"first": "today", "second": "week"}
    assert result["items"][0]["due_at"].endswith("+09:00")


def test_source_failures_produce_fixed_warnings_without_raw_details(monkeypatch):
    secrets = (
        "private-auto-error",
        "private-summary-error",
        "private-crm-error",
        "private-sales-error",
    )
    _install_sources(
        monkeypatch,
        automated_result={
            "ok": False,
            "tasks": [],
            "warning": secrets[0],
        },
        automated_summary={
            "ok": False,
            "summary": {},
            "warning": secrets[1],
        },
        crm_due=RuntimeError(secrets[2]),
        sales_result={
            "ok": False,
            "assignments": [],
            "message": secrets[3],
        },
    )

    result = work_inbox.build_work_inbox(
        USER_ID,
        today="2026-08-05",
    )

    assert result["ok"] is False
    assert result["items"] == []
    assert result["warnings"] == [
        "자동 생성 업무를 불러오지 못했습니다.",
        "CRM 후속일정을 불러오지 못했습니다.",
        "영업 재연락 일정을 불러오지 못했습니다.",
    ]
    serialized = repr(result)
    for secret in secrets:
        assert secret not in serialized


def test_summary_only_failure_uses_visible_rows_and_marks_degraded(monkeypatch):
    _install_sources(
        monkeypatch,
        automated_result={
            "ok": True,
            "tasks": [
                {
                    "task_id": TASK_ID,
                    "task_type": "guidance_followup",
                    "title": "visible task",
                    "priority": "normal",
                    "status": "in_progress",
                    "due_at": "2026-08-05T00:00:00Z",
                    "task_version": 1,
                }
            ],
        },
        automated_summary={"ok": False, "summary": {}},
    )

    result = work_inbox.build_work_inbox(USER_ID, today="2026-08-05")

    assert result["ok"] is False
    assert len(result["items"]) == 1
    assert result["summary"]["open_count"] == 1
    assert result["summary"]["today_count"] == 1
    assert result["summary"]["in_progress_count"] == 1
    assert result["warnings"] == [
        "자동 생성 업무 요약을 불러오지 못했습니다."
    ]


def test_crm_only_failure_marks_inbox_degraded(monkeypatch):
    _install_sources(monkeypatch, crm_due=RuntimeError("private detail"))

    result = work_inbox.build_work_inbox(USER_ID, today="2026-08-05")

    assert result["ok"] is False
    assert result["warnings"] == ["CRM 후속일정을 불러오지 못했습니다."]


def test_crm_restore_failure_is_fixed_warning_and_degraded(monkeypatch):
    _install_sources(monkeypatch)

    result = work_inbox.build_work_inbox(
        USER_ID,
        today="2026-08-05",
        crm_restore_ok=False,
    )

    assert result["ok"] is False
    assert result["warnings"] == [
        "CRM 클라우드 복원을 확인하지 못했습니다."
    ]


def test_automated_page_overlap_is_deduplicated_and_degraded(monkeypatch):
    first_page = [
        {"task_id": f"60000000-0000-4000-8000-{index:012d}"}
        for index in range(1, 501)
    ]
    final_row = {"task_id": "60000000-0000-4000-8000-000000000501"}

    def list_tasks(_user_id, *, limit, offset, db=None):
        if offset == 0:
            return {"ok": True, "tasks": first_page, "total_count": 501}
        return {
            "ok": True,
            "tasks": [first_page[-1], final_row],
            "total_count": 501,
        }

    monkeypatch.setattr(
        work_inbox.work_task_repository,
        "list_my_work_tasks",
        list_tasks,
    )

    result = work_inbox._load_automated_tasks(USER_ID)

    task_ids = [row["task_id"] for row in result["tasks"]]
    assert result["ok"] is False
    assert len(task_ids) == len(set(task_ids)) == 501


def test_build_is_read_only_for_crm_sales_and_automated_sources():
    source = "\n".join(
        inspect.getsource(function)
        for function in (
            work_inbox.build_work_inbox,
            work_inbox._load_automated_tasks,
            work_inbox._load_sales_followups,
        )
    )

    for required_read in (
        "list_my_work_tasks(",
        "get_my_work_task_summary(",
        "get_due_action_summary(",
        "list_my_sales_followups(",
    ):
        assert required_read in source
    for forbidden_write in (
        "transition_my_work_task(",
        "start_work_task(",
        "complete_work_task(",
        "defer_work_task(",
        "record_contact(",
        "save_user_note(",
        "upsert_customer_record(",
        "sync_crm_record(",
    ):
        assert forbidden_write not in source


def test_render_changes_only_automated_tasks_and_navigates_for_source_rows():
    source = inspect.getsource(work_inbox.render_work_inbox_page)
    automated_loop = source.index("for item in automated:")
    local_section = source.index('st.markdown("#### CRM·영업 후속일정")')

    for transition in (
        "start_work_task(",
        "complete_work_task(",
        "defer_work_task(",
    ):
        position = source.index(transition)
        assert automated_loop < position < local_section
        assert transition not in source[local_section:]
    assert 'args=("기업 컨설팅",)' in source[local_section:]
    assert 'args=("DB발굴",)' in source[local_section:]
    assert "record_contact(" not in source
    assert "upsert_customer_record(" not in source


def test_app_exposes_lazy_loaded_work_inbox_route():
    source = (ROOT / "app.py").read_text(encoding="utf-8")

    assert '"업무함": "업무함"' in source
    assert 'elif active_tab == "업무함":' in source
    assert source.count("from work_inbox import render_work_inbox_page") == 1
    route = source[source.index('elif active_tab == "업무함":') :]
    route = route[: route.index("\nelif active_tab", 1)]
    assert "from work_inbox import render_work_inbox_page" in route
    assert "render_work_inbox_page(" in route
    assert "CURRENT_USER_ID" in route
    assert "CURRENT_USER_NAME" in route
    assert "navigate=_navigate_to_main_menu" in route
    assert "crm_restore_ok=crm_restore_ok" in route
    assert 'if active_tab in {"홈", "업무함"}:' in source
    assert "restore_crm_from_cloud(CURRENT_USER_ID)" in source
