import streamlit as st


def apply_oasis_ui():
    """OASIS v3.1.3 UI override styles.

    기존 기능 로직은 건드리지 않고, 사이드바 로고/메뉴/카드 표시만 보정한다.
    """
    st.markdown(
        """
<style>
/* ================= v3.1.3 OASIS UI polish ================= */
:root {
    --oasis-sidebar-dark: #052a67;
    --oasis-sidebar-mid: #0649ad;
    --oasis-sidebar-bright: #0b63df;
    --oasis-white: #ffffff;
    --oasis-menu-active: #ffffff;
    --oasis-menu-text: rgba(255,255,255,0.94);
    --oasis-menu-muted: rgba(255,255,255,0.62);
    --oasis-blue-text: #063b91;
}

[data-testid="stSidebar"] {
    background:
        radial-gradient(circle at 22% 0%, rgba(67,142,255,0.35), transparent 28%),
        linear-gradient(180deg, var(--oasis-sidebar-dark) 0%, var(--oasis-sidebar-mid) 54%, var(--oasis-sidebar-bright) 100%) !important;
}

[data-testid="stSidebar"] > div:first-child {
    padding: 1rem 0.9rem 1.1rem 0.9rem !important;
}

/* 사이드바 로고: 파란 배경에서 선명하게 보이도록 흰색화 + 확대 */
[data-testid="stSidebar"] img {
    width: 220px !important;
    max-width: 96% !important;
    height: auto !important;
    display: block !important;
    margin: 0.2rem auto 0.35rem auto !important;
    filter: brightness(0) invert(1) drop-shadow(0 14px 26px rgba(0,0,0,0.18)) !important;
    opacity: 1 !important;
}

.sidebar-brand {
    text-align: center !important;
    color: #fff !important;
    margin: 0 0 0.9rem 0 !important;
}
.sidebar-brand-title {
    font-size: 25px !important;
    font-weight: 950 !important;
    color: #fff !important;
    letter-spacing: -0.7px !important;
    line-height: 1.18 !important;
    text-shadow: 0 10px 22px rgba(0,0,0,0.16) !important;
}
.sidebar-brand-sub {
    font-size: 13px !important;
    color: rgba(255,255,255,0.92) !important;
    margin-top: 7px !important;
    letter-spacing: 0.8px !important;
    font-weight: 800 !important;
}

.sidebar-user-card {
    background: rgba(255,255,255,0.13) !important;
    border: 1px solid rgba(255,255,255,0.20) !important;
    border-radius: 16px !important;
    padding: 12px 14px !important;
    color: #fff !important;
}
.sidebar-user-card .name {
    font-size: 18px !important;
    color: #fff !important;
}
.sidebar-user-card .role {
    color: rgba(255,255,255,0.86) !important;
}
.sidebar-section-label {
    color: rgba(255,255,255,0.65) !important;
    font-size: 12px !important;
    font-weight: 900 !important;
    margin: 14px 0 8px 6px !important;
    letter-spacing: 1.1px !important;
}

/* Streamlit 기본 radio 아이콘 숨기고, 흰색 채워진 원을 직접 표시 */
[data-testid="stSidebar"] div[role="radiogroup"] label {
    position: relative !important;
    display: flex !important;
    align-items: center !important;
    gap: 0 !important;
    min-height: 44px !important;
    padding: 10px 14px !important;
    margin: 0 0 7px 0 !important;
    border-radius: 14px !important;
    border: 1px solid rgba(255,255,255,0.12) !important;
    background: rgba(255,255,255,0.055) !important;
    box-shadow: none !important;
    transition: transform 0.14s ease, background 0.14s ease, box-shadow 0.14s ease !important;
}

/* v3.1.3: 기본 radio 동그라미만 사용한다. 커스텀 원형은 제거하여 중복 표시를 없앤다. */
[data-testid="stSidebar"] div[role="radiogroup"] label input {
    accent-color: #ffffff !important;
}

[data-testid="stSidebar"] div[role="radiogroup"] label > div:first-child {
    margin-right: 10px !important;
}

[data-testid="stSidebar"] div[role="radiogroup"] label:hover {
    background: rgba(255,255,255,0.14) !important;
    transform: translateX(3px) !important;
}

[data-testid="stSidebar"] div[role="radiogroup"] label:has(input:checked) {
    background: #ffffff !important;
    border-color: #ffffff !important;
    box-shadow: 0 16px 32px rgba(0,0,0,0.19) !important;
}


[data-testid="stSidebar"] div[role="radiogroup"] label p {
    font-size: 16px !important;
    font-weight: 880 !important;
    letter-spacing: -0.35px !important;
    color: var(--oasis-menu-text) !important;
    line-height: 1.2 !important;
}

[data-testid="stSidebar"] div[role="radiogroup"] label:has(input:checked) p {
    color: var(--oasis-blue-text) !important;
    font-weight: 950 !important;
}

[data-testid="stSidebar"] .stButton > button {
    background: rgba(255,255,255,0.12) !important;
    color: #fff !important;
    border: 1px solid rgba(255,255,255,0.18) !important;
    box-shadow: none !important;
}

/* 홈 카드: 더 업무 시스템처럼 차분한 카드감 */
.hero {
    border-radius: 22px !important;
    box-shadow: 0 24px 58px rgba(7, 59, 145, 0.27) !important;
}
.point-card, .metric-card, .oasis-card, .preview-box {
    border-radius: 16px !important;
    box-shadow: 0 16px 38px rgba(15,55,125,0.075) !important;
}
.point-icon {
    color: #0b5bd3 !important;
}
.oasis-footer {
    color: #93a4ba !important;
}

/* v3.2.1: 메뉴 전환 시 Streamlit 기본 재실행 페이드 체감 완화 */
[data-testid="stAppViewContainer"],
[data-testid="stAppViewBlockContainer"],
[data-testid="stSidebar"],
[data-testid="stHeader"],
.main,
.block-container {
    opacity: 1 !important;
    transition: none !important;
}
[data-testid="stDecoration"],
[data-testid="stStatusWidget"],
.stDeployButton {
    display: none !important;
    visibility: hidden !important;
}

/* 단계형 업무 화면과 공통 컴포넌트 */
.oasis-topbar-compact {
    justify-content: flex-start !important;
    min-height: 64px !important;
    padding: 8px 2px 12px 2px !important;
    border-bottom: 1px solid #e2e8f0 !important;
    margin-bottom: 18px !important;
}
.oasis-topbar-compact .oasis-topbar-title {
    font-size: 22px !important;
}

[data-testid="stMetric"] {
    background: #ffffff !important;
    border: 1px solid #e2e8f0 !important;
    border-radius: 14px !important;
    padding: 14px 16px !important;
    box-shadow: 0 6px 18px rgba(15, 55, 125, 0.055) !important;
}

div[data-baseweb="tab-list"] {
    gap: 6px !important;
    overflow-x: auto !important;
    scrollbar-width: thin !important;
}
button[data-baseweb="tab"] {
    min-width: max-content !important;
    min-height: 44px !important;
}

[data-testid="stButton"] button,
[data-testid="stDownloadButton"] button,
[data-testid="stFormSubmitButton"] button {
    min-height: 44px !important;
    border-radius: 12px !important;
    font-weight: 750 !important;
}

/* 모바일에서는 여러 열을 세로 흐름으로 전환하고 조작 영역을 넓힌다. */
@media (max-width: 768px) {
    .block-container {
        padding: 0.7rem 0.75rem 5rem 0.75rem !important;
        max-width: 100% !important;
    }
    .oasis-topbar-compact {
        min-height: 50px !important;
        margin-bottom: 10px !important;
    }
    .oasis-topbar-compact .oasis-topbar-title {
        font-size: 19px !important;
    }
    .oasis-topbar-compact .oasis-topbar-sub {
        font-size: 11px !important;
        margin-top: 3px !important;
    }
    .hero {
        padding: 24px 20px !important;
        border-radius: 18px !important;
    }
    .hero-title {
        font-size: 1.8rem !important;
        line-height: 1.25 !important;
    }
    [data-testid="stHorizontalBlock"] {
        flex-wrap: wrap !important;
        gap: 0.65rem !important;
    }
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
        flex: 1 1 100% !important;
        width: 100% !important;
        min-width: 0 !important;
    }
    [data-testid="stMetric"] {
        padding: 12px 14px !important;
    }
    div[data-baseweb="tab-list"] {
        flex-wrap: nowrap !important;
        padding-bottom: 4px !important;
    }
    button[data-baseweb="tab"] {
        padding-left: 13px !important;
        padding-right: 13px !important;
    }
    [data-testid="stDataFrame"],
    [data-testid="stDataEditor"] {
        overflow-x: auto !important;
    }
}

</style>
        """,
        unsafe_allow_html=True,
    )
