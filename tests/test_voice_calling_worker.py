import json

import pytest

from voice_calling import VoiceSettings
from voice_calling_provider import UnknownCall
from voice_calling_worker import dispatch_once, main, run_dispatch_loop


def settings():
    return VoiceSettings(enabled=True, twilio_account_sid="AC" + "1" * 32,
                         twilio_auth_token="x" * 32, caller_id="+82" + "10" + "0" * 8,
                         public_base_url="https://voice.example.test", openai_api_key="test-only",
                         stream_ticket_secret="s" * 32)


class Repo:
    def __init__(self, broken_action=None):
        self.broken_action = broken_action
        self.actions = []
    def worker(self, action, payload):
        self.actions.append(action)
        if action == self.broken_action:
            raise RuntimeError("private authorization and customer payload")
        if action == "claim":
            return {"ok": True, "job": {"id": "synthetic-job"}}
        return {"ok": True}


class Carrier:
    def __init__(self, uncertain=False, hangup_failed=False):
        self.calls, self.hangups = 0, 0
        self.uncertain, self.hangup_failed = uncertain, hangup_failed
    def start_call(self, job):
        self.calls += 1
        if self.uncertain:
            raise UnknownCall("private provider error")
        return {"provider_call_id": "CA" + "1" * 32}
    def hangup(self, call_id):
        self.hangups += 1
        if self.hangup_failed:
            raise RuntimeError("private hangup response")


def test_claim_exception_does_not_dispatch_or_leak():
    carrier = Carrier()
    result = dispatch_once(settings(), Repo("claim"), carrier)
    assert result == {"ok": False, "code": "QUEUE_UNAVAILABLE"}
    assert carrier.calls == 0


def test_recheck_exception_never_dispatches():
    carrier, repo = Carrier(), Repo("get_job")
    assert dispatch_once(settings(), repo, carrier)["code"] == "TARGET_CHANGED"
    assert carrier.calls == 0 and repo.actions[-1] == "mark_failed"


@pytest.mark.parametrize("hangup_failed", [False, True])
def test_storage_exception_after_known_call_attempts_hangup_and_marks_unknown(hangup_failed):
    carrier, repo = Carrier(hangup_failed=hangup_failed), Repo("mark_dispatched")
    result = dispatch_once(settings(), repo, carrier)
    assert result == {"ok": False, "code": "DISPATCH_UNKNOWN"}
    assert carrier.calls == 1 and carrier.hangups == 1
    assert repo.actions[-1] == "mark_unknown"


def test_mark_unknown_failure_never_retries_uncertain_call():
    carrier, repo = Carrier(uncertain=True), Repo("mark_unknown")
    assert dispatch_once(settings(), repo, carrier) == {"ok": False, "code": "DISPATCH_UNKNOWN"}
    assert carrier.calls == 1


def test_untrusted_queue_code_never_printed():
    class UnexpectedRepo:
        def worker(self, action, payload):
            return {"ok": True, "code": "private customer content"}
    carrier = Carrier()
    result = dispatch_once(settings(), UnexpectedRepo(), carrier)
    assert result["code"] == "QUEUE_RESPONSE_UNEXPECTED"
    assert carrier.calls == 0


class StopLoop(BaseException):
    pass


def test_loop_survives_errors_and_backoff_is_bounded_without_log_spam():
    waits, logs = [], []
    def broken():
        raise RuntimeError("never print this private value")
    def sleep(delay):
        waits.append(delay)
        if len(waits) >= 8:
            raise StopLoop()
    with pytest.raises(StopLoop):
        run_dispatch_loop(dispatch=broken, sleep=sleep, emit=logs.append, clock=lambda: 1.0)
    assert waits == [10, 20, 40, 60, 60, 60, 60, 60]
    assert logs == ["WORKER_UNAVAILABLE"]


def test_loop_recovery_resets_backoff_and_reports_state_change():
    results = iter([{"ok": False, "code": "QUEUE_UNAVAILABLE"},
                    {"ok": False, "code": "QUEUE_UNAVAILABLE"},
                    {"ok": True, "code": "EMPTY"}])
    waits, logs = [], []
    def sleep(delay):
        waits.append(delay)
        if len(waits) == 3:
            raise StopLoop()
    with pytest.raises(StopLoop):
        run_dispatch_loop(dispatch=lambda: next(results), sleep=sleep, emit=logs.append, clock=lambda: 0.0)
    assert waits == [10, 20, 10]
    assert logs == ["QUEUE_UNAVAILABLE", "EMPTY"]


def test_once_does_not_sleep_and_sanitizes_arbitrary_codes():
    logs = []
    def no_sleep(delay):
        pytest.fail("--once must not wait")
    run_dispatch_loop(once=True, dispatch=lambda: {"ok": True, "code": "private result"},
                      sleep=no_sleep, emit=logs.append)
    assert logs == ["QUEUE_RESPONSE_UNEXPECTED"]


def test_unchanged_status_prints_only_every_five_minutes():
    times = iter([0.0, 100.0, 301.0])
    waits, logs = [], []
    def sleep(delay):
        waits.append(delay)
        if len(waits) == 3:
            raise StopLoop()
    with pytest.raises(StopLoop):
        run_dispatch_loop(dispatch=lambda: {"ok": True, "code": "EMPTY"}, sleep=sleep,
                          clock=lambda: next(times), emit=logs.append)
    assert logs == ["EMPTY", "EMPTY"]


def test_check_only_never_claims_jobs(monkeypatch, capsys):
    import voice_calling_worker
    def forbidden():
        pytest.fail("Diagnostics cannot dispatch")
    monkeypatch.setattr(voice_calling_worker, "dispatch_once", forbidden)
    monkeypatch.setattr(VoiceSettings, "from_environment", lambda: VoiceSettings())
    main(["--check-only"])
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "configuration_only" and not result["actual_calls_started"]


def test_network_flag_alone_cannot_enable_worker():
    with pytest.raises(SystemExit) as error:
        main(["--check-network"])
    assert error.value.code == 2
