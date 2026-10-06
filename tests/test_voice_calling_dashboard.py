from voice_calling_dashboard import campaign_display, campaign_status_label, duration_label, filtered_candidates, merged_selection, safe_count, selection_limit, skipped_summary


def test_selection_caps_and_merge_survive_hidden_pages():
    assert selection_limit(False) == 30
    assert selection_limit(True) == 100
    assert merged_selection(["old-page", "a", "b"], ["a", "b"], ["b", "b", "forged"]) == ["old-page", "b"]


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
