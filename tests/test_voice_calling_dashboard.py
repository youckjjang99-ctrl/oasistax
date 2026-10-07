from voice_calling_dashboard import campaign_display, campaign_status_label, duration_label, filtered_candidates, merged_selection, safe_count, selection_limit, skipped_summary, select_current_page, row_key, default_catalog_filters, employee_label, blocked_reason_label, excluded_counts_summary


def test_selection_caps_and_merge_survive_hidden_pages():
    assert selection_limit(False) == 0
    assert selection_limit(True) == 100
    assert merged_selection(["old-page", "a", "b"], ["a", "b"], ["b", "b", "forged"]) == ["old-page", "b"]


def test_current_page_selection_replaces_prior_page_and_handles_source_rows():
    rows = [{"row_id": f"source:{i}", "company_uid": None} for i in range(100, 200)]
    assert select_current_page(rows) == [f"source:{i}" for i in range(100, 200)]
    assert len(select_current_page(rows + rows)) == 100
    assert row_key({"row_id": "source:one", "company_uid": "company:one"}) == "source:one"


def test_default_filters_include_missing_phones_and_unknown_businesses():
    defaults = default_catalog_filters()
    assert defaults["business_type"] == defaults["phone_type"] == defaults["discovery_type"] == "all"
    assert employee_label(None) == "미확인"
    assert employee_label(0) == "0명"
    assert employee_label(-1, change=True) == "-1명"


def test_catalog_block_reasons_are_specific_and_safely_allowlisted():
    assert blocked_reason_label({"blocked_reason": "IDENTITY_CONFLICT"}) == "업체 연결 충돌 확인 필요"
    assert blocked_reason_label({"blocked_reason": "TARGET_NOT_READY"}) == "발신 연락처·담당 배정 확인 필요"
    assert blocked_reason_label({"blocked_reason": "private source error"}) == "발신 조건 확인 필요"


def test_search_filters_only_current_page_and_eligible_flag():
    rows = [{"company_name": "Example A", "eligible": True}, {"company_name": "Example B", "eligible": False}]
    eligible = lambda row: row["eligible"]
    assert filtered_candidates(rows, "example a", False, eligible) == [rows[0]]
    assert filtered_candidates(rows, "", True, eligible) == [rows[0]]


def test_table_allowlist_does_not_leak_rpc_secrets():
    result = campaign_display([{"name": "Example", "status": "running", "total": 4, "queued": 2, "private": "do not render"}])[0]
    assert result["전체 요청"] == 4 and result["상태"] == "자동발신 진행"
    assert "private" not in result and "do not render" not in str(result)


def test_missing_duration_is_not_claimed_as_zero():
    assert duration_label(None) == "-"
    assert duration_label(0) == "0초"
    assert duration_label(62) == "1분 02초"
    assert safe_count(-1) == 0
    assert safe_count("invalid") == 0


def test_completed_campaign_is_not_still_displayed_as_dialing():
    row = {"status": "running", "total": 3, "completed": 2, "failed": 1}
    assert campaign_status_label(row) == "처리 완료"
    assert campaign_status_label({**row, "unknown": 1}) == "자동발신 진행"
    assert campaign_status_label({**row, "status": "cancelled"}) == "종료 · 남은 요청 취소"


def test_skipped_summary_only_uses_safe_labels_and_counts():
    result = skipped_summary([{"code": "DO_NOT_CALL", "company_uid": "private"}, {"code": "DO_NOT_CALL"}, {"code": "some secret error"}])
    assert result == "수신거부 2건 · 상태 재확인 필요 1건"
    assert "private" not in result and "secret" not in result


def test_excluded_counts_hide_untrusted_keys_and_invalid_counts():
    result = excluded_counts_summary({"DO_NOT_CALL": 2, "private error": 1, "NO_PHONE": -1})
    assert result == "수신거부 2건 · 기타 발신 조건 미충족 1건"
    assert excluded_counts_summary("private error") == ""
