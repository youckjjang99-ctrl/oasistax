import pandas as pd
from datetime import datetime
from utils import HISTORY_DIR, get_user_dirs


HISTORY_FILE = HISTORY_DIR / "실행이력.xlsx"


def _history_file(user_id=None):
    if user_id:
        return get_user_dirs(user_id)["history"] / "실행이력.xlsx"
    return HISTORY_FILE


def append_run_history(upload_file_name, result_file, status, memo="", manager_name="", user_id=None):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    history_file = _history_file(user_id)

    new_row = pd.DataFrame([{
        "실행일시": now,
        "회원ID": user_id or "",
        "담당자명": manager_name,
        "업로드파일": upload_file_name,
        "결과파일": result_file,
        "상태": status,
        "메모": memo
    }])

    if history_file.exists():
        try:
            old = pd.read_excel(history_file)
            history = pd.concat([old, new_row], ignore_index=True)
        except Exception:
            history = new_row
    else:
        history = new_row

    history.to_excel(history_file, index=False)


def read_run_history(user_id=None):
    history_file = _history_file(user_id)
    if history_file.exists():
        try:
            return pd.read_excel(history_file).sort_values("실행일시", ascending=False)
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()


def get_manager_stats(user_id=None):
    history = read_run_history(user_id)
    if history.empty or "담당자명" not in history.columns:
        return pd.DataFrame()

    success = history[history["상태"] == "성공"] if "상태" in history.columns else history

    if success.empty:
        return pd.DataFrame()

    stats = (
        success
        .groupby("담당자명", dropna=False)
        .size()
        .reset_index(name="실행횟수")
        .sort_values("실행횟수", ascending=False)
    )

    stats["담당자명"] = stats["담당자명"].fillna("미입력")
    return stats
