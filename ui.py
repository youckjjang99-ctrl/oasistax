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
    [data-testid="stSidebar"] {
        width: min(82vw, 300px) !important;
        min-width: min(82vw, 300px) !important;
        max-width: min(82vw, 300px) !important;
    }
    [data-testid="stSidebar"] > div:first-child {
        width: 100% !important;
        padding: 0.8rem 0.75rem 1rem 0.75rem !important;
        overflow-x: hidden !important;
    }
    [data-testid="stSidebarHeader"] {
        position: sticky !important;
        top: 0 !important;
        z-index: 1002 !important;
        height: 48px !important;
        min-height: 48px !important;
        margin: -0.15rem 0 0.25rem 0 !important;
        justify-content: flex-end !important;
        pointer-events: none !important;
    }
    [data-testid="stSidebarCollapseButton"] {
        display: inline-flex !important;
        visibility: visible !important;
        opacity: 1 !important;
        pointer-events: auto !important;
        color: var(--oasis-blue-text) !important;
    }
    [data-testid="stSidebarCollapseButton"] button {
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
        gap: 3px !important;
        width: auto !important;
        min-width: 70px !important;
        height: 40px !important;
        padding: 0 10px !important;
        border: 1px solid rgba(255,255,255,0.72) !important;
        border-radius: 12px !important;
        background: rgba(255,255,255,0.96) !important;
        color: var(--oasis-blue-text) !important;
        box-shadow: 0 8px 22px rgba(0,0,0,0.18) !important;
    }
    [data-testid="stSidebarCollapseButton"] button::after {
        content: "닫기" !important;
        color: var(--oasis-blue-text) !important;
        font-size: 12px !important;
        font-weight: 850 !important;
        line-height: 1 !important;
    }
    [data-testid="stSidebarCollapseButton"] svg {
        color: var(--oasis-blue-text) !important;
        fill: var(--oasis-blue-text) !important;
    }
    [data-testid="stExpandSidebarButton"] {
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
        gap: 5px !important;
        visibility: visible !important;
        opacity: 1 !important;
        position: fixed !important;
        top: 10px !important;
        left: 10px !important;
        z-index: 1003 !important;
        width: auto !important;
        min-width: 168px !important;
        height: 42px !important;
        padding: 0 13px !important;
        border: 1px solid rgba(255,255,255,0.92) !important;
        border-radius: 12px !important;
        background: #0b5bd3 !important;
        color: #ffffff !important;
        box-shadow: 0 8px 24px rgba(8,72,166,0.28) !important;
    }
    [data-testid="stExpandSidebarButton"]::after {
        content: "사이드메뉴 열기" !important;
        color: #ffffff !important;
        font-size: 13px !important;
        font-weight: 850 !important;
        line-height: 1 !important;
    }
    [data-testid="stExpandSidebarButton"] svg {
        color: #ffffff !important;
        fill: #ffffff !important;
    }
    [data-testid="stSidebar"] img {
        width: 142px !important;
        margin: 0 auto 0.15rem auto !important;
    }
    .sidebar-brand {
        margin-bottom: 0.65rem !important;
    }
    .sidebar-brand-title {
        font-size: 21px !important;
        letter-spacing: -0.4px !important;
    }
    .sidebar-brand-sub {
        font-size: 10px !important;
        margin-top: 4px !important;
        letter-spacing: 0.55px !important;
    }
    .sidebar-user-card {
        border-radius: 13px !important;
        padding: 10px 12px !important;
    }
    .sidebar-user-card .name {
        font-size: 16px !important;
    }
    .sidebar-user-card .role {
        font-size: 12px !important;
    }
    .sidebar-section-label {
        font-size: 10px !important;
        margin: 11px 0 6px 3px !important;
        letter-spacing: 0.8px !important;
    }
    [data-testid="stSidebar"] [data-testid="stButtonGroup"] {
        width: 100% !important;
        margin-bottom: 0.45rem !important;
    }
    [data-testid="stSidebar"] [data-testid="stButtonGroup"]
    [role="radiogroup"] {
        display: grid !important;
        grid-template-columns: repeat(3, minmax(0, 1fr)) !important;
        width: 100% !important;
        gap: 5px !important;
    }
    [data-testid="stSidebar"] [data-testid="stButtonGroup"]
    [data-variant="pills"] {
        width: 100% !important;
        min-width: 0 !important;
        min-height: 36px !important;
        padding: 6px 4px !important;
        justify-content: center !important;
        border: 1px solid rgba(255,255,255,0.24) !important;
        border-radius: 10px !important;
        background: rgba(255,255,255,0.08) !important;
        color: rgba(255,255,255,0.88) !important;
        font-size: 11px !important;
        font-weight: 800 !important;
        white-space: nowrap !important;
    }
    [data-testid="stSidebar"] [data-testid="stButtonGroup"]
    [data-variant="pills"] * {
        color: inherit !important;
    }
    [data-testid="stSidebar"] [data-testid="stButtonGroup"]
    [data-variant="pills"][data-selected="true"],
    [data-testid="stSidebar"] [data-testid="stButtonGroup"]
    [data-variant="pills"][aria-pressed="true"] {
        background: #ffffff !important;
        border-color: #ffffff !important;
        color: var(--oasis-blue-text) !important;
        box-shadow: 0 7px 18px rgba(0,0,0,0.15) !important;
    }
    [data-testid="stSidebar"] div[role="radiogroup"] label {
        width: 100% !important;
        min-height: 42px !important;
        margin-bottom: 5px !important;
        padding: 9px 11px !important;
        border-radius: 12px !important;
        box-sizing: border-box !important;
    }
    [data-testid="stSidebar"] [data-testid="stRadio"],
    [data-testid="stSidebar"] [data-testid="stRadioGroup"] {
        width: 100% !important;
    }
    [data-testid="stSidebar"] [data-testid="stRadioOption"]
    > div > div:first-child > div:first-child {
        display: none !important;
    }
    [data-testid="stSidebar"] div[role="radiogroup"] label p {
        width: 100% !important;
        font-size: 14px !important;
        letter-spacing: -0.25px !important;
        white-space: nowrap !important;
    }
    [data-testid="stSidebar"] div[role="radiogroup"] label:hover {
        transform: none !important;
    }
    [data-testid="stSidebar"] hr {
        margin: 0.8rem 0 !important;
        border-color: rgba(255,255,255,0.15) !important;
    }
    [data-testid="stSidebar"] .stButton > button {
        min-height: 40px !important;
        font-size: 13px !important;
    }
    .block-container {
        padding: 1.1rem 0.75rem 5rem 0.75rem !important;
        max-width: 100% !important;
    }
    .oasis-topbar-compact {
        min-height: 62px !important;
        padding: 12px 14px 14px 14px !important;
        margin-top: 10px !important;
        margin-bottom: 10px !important;
        overflow: visible !important;
    }
    .oasis-topbar-compact .oasis-topbar-title {
        font-size: 19px !important;
        line-height: 1.3 !important;
        overflow: visible !important;
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
