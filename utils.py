import os
import glob
import base64
import pandas as pd
from datetime import datetime
from pathlib import Path
from io import BytesIO


ROOT_DIR = Path(__file__).parent
TEMPLATE_DIR = ROOT_DIR / "templates"
UPLOAD_DIR = ROOT_DIR / "uploads"
RESULT_DIR = ROOT_DIR / "results"
HISTORY_DIR = ROOT_DIR / "history"
USER_DATA_DIR = ROOT_DIR / "user_data"

for folder in [TEMPLATE_DIR, UPLOAD_DIR, RESULT_DIR, HISTORY_DIR, USER_DATA_DIR]:
    folder.mkdir(exist_ok=True)


def image_to_base64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def get_logo_path():
    for path in [
        ROOT_DIR / "logo.png",
        ROOT_DIR / "logo.jpg",
        ROOT_DIR / "logo.jpeg",
        ROOT_DIR / "oasis_logo_transparent_1200.png",
        ROOT_DIR / "oasis_logo_transparent_800.png",
    ]:
        if path.exists():
            return path
    return None


def logo_html(width=240):
    logo_path = get_logo_path()
    if logo_path:
        return (
            f"<img src='data:image/png;base64,{image_to_base64(logo_path)}' "
            f"style='width:{width}px; height:auto; object-fit:contain;'/>"
        )
    return "<div style='font-size:26px; font-weight:800; color:#0b2d66;'>OASIS</div>"


def make_upload_filename(original_name):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = original_name.replace(" ", "_").replace("(", "").replace(")", "")
    return f"업로드고객DB_{ts}_{safe_name}"


def find_customer_template():
    candidates = [
        TEMPLATE_DIR / "고객DB_양식v2.xlsx",
        ROOT_DIR / "고객DB_양식v2.xlsx",
        ROOT_DIR / "고객DB.xlsx",
    ]

    for path in candidates:
        if path.exists():
            return path

    return None


def make_basic_customer_template_bytes():
    customer_columns = [
        "업체명", "대표자명", "사업자등록번호", "업종명", "사업장 소재지",
        "설립년도", "연매출", "전년도매출", "올해예상매출", "매출감소여부",
        "상시근로자수", "고용보험가입인원", "신규채용계획", "청년채용계획",
        "희망상담주제1", "희망상담주제2", "희망상담주제3",
        "희망자금용도1", "희망자금용도2", "희망자금용도3",
        "키워드메모", "주요 사업내용", "비고",
        "벤처", "메인비즈", "이노비즈", "기업부설연구소",
        "특허보유", "R&D수행", "기술제품보유", "기술인력보유",
        "기술매출발생", "스마트공장도입", "기술보증희망", "기술성메모"
    ]

    sample = pd.DataFrame([{
        "업체명": "예시기업",
        "대표자명": "홍길동",
        "업종명": "제조업",
        "사업장 소재지": "충남 천안시",
        "전년도매출": 400000000,
        "올해예상매출": 300000000,
        "매출감소여부": "Y",
        "상시근로자수": 5,
        "고용보험가입인원": 5,
        "희망상담주제1": "정책자금",
        "희망자금용도1": "운전자금",
        "키워드메모": "제조 설비투자 운전자금",
        "주요 사업내용": "제품 제조 및 판매",
        "벤처": "N",
        "메인비즈": "N",
        "이노비즈": "N",
        "기업부설연구소": "N",
        "특허보유": "N",
        "R&D수행": "N",
        "기술제품보유": "N",
        "기술인력보유": "N",
        "기술매출발생": "N",
        "스마트공장도입": "N",
        "기술보증희망": "N"
    }], columns=customer_columns)

    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        sample.to_excel(writer, sheet_name="고객DB", index=False)
        pd.DataFrame(columns=["상품명", "기관명", "대상지역", "대상업종", "추천키워드", "자금용도", "필요서류"]).to_excel(writer, sheet_name="상시정책자금DB", index=False)
        pd.DataFrame(columns=["제도명", "기관명", "대상", "최소고용인원", "추천키워드", "필요서류"]).to_excel(writer, sheet_name="고용지원금DB", index=False)
        pd.DataFrame({
            "안내": [
                "오아시스 내부용 고객DB 기본 양식입니다.",
                "파일명은 변경해도 되지만 시트명과 컬럼명은 변경하지 말아주세요.",
                "Y/N 항목은 Y 또는 N으로 입력하세요."
            ]
        }).to_excel(writer, sheet_name="사용가이드", index=False)

    output.seek(0)
    return output.getvalue()


def cleanup_old_files(folder, pattern, keep_count=30):
    files = list(Path(folder).glob(pattern))
    if len(files) <= keep_count:
        return

    files.sort(key=lambda x: x.stat().st_mtime, reverse=True)

    for old_file in files[keep_count:]:
        try:
            old_file.unlink()
        except Exception:
            pass


def run_cleanup():
    cleanup_old_files(RESULT_DIR, "매칭결과_*.xlsx", keep_count=30)
    cleanup_old_files(UPLOAD_DIR, "업로드고객DB_*.xlsx", keep_count=30)


def move_result_files_to_results(before_files):
    after_files = set(glob.glob("매칭결과_*.xlsx"))
    new_files = list(after_files - before_files)

    if not new_files:
        all_result_files = glob.glob("매칭결과_*.xlsx")
        if all_result_files:
            new_files = [max(all_result_files, key=os.path.getmtime)]

    moved_files = []
    for file in new_files:
        src = Path(file)
        dst = RESULT_DIR / src.name

        try:
            if src.exists():
                if dst.exists():
                    dst.unlink()
                src.replace(dst)
                moved_files.append(str(dst))
            elif dst.exists():
                moved_files.append(str(dst))
        except Exception:
            if src.exists():
                moved_files.append(str(src))

    return moved_files


def extract_company_previews(result_file):
    previews = {}

    try:
        xls = pd.ExcelFile(result_file)
        company_sheets = [s for s in xls.sheet_names if s.startswith("업체별_")]

        for sheet in company_sheets:
            df_raw = pd.read_excel(result_file, sheet_name=sheet, header=None)

            header_row = None
            for i in range(len(df_raw)):
                row_values = [str(v) for v in df_raw.iloc[i].tolist()]
                if "추천사업명" in row_values and "신청가능성점수" in row_values:
                    header_row = i
                    break

            if header_row is None:
                continue

            df = pd.read_excel(result_file, sheet_name=sheet, header=header_row)
            df = df.dropna(how="all")

            if "추천사업명" not in df.columns:
                continue

            cols = [
                col for col in [
                    "사업구분", "추천사업명", "기관명", "추천등급",
                    "신청가능성점수", "매칭점수", "신청판정"
                ]
                if col in df.columns
            ]

            company_name = sheet.replace("업체별_", "")
            previews[company_name] = df[cols].head(3)

    except Exception:
        pass

    return previews


# ================================
# v2.1 회원별 데이터 저장 유틸
# ================================
def safe_user_folder_name(user_id):
    import re
    user_id = str(user_id or "guest").strip().lower()
    user_id = re.sub(r"[^a-z0-9가-힣_.@-]", "_", user_id)
    return user_id[:50] or "guest"


def get_user_base_dir(user_id):
    user_dir = USER_DATA_DIR / safe_user_folder_name(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


def get_user_dirs(user_id):
    base = get_user_base_dir(user_id)
    dirs = {
        "base": base,
        "uploads": base / "uploads",
        "results": base / "results",
        "history": base / "history",
    }
    for folder in dirs.values():
        folder.mkdir(parents=True, exist_ok=True)
    return dirs


def get_user_cumulative_db_path(user_id):
    return get_user_base_dir(user_id) / "고객DB누적.xlsx"


def append_user_customer_db(uploaded_excel_path, user_id, manager_name=""):
    cumulative_path = get_user_cumulative_db_path(user_id)
    try:
        df_new = pd.read_excel(uploaded_excel_path, sheet_name="고객DB")
        df_new = df_new.dropna(how="all")
        if df_new.empty:
            return cumulative_path, 0

        df_new.insert(0, "누적저장일시", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        df_new.insert(1, "회원ID", str(user_id or ""))
        df_new.insert(2, "담당자명", str(manager_name or ""))

        if cumulative_path.exists():
            try:
                df_old = pd.read_excel(cumulative_path, sheet_name="고객DB누적")
                df_all = pd.concat([df_old, df_new], ignore_index=True, sort=False)
            except Exception:
                df_all = df_new
        else:
            df_all = df_new

        with pd.ExcelWriter(cumulative_path, engine="openpyxl") as writer:
            df_all.to_excel(writer, sheet_name="고객DB누적", index=False)

        return cumulative_path, len(df_new)
    except Exception:
        return cumulative_path, 0
