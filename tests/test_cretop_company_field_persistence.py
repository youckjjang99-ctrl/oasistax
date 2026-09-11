"""Synthetic identifiers/values only; no customer files or network services."""
from io import BytesIO
from unittest.mock import Mock

import pandas as pd
import pytest

import utils


# Deliberately constructed test identifiers, never extracted from a real report.
BUSINESS = "-".join(("111", "11", "11111"))
CORPORATE = "-".join(("011111", "1111111"))
CERTIFICATIONS = (
    "벤처", "메인비즈", "이노비즈", "기업부설연구소",
    "연구개발전담부서", "특허보유", "상표", "R&D수행",
)
COMPANY_FIELDS = (
    "법인등록번호", "설립일", "설립년도", "종업원수", "상시근로자수", "기업유형", "기업규모",
)


def company(**changes):
    return {
        "업체명": "합성 검증기업", "대표자명": "합성대표", "사업자등록번호": BUSINESS,
        "법인등록번호": CORPORATE, "설립일": "2020-01-02", "설립년도": "2020",
        "종업원수": 0, "상시근로자수": 0, "기업유형": "일반법인", "기업규모": "중소기업",
        **{key: "" for key in CERTIFICATIONS}, **changes,
    }


@pytest.fixture
def local_storage(tmp_path, monkeypatch):
    path = tmp_path / "synthetic-customers.xlsx"
    monkeypatch.setattr(utils, "get_user_cumulative_db_path", lambda _: path)
    monkeypatch.setattr(utils, "find_customer_template", lambda: None)
    return path


def test_default_columns_and_download_template_include_company_fields(local_storage):
    expected = set(COMPANY_FIELDS + CERTIFICATIONS)
    assert expected <= set(utils.get_customer_db_columns())
    content = utils.make_basic_customer_template_bytes()
    headers = pd.read_excel(BytesIO(content), sheet_name="고객DB", nrows=0).columns
    assert expected <= set(headers)


def test_legacy_template_gains_canonical_fields_without_losing_custom_columns(tmp_path, monkeypatch):
    template = tmp_path / "synthetic-template.xlsx"
    pd.DataFrame(columns=["업체명", "대표자명", "검증확장열"]).to_excel(
        template, sheet_name="고객DB", index=False,
    )
    monkeypatch.setattr(utils, "find_customer_template", lambda: template)
    columns = utils.get_customer_db_columns()
    assert columns[:3] == ["업체명", "대표자명", "검증확장열"]
    assert set(COMPANY_FIELDS + CERTIFICATIONS) <= set(columns)
    assert len(columns) == len(set(columns))


def test_new_customer_round_trip_keeps_company_values_and_unknowns(local_storage):
    data = company(메인비즈="Y", 이노비즈="N")
    _, count, _, _, _ = utils.append_cretop_to_user_customer_db(
        "unused.pdf", "synthetic-owner", extracted_data=data,
    )
    assert count == 1
    saved = pd.read_excel(local_storage, sheet_name="고객DB", dtype=str).fillna("").iloc[0]
    for field in COMPANY_FIELDS:
        assert saved[field] == str(data[field])
    for field in CERTIFICATIONS:
        assert saved[field] == data[field]


@pytest.mark.parametrize("value,expected", [
    (None, ""), ("", ""), (" ", ""), ("미확인", ""), ("unknown", ""),
    (float("nan"), ""), (True, "Y"), (False, "N"),
    ("Y", "Y"), ("N", "N"), ("true", "Y"), ("false", "N"),
])
def test_certification_mapping_is_three_state(value, expected):
    data = {field: value for field in CERTIFICATIONS}
    row = utils.build_customer_row_from_cretop(data, list(CERTIFICATIONS))
    assert all(row[field] == expected for field in CERTIFICATIONS)


def test_english_certification_aliases_and_research_positive_evidence():
    row = utils.build_customer_row_from_cretop(
        {"MainBiz": True, "InnoBiz": False, "기업부설연구소": True}, list(CERTIFICATIONS),
    )
    assert row["메인비즈"] == "Y"
    assert row["이노비즈"] == "N"
    assert row["R&D수행"] == "Y"
    unknown = utils.build_customer_row_from_cretop(
        {"기업부설연구소": "N", "연구개발전담부서": ""}, list(CERTIFICATIONS),
    )
    assert unknown["R&D수행"] == ""


@pytest.mark.parametrize("unknown", [None, "", " ", "미확인", "unknown", float("nan")])
def test_reanalysis_unknowns_do_not_replace_existing_certifications(local_storage, unknown):
    existing = company(**{field: "Y" for field in CERTIFICATIONS})
    utils.append_cretop_to_user_customer_db("unused.pdf", "synthetic-owner", extracted_data=existing)
    incoming = company(**{field: unknown for field in CERTIFICATIONS})
    ok, _, _ = utils.refresh_existing_customer_from_cretop("synthetic-owner", incoming)
    assert ok
    saved = pd.read_excel(local_storage, sheet_name="고객DB", dtype=str).fillna("").iloc[0]
    assert all(saved[field] == "Y" for field in CERTIFICATIONS)


def test_reanalysis_explicit_negative_updates_and_blank_company_values_preserve(local_storage):
    existing = company(**{field: "Y" for field in CERTIFICATIONS})
    utils.append_cretop_to_user_customer_db("unused.pdf", "synthetic-owner", extracted_data=existing)
    incoming = company(**{field: "" for field in COMPANY_FIELDS},
                       **{field: False for field in CERTIFICATIONS})
    ok, _, _ = utils.refresh_existing_customer_from_cretop(
        "synthetic-owner", incoming, reviewed_fields=COMPANY_FIELDS,
    )
    assert ok
    saved = pd.read_excel(local_storage, sheet_name="고객DB", dtype=str).fillna("").iloc[0]
    assert all(saved[field] == "N" for field in CERTIFICATIONS)
    assert all(saved[field] == str(existing[field]) for field in COMPANY_FIELDS)


def test_reviewed_updates_to_canonical_fields_persist(local_storage):
    utils.append_cretop_to_user_customer_db("unused.pdf", "synthetic-owner", extracted_data=company())
    incoming = company(기업유형="외감법인", 기업규모="중견기업", 종업원수=7, 상시근로자수=7,
                       설립일="2021-02-03", 설립년도="2021")
    ok, _, _ = utils.refresh_existing_customer_from_cretop(
        "synthetic-owner", incoming, reviewed_fields=COMPANY_FIELDS,
    )
    assert ok
    saved = pd.read_excel(local_storage, sheet_name="고객DB", dtype=str).fillna("").iloc[0]
    assert all(saved[field] == str(incoming[field]) for field in COMPANY_FIELDS)


def test_corporate_identifier_leading_zero_survives_an_unrelated_refresh(local_storage):
    data = company(법인등록번호=CORPORATE.replace("-", ""))
    utils.append_cretop_to_user_customer_db("unused.pdf", "synthetic-owner", extracted_data=data)
    incoming = {"업체명": data["업체명"], "사업자등록번호": BUSINESS, "종업원수": 8}
    ok, _, _ = utils.refresh_existing_customer_from_cretop("synthetic-owner", incoming)
    assert ok
    saved = pd.read_excel(local_storage, sheet_name="고객DB", dtype=str).fillna("").iloc[0]
    assert saved["법인등록번호"] == data["법인등록번호"]


@pytest.mark.parametrize("unknown", [None, "", " ", float("nan"), pd.NaT, pd.NA])
def test_unknown_scalars_cannot_erase_existing_company_or_financial_values(local_storage, unknown):
    existing = company(종업원수=12, 상시근로자수=12, 매출액=320, 영업이익=20)
    utils.append_cretop_to_user_customer_db("unused.pdf", "synthetic-owner", extracted_data=existing)
    fields = COMPANY_FIELDS + ("매출액", "영업이익")
    incoming = {"사업자등록번호": BUSINESS, "업체명": existing["업체명"],
                **{field: unknown for field in fields}}
    ok, _, _ = utils.refresh_existing_customer_from_cretop(
        "synthetic-owner", incoming, reviewed_fields=COMPANY_FIELDS,
    )
    assert ok
    saved = pd.read_excel(local_storage, sheet_name="고객DB", dtype=str).fillna("").iloc[0]
    assert all(saved[field] == str(existing[field]) for field in fields)


def test_explicit_zero_updates_employee_alias_and_financial_values(local_storage):
    existing = company(종업원수=12, 상시근로자수=12, 매출액=320)
    utils.append_cretop_to_user_customer_db("unused.pdf", "synthetic-owner", extracted_data=existing)
    incoming = {"사업자등록번호": BUSINESS, "업체명": existing["업체명"], "종업원수": 0, "매출액": 0}
    ok, _, _ = utils.refresh_existing_customer_from_cretop("synthetic-owner", incoming)
    assert ok
    saved = pd.read_excel(local_storage, sheet_name="고객DB", dtype=str).fillna("").iloc[0]
    assert all(saved[field] == "0" for field in ("종업원수", "상시근로자수", "매출액"))


def test_missing_value_check_does_not_truth_test_container_results():
    data = {"synthetic-list": [1, 2], "synthetic-object": {"fixture": 1}}
    assert utils.build_customer_row_from_cretop(data, list(data)) == data
    assert not utils._is_blank_cumulative_value(data["synthetic-list"])
    assert not utils._is_blank_cumulative_value(data["synthetic-object"])


def test_review_save_passes_canonical_values_to_local_and_cloud_mapping(local_storage, monkeypatch):
    import enterprise_documents
    import enterprise_registration_ui as ui

    cloud = Mock(return_value=(True, "synthetic-ok"))
    monkeypatch.setattr(ui, "sync_customer_snapshot", cloud)
    monkeypatch.setattr(ui, "save_customer_snapshot", Mock())
    monkeypatch.setattr(enterprise_documents, "register_existing_enterprise_document", Mock())
    data = company(메인비즈="Y", 이노비즈="N")
    prepared, error = ui.prepare_registration_data(data, {field: data[field] for field in COMPANY_FIELDS})
    assert not error
    result = ui.save_reviewed_registration("synthetic-owner", "synthetic-manager", "unused.pdf", prepared)
    assert result["saved"]
    transmitted = cloud.call_args.args[1]
    saved = pd.read_excel(local_storage, sheet_name="고객DB", dtype=str).fillna("").iloc[0]
    for field in COMPANY_FIELDS + CERTIFICATIONS:
        assert saved[field] == str(transmitted[field])
