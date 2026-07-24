from __future__ import annotations

import re


ALL_PROVINCES = "전국"
ALL_DISTRICTS = "전체"

# DB 발굴 화면에서는 도 단위는 시·군, 광역시는 구·군까지만 선택한다.
# 일반 시 안의 행정구(예: 수원시 영통구)는 해당 시를 선택하면 함께 수집된다.
REGION_HIERARCHY: dict[str, tuple[str, ...]] = {
    "서울특별시": (
        "종로구",
        "중구",
        "용산구",
        "성동구",
        "광진구",
        "동대문구",
        "중랑구",
        "성북구",
        "강북구",
        "도봉구",
        "노원구",
        "은평구",
        "서대문구",
        "마포구",
        "양천구",
        "강서구",
        "구로구",
        "금천구",
        "영등포구",
        "동작구",
        "관악구",
        "서초구",
        "강남구",
        "송파구",
        "강동구",
    ),
    "부산광역시": (
        "중구",
        "서구",
        "동구",
        "영도구",
        "부산진구",
        "동래구",
        "남구",
        "북구",
        "해운대구",
        "사하구",
        "금정구",
        "강서구",
        "연제구",
        "수영구",
        "사상구",
        "기장군",
    ),
    "대구광역시": (
        "중구",
        "동구",
        "서구",
        "남구",
        "북구",
        "수성구",
        "달서구",
        "달성군",
        "군위군",
    ),
    "인천광역시": (
        "중구",
        "동구",
        "미추홀구",
        "연수구",
        "남동구",
        "부평구",
        "계양구",
        "서구",
        "강화군",
        "옹진군",
    ),
    "광주광역시": ("동구", "서구", "남구", "북구", "광산구"),
    "대전광역시": ("동구", "중구", "서구", "유성구", "대덕구"),
    "울산광역시": ("중구", "남구", "동구", "북구", "울주군"),
    "세종특별자치시": (),
    "경기도": (
        "수원시",
        "성남시",
        "의정부시",
        "안양시",
        "부천시",
        "광명시",
        "평택시",
        "동두천시",
        "안산시",
        "고양시",
        "과천시",
        "구리시",
        "남양주시",
        "오산시",
        "시흥시",
        "군포시",
        "의왕시",
        "하남시",
        "용인시",
        "파주시",
        "이천시",
        "안성시",
        "김포시",
        "화성시",
        "광주시",
        "양주시",
        "포천시",
        "여주시",
        "연천군",
        "가평군",
        "양평군",
    ),
    "강원특별자치도": (
        "춘천시",
        "원주시",
        "강릉시",
        "동해시",
        "태백시",
        "속초시",
        "삼척시",
        "홍천군",
        "횡성군",
        "영월군",
        "평창군",
        "정선군",
        "철원군",
        "화천군",
        "양구군",
        "인제군",
        "고성군",
        "양양군",
    ),
    "충청북도": (
        "청주시",
        "충주시",
        "제천시",
        "보은군",
        "옥천군",
        "영동군",
        "증평군",
        "진천군",
        "괴산군",
        "음성군",
        "단양군",
    ),
    "충청남도": (
        "천안시",
        "공주시",
        "보령시",
        "아산시",
        "서산시",
        "논산시",
        "계룡시",
        "당진시",
        "금산군",
        "부여군",
        "서천군",
        "청양군",
        "홍성군",
        "예산군",
        "태안군",
    ),
    "전북특별자치도": (
        "전주시",
        "군산시",
        "익산시",
        "정읍시",
        "남원시",
        "김제시",
        "완주군",
        "진안군",
        "무주군",
        "장수군",
        "임실군",
        "순창군",
        "고창군",
        "부안군",
    ),
    "전라남도": (
        "목포시",
        "여수시",
        "순천시",
        "나주시",
        "광양시",
        "담양군",
        "곡성군",
        "구례군",
        "고흥군",
        "보성군",
        "화순군",
        "장흥군",
        "강진군",
        "해남군",
        "영암군",
        "무안군",
        "함평군",
        "영광군",
        "장성군",
        "완도군",
        "진도군",
        "신안군",
    ),
    "경상북도": (
        "포항시",
        "경주시",
        "김천시",
        "안동시",
        "구미시",
        "영주시",
        "영천시",
        "상주시",
        "문경시",
        "경산시",
        "의성군",
        "청송군",
        "영양군",
        "영덕군",
        "청도군",
        "고령군",
        "성주군",
        "칠곡군",
        "예천군",
        "봉화군",
        "울진군",
        "울릉군",
    ),
    "경상남도": (
        "창원시",
        "진주시",
        "통영시",
        "사천시",
        "김해시",
        "밀양시",
        "거제시",
        "양산시",
        "의령군",
        "함안군",
        "창녕군",
        "고성군",
        "남해군",
        "하동군",
        "산청군",
        "함양군",
        "거창군",
        "합천군",
    ),
    "제주특별자치도": ("제주시", "서귀포시"),
}

PROVINCE_ALIASES: dict[str, tuple[str, ...]] = {
    "서울특별시": ("서울특별시", "서울시"),
    "부산광역시": ("부산광역시", "부산시"),
    "대구광역시": ("대구광역시", "대구시"),
    "인천광역시": ("인천광역시", "인천시"),
    # "광주시"는 경기도 광주시와 충돌하므로 광역시의 정식 명칭만 사용한다.
    "광주광역시": ("광주광역시",),
    "대전광역시": ("대전광역시", "대전시"),
    "울산광역시": ("울산광역시", "울산시"),
    "세종특별자치시": ("세종특별자치시", "세종시"),
    "경기도": ("경기도",),
    "강원특별자치도": ("강원특별자치도", "강원도"),
    "충청북도": ("충청북도", "충북"),
    "충청남도": ("충청남도", "충남"),
    "전북특별자치도": ("전북특별자치도", "전라북도", "전북"),
    "전라남도": ("전라남도", "전남"),
    "경상북도": ("경상북도", "경북"),
    "경상남도": ("경상남도", "경남"),
    "제주특별자치도": ("제주특별자치도", "제주도"),
}

METROPOLITAN_PROVINCES = {
    "서울특별시",
    "부산광역시",
    "대구광역시",
    "인천광역시",
    "광주광역시",
    "대전광역시",
    "울산광역시",
}


def province_options() -> list[str]:
    return [ALL_PROVINCES, *REGION_HIERARCHY.keys()]


def district_options(province: str) -> list[str]:
    return [ALL_DISTRICTS, *REGION_HIERARCHY.get(province, ())]


def district_label(province: str) -> str:
    if province in METROPOLITAN_PROVINCES:
        return "구·군"
    if province == "세종특별자치시":
        return "하위 지역"
    return "시·군"


def region_query(province: str, district: str = ALL_DISTRICTS) -> str:
    if not province or province == ALL_PROVINCES:
        return ""
    if district and district != ALL_DISTRICTS:
        return f"{province} {district}"
    return province


def _normalize(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _starts_with_province(address: str, aliases: tuple[str, ...]) -> bool:
    return any(address.startswith(alias) for alias in aliases)


def matches_region(
    address: object,
    province: str,
    district: str = ALL_DISTRICTS,
) -> bool:
    if not province or province == ALL_PROVINCES:
        return True
    normalized = _normalize(address)
    if not normalized:
        return False
    aliases = PROVINCE_ALIASES.get(province, (province,))
    if not _starts_with_province(normalized, aliases):
        return False
    return (
        not district
        or district == ALL_DISTRICTS
        or district in normalized
    )


def resolve_region(address: object) -> tuple[str, str]:
    normalized = _normalize(address)
    if not normalized:
        return "", ""
    for province, aliases in PROVINCE_ALIASES.items():
        if not _starts_with_province(normalized, aliases):
            continue
        district = next(
            (
                name
                for name in REGION_HIERARCHY.get(province, ())
                if name in normalized
            ),
            "",
        )
        return province, district
    return "", ""
