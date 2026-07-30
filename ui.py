import streamlit as st


def apply_oasis_ui():
    """OASIS 공통 디자인 시스템을 모든 업무 화면에 적용한다."""
    st.markdown(
        """
<style>
/* ================= OASIS unified design system ================= */
:root {
    --oasis-navy-950: #052452;
    --oasis-navy-900: #062b63;
    --oasis-blue-700: #0848a6;
    --oasis-blue-600: #0b5bd3;
    --oasis-blue-500: #1670eb;
    --oasis-blue-100: #eaf3ff;
    --oasis-bg: #f4f7fb;
    --oasis-surface: #ffffff;
    --oasis-surface-soft: #f8fafc;
    --oasis-text-strong: #172033;
    --oasis-text: #334155;
    --oasis-muted: #5f6f84;
    --oasis-border: #dce5f0;
    --oasis-border-strong: #c7d4e5;
    --oasis-success: #177447;
    --oasis-warning: #9a5b08;
    --oasis-danger: #c23a46;
    --oasis-focus: rgba(11, 91, 211, 0.18);
    --oasis-radius-control: 11px;
    --oasis-radius-card: 16px;
    --oasis-radius-hero: 22px;
    --oasis-shadow-card: 0 8px 24px rgba(26, 54, 93, 0.07);
    --oasis-shadow-raised: 0 18px 42px rgba(18, 62, 128, 0.13);
    --oasis-sidebar-dark: #052a67;
    --oasis-sidebar-mid: #0649ad;
    --oasis-sidebar-bright: #0b63df;
    --oasis-menu-text: rgba(255, 255, 255, 0.96);
    --oasis-menu-muted: rgba(255, 255, 255, 0.82);
}

html {
    font-size: 16px;
}

body,
.stApp,
[data-testid="stAppViewContainer"],
[data-testid="stAppViewBlockContainer"],
.main,
.block-container,
button,
input,
textarea,
select {
    font-family:
        "Pretendard",
        "Noto Sans KR",
        "Apple SD Gothic Neo",
        "Malgun Gothic",
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        sans-serif !important;
}

.stApp {
    color: var(--oasis-text);
    background:
        radial-gradient(circle at 0% 0%, rgba(58, 133, 247, 0.09), transparent 31rem),
        linear-gradient(145deg, #f7faff 0%, #ffffff 48%, #f1f6fd 100%) !important;
}

[data-testid="stAppViewContainer"],
[data-testid="stAppViewBlockContainer"],
[data-testid="stSidebar"],
[data-testid="stHeader"],
.main,
.block-container {
    opacity: 1 !important;
    transition: none !important;
}

#MainMenu,
footer,
[data-testid="stDecoration"],
[data-testid="stStatusWidget"],
.stDeployButton {
    display: none !important;
    visibility: hidden !important;
}

.block-container {
    width: 100% !important;
    max-width: 1280px !important;
    padding: 4.35rem clamp(1rem, 2.5vw, 2rem) 4rem !important;
}

p,
li,
[data-testid="stMarkdownContainer"] {
    line-height: 1.58;
}

h1,
h2,
h3,
h4,
h5,
h6,
.hero-title,
.section-title,
.login-title,
.oasis-topbar-title,
.point-title,
.metric-value {
    color: var(--oasis-text-strong);
    letter-spacing: -0.035em !important;
    line-height: 1.28 !important;
    word-break: keep-all !important;
    overflow-wrap: break-word !important;
    text-wrap: balance;
}

h1 {
    font-size: clamp(1.9rem, 2.7vw, 2.45rem) !important;
    font-weight: 800 !important;
    margin: 0.25rem 0 1rem !important;
}

h2 {
    font-size: clamp(1.55rem, 2.15vw, 1.95rem) !important;
    font-weight: 800 !important;
    margin: 0.2rem 0 0.85rem !important;
}

h3 {
    font-size: clamp(1.24rem, 1.7vw, 1.5rem) !important;
    font-weight: 700 !important;
    margin: 1.15rem 0 0.7rem !important;
}

h4 {
    font-size: 1.08rem !important;
    font-weight: 700 !important;
    margin: 1rem 0 0.6rem !important;
}

[data-testid="stCaptionContainer"],
[data-testid="stCaptionContainer"] p,
.oasis-footer {
    color: var(--oasis-muted) !important;
    font-size: 0.81rem !important;
    line-height: 1.55 !important;
}

/* Header, hero and cards */
.oasis-topbar,
.oasis-topbar-compact {
    display: flex !important;
    align-items: center !important;
    justify-content: flex-start !important;
    min-height: 58px !important;
    margin: 0 0 1rem !important;
    padding: 0.7rem 1rem !important;
    border: 1px solid rgba(215, 226, 240, 0.9) !important;
    border-radius: 14px !important;
    background: rgba(255, 255, 255, 0.9) !important;
    box-shadow: 0 5px 18px rgba(24, 58, 105, 0.055) !important;
    backdrop-filter: blur(8px);
}

.oasis-topbar-compact .oasis-topbar-title {
    color: var(--oasis-navy-900) !important;
    font-size: clamp(1.08rem, 1.7vw, 1.32rem) !important;
    font-weight: 800 !important;
}

.oasis-topbar-compact .oasis-topbar-sub {
    margin-top: 0.15rem !important;
    color: var(--oasis-muted) !important;
    font-size: 0.78rem !important;
    font-weight: 600 !important;
    letter-spacing: 0.01em;
}

.hero {
    position: relative;
    overflow: hidden;
    margin: 0 0 1.25rem !important;
    padding: clamp(2rem, 4.2vw, 3rem) clamp(1.75rem, 4.5vw, 3.4rem) !important;
    color: #ffffff !important;
    border: 1px solid rgba(255, 255, 255, 0.15) !important;
    border-radius: var(--oasis-radius-hero) !important;
    background: linear-gradient(135deg, #073b91 0%, #0b55c4 56%, #1b78ff 100%) !important;
    box-shadow: 0 18px 44px rgba(7, 59, 145, 0.22) !important;
}

.hero::after {
    content: "";
    position: absolute;
    top: -6rem;
    right: -4rem;
    width: 18rem;
    height: 18rem;
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.1);
    pointer-events: none;
}

.hero-title {
    position: relative;
    z-index: 1;
    max-width: 780px;
    margin: 0 0 0.75rem !important;
    color: #ffffff !important;
    font-size: clamp(2rem, 4vw, 2.75rem) !important;
    font-weight: 800 !important;
}

.hero-sub {
    position: relative;
    z-index: 1;
    max-width: 780px;
    color: rgba(255, 255, 255, 0.92) !important;
    font-size: clamp(0.95rem, 1.6vw, 1.08rem) !important;
    line-height: 1.7 !important;
    word-break: keep-all;
}

.badge {
    position: relative;
    z-index: 1;
    display: inline-block;
    margin-bottom: 0.85rem;
    padding: 0.42rem 0.78rem;
    color: #ffffff;
    border: 1px solid rgba(255, 255, 255, 0.31);
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.13);
    font-size: 0.75rem;
    font-weight: 700;
    letter-spacing: 0.035em;
}

.oasis-card,
.preview-box,
.point-card,
.metric-card,
.oasis-section-card,
.oasis-question-card {
    border: 1px solid var(--oasis-border) !important;
    border-radius: var(--oasis-radius-card) !important;
    background: rgba(255, 255, 255, 0.96) !important;
    box-shadow: var(--oasis-shadow-card) !important;
}

.oasis-card {
    padding: clamp(1.25rem, 3vw, 2rem) !important;
}

.preview-box {
    margin-top: 1.25rem !important;
    padding: clamp(1rem, 2.5vw, 1.5rem) !important;
}

.point-card {
    min-height: 132px !important;
    padding: 1.15rem !important;
}

.point-icon {
    margin-bottom: 0.65rem !important;
    color: var(--oasis-blue-600) !important;
    font-size: 1.45rem !important;
    line-height: 1 !important;
}

.point-title,
.section-title {
    color: var(--oasis-navy-900) !important;
    font-weight: 800 !important;
}

.point-title {
    margin-bottom: 0.42rem !important;
    font-size: 1.06rem !important;
}

.point-desc,
.section-desc {
    color: var(--oasis-muted) !important;
    line-height: 1.6 !important;
}

.point-desc {
    margin: 0 !important;
    font-size: 0.88rem !important;
}

.section-title {
    margin-bottom: 0.55rem !important;
    font-size: 1.4rem !important;
}

.section-desc {
    margin-bottom: 1rem !important;
    font-size: 0.93rem !important;
}

.metric-card {
    padding: 1rem 1.1rem !important;
}

.metric-title {
    margin-bottom: 0.35rem !important;
    color: var(--oasis-muted) !important;
    font-size: 0.8rem !important;
    font-weight: 700 !important;
}

.metric-value {
    color: var(--oasis-navy-900) !important;
    font-size: 1.7rem !important;
    font-weight: 800 !important;
}

/* Streamlit metrics */
[data-testid="stMetric"] {
    min-height: 108px !important;
    padding: 0.95rem 1.05rem !important;
    border: 1px solid var(--oasis-border) !important;
    border-radius: 14px !important;
    background: #ffffff !important;
    box-shadow: 0 5px 18px rgba(24, 58, 105, 0.055) !important;
}

[data-testid="stMetricLabel"] {
    color: var(--oasis-muted) !important;
    font-size: 0.82rem !important;
    font-weight: 700 !important;
}

[data-testid="stMetricValue"] {
    color: var(--oasis-navy-900) !important;
    font-size: clamp(1.5rem, 2.4vw, 1.9rem) !important;
    font-weight: 800 !important;
    line-height: 1.2 !important;
}

[data-testid="stMetricDelta"] {
    font-size: 0.78rem !important;
}

/* Forms and controls */
[data-testid="stWidgetLabel"] p,
[data-testid="stWidgetLabel"] label,
[data-testid="stCheckbox"] label p,
[data-testid="stRadio"] > label p {
    color: var(--oasis-text) !important;
    font-size: 0.88rem !important;
    font-weight: 700 !important;
    line-height: 1.42 !important;
    word-break: keep-all !important;
}

[data-baseweb="input"],
[data-baseweb="select"] > div,
[data-testid="stTextArea"] textarea,
[data-testid="stDateInput"] input,
[data-testid="stNumberInput"] input {
    min-height: 46px !important;
    color: var(--oasis-text-strong) !important;
    border-color: var(--oasis-border-strong) !important;
    border-radius: var(--oasis-radius-control) !important;
    background: #ffffff !important;
    font-size: 0.94rem !important;
    box-shadow: none !important;
}

[data-testid="stTextArea"] textarea {
    padding: 0.75rem 0.85rem !important;
    line-height: 1.55 !important;
}

[data-baseweb="input"]:focus-within,
[data-baseweb="select"] > div:focus-within,
[data-testid="stTextArea"] textarea:focus,
[data-testid="stDateInput"] input:focus,
[data-testid="stNumberInput"]:focus-within {
    border-color: var(--oasis-blue-600) !important;
    box-shadow: 0 0 0 3px var(--oasis-focus) !important;
    outline: none !important;
}

input::placeholder,
textarea::placeholder {
    color: #8594a8 !important;
    opacity: 1 !important;
}

[data-testid="stNumberInput"] button {
    min-width: 42px !important;
    min-height: 42px !important;
}

[data-testid="stFileUploader"] {
    padding: 1rem !important;
    border: 1px dashed #aebed3 !important;
    border-radius: 14px !important;
    background: rgba(255, 255, 255, 0.92) !important;
}

[data-testid="stForm"] {
    padding: clamp(1rem, 2.6vw, 1.35rem) !important;
    border: 1px solid var(--oasis-border) !important;
    border-radius: var(--oasis-radius-card) !important;
    background: rgba(255, 255, 255, 0.88) !important;
}

/* Clear visual hierarchy: secondary actions are quiet, primary actions are blue. */
[data-testid="stButton"] button,
[data-testid="stDownloadButton"] button,
[data-testid="stFormSubmitButton"] button,
[data-testid="stPopover"] button {
    min-height: 44px !important;
    padding: 0.62rem 1rem !important;
    color: var(--oasis-blue-700) !important;
    border: 1px solid #b9cbe2 !important;
    border-radius: var(--oasis-radius-control) !important;
    background: #ffffff !important;
    box-shadow: 0 3px 10px rgba(22, 56, 105, 0.045) !important;
    font-size: 0.9rem !important;
    font-weight: 700 !important;
    line-height: 1.3 !important;
    white-space: normal !important;
    word-break: keep-all !important;
}

[data-testid="stButton"] button[kind="primary"],
[data-testid="stFormSubmitButton"] button[kind="primary"] {
    color: #ffffff !important;
    border-color: transparent !important;
    background: linear-gradient(135deg, #073f98 0%, #1265dd 100%) !important;
    box-shadow: 0 8px 18px rgba(18, 97, 216, 0.2) !important;
}

[data-testid="stButton"] button:hover,
[data-testid="stDownloadButton"] button:hover,
[data-testid="stFormSubmitButton"] button:hover {
    border-color: var(--oasis-blue-600) !important;
    background: #f4f8ff !important;
}

[data-testid="stButton"] button[kind="primary"]:hover,
[data-testid="stFormSubmitButton"] button[kind="primary"]:hover {
    color: #ffffff !important;
    background: linear-gradient(135deg, #063985 0%, #0b5bd3 100%) !important;
}

.st-key-enterprise_delete_action [data-testid="stButton"] button {
    color: var(--oasis-danger) !important;
    border-color: #e7b9bd !important;
    background: #fff8f8 !important;
}

.st-key-enterprise_delete_action [data-testid="stButton"] button:hover {
    color: #a92531 !important;
    border-color: var(--oasis-danger) !important;
    background: #fff0f1 !important;
}

button:focus-visible,
[role="button"]:focus-visible,
label:focus-within {
    outline: 3px solid rgba(77, 143, 236, 0.48) !important;
    outline-offset: 2px !important;
}

/* Tabs, expanders, notices and tables */
div[data-baseweb="tab-list"] {
    gap: 0.25rem !important;
    overflow-x: auto !important;
    padding-bottom: 0.2rem !important;
    border-bottom: 1px solid var(--oasis-border) !important;
    scrollbar-width: thin !important;
}

button[data-baseweb="tab"] {
    min-width: max-content !important;
    min-height: 44px !important;
    padding: 0.65rem 0.85rem !important;
    color: var(--oasis-muted) !important;
    font-size: 0.88rem !important;
    font-weight: 700 !important;
    white-space: nowrap !important;
}

button[data-baseweb="tab"][aria-selected="true"] {
    color: var(--oasis-blue-700) !important;
}

[data-testid="stExpander"] details {
    overflow: hidden !important;
    border: 1px solid var(--oasis-border) !important;
    border-radius: 14px !important;
    background: rgba(255, 255, 255, 0.92) !important;
}

[data-testid="stExpander"] details summary {
    min-height: 48px !important;
    padding: 0.65rem 0.85rem !important;
}

[data-testid="stExpander"] details summary p {
    color: var(--oasis-text-strong) !important;
    font-size: 0.9rem !important;
    font-weight: 700 !important;
    word-break: keep-all !important;
}

[data-testid="stAlert"] {
    border-radius: 13px !important;
}

[data-testid="stAlert"] [data-testid="stMarkdownContainer"] p {
    font-size: 0.9rem !important;
    line-height: 1.55 !important;
}

[data-testid="stDataFrame"],
[data-testid="stDataEditor"] {
    overflow: hidden !important;
    border: 1px solid var(--oasis-border) !important;
    border-radius: 13px !important;
    background: #ffffff !important;
}

[data-testid="stHorizontalBlock"] {
    gap: 0.8rem !important;
}

/* ================= Sidebar ================= */
[data-testid="stSidebar"] {
    color: #ffffff !important;
    border-right: 1px solid rgba(255, 255, 255, 0.13) !important;
    background:
        radial-gradient(circle at 22% 0%, rgba(67, 142, 255, 0.34), transparent 28%),
        linear-gradient(
            180deg,
            var(--oasis-sidebar-dark) 0%,
            var(--oasis-sidebar-mid) 54%,
            var(--oasis-sidebar-bright) 100%
        ) !important;
}

[data-testid="stSidebar"] > div:first-child {
    padding: 0.65rem 0.9rem 0.9rem !important;
}

[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
    gap: 0.6rem !important;
}

[data-testid="stSidebar"]
[data-testid="stMarkdownContainer"]:has(> .sidebar-logo),
[data-testid="stSidebar"]
[data-testid="stMarkdownContainer"]:has(> .sidebar-user-card),
[data-testid="stSidebar"]
[data-testid="stMarkdownContainer"]:has(> .sidebar-section-label) {
    margin-bottom: 0 !important;
}

.sidebar-logo {
    position: relative !important;
    box-sizing: border-box !important;
    width: 100% !important;
    height: 82px !important;
    margin: 0 !important;
    overflow: hidden !important;
}

[data-testid="stSidebar"] .sidebar-logo img {
    position: absolute !important;
    top: 50% !important;
    left: 50% !important;
    display: block !important;
    width: 360px !important;
    max-width: none !important;
    height: auto !important;
    margin: 0 !important;
    opacity: 1 !important;
    filter: brightness(0) invert(1) drop-shadow(0 10px 20px rgba(0, 0, 0, 0.14)) !important;
    transform: translate(-50%, -50%) !important;
}

.sidebar-user-card {
    margin: 0 !important;
    padding: 0.65rem 0.8rem !important;
    color: #ffffff !important;
    border: 1px solid rgba(255, 255, 255, 0.2) !important;
    border-radius: 14px !important;
    background: rgba(255, 255, 255, 0.13) !important;
}

.sidebar-user-card .name {
    color: #ffffff !important;
    font-size: 1rem !important;
    font-weight: 800 !important;
    line-height: 1.3 !important;
}

.sidebar-user-card .role {
    margin-top: 0.2rem !important;
    color: var(--oasis-menu-muted) !important;
    font-size: 0.75rem !important;
    line-height: 1.4 !important;
}

.sidebar-section-label {
    margin: 0.2rem 0 0 0.15rem !important;
    color: var(--oasis-menu-muted) !important;
    font-size: 0.74rem !important;
    font-weight: 700 !important;
    letter-spacing: 0.055em !important;
    text-transform: none !important;
}

[data-testid="stSidebar"] .st-key-sidebar_group_switcher,
[data-testid="stSidebar"] .st-key-sidebar_detail_navigation {
    box-sizing: border-box !important;
    width: 100% !important;
    padding: 0.68rem !important;
    border: 1px solid rgba(255, 255, 255, 0.17) !important;
    border-radius: 13px !important;
}

[data-testid="stSidebar"] .st-key-sidebar_group_switcher {
    background: rgba(255, 255, 255, 0.105) !important;
}

[data-testid="stSidebar"] .st-key-sidebar_detail_navigation {
    padding-top: 0.72rem !important;
    background: rgba(2, 28, 72, 0.24) !important;
    box-shadow: inset 3px 0 0 rgba(255, 255, 255, 0.34) !important;
}

[data-testid="stSidebar"] .st-key-sidebar_group_switcher
[data-testid="stVerticalBlock"],
[data-testid="stSidebar"] .st-key-sidebar_detail_navigation
[data-testid="stVerticalBlock"] {
    gap: 0.48rem !important;
}

.sidebar-nav-heading {
    display: flex !important;
    align-items: center !important;
    justify-content: space-between !important;
    gap: 0.5rem !important;
    margin: 0 !important;
    color: #ffffff !important;
    font-size: 0.78rem !important;
    font-weight: 800 !important;
    line-height: 1.25 !important;
    letter-spacing: -0.01em !important;
}

.sidebar-nav-heading small {
    display: inline-flex !important;
    align-items: center !important;
    min-height: 21px !important;
    padding: 0.14rem 0.45rem !important;
    color: rgba(255, 255, 255, 0.82) !important;
    border: 1px solid rgba(255, 255, 255, 0.2) !important;
    border-radius: 999px !important;
    background: rgba(255, 255, 255, 0.09) !important;
    font-size: 0.66rem !important;
    font-weight: 700 !important;
    letter-spacing: 0 !important;
    white-space: nowrap !important;
}

.sidebar-nav-heading-detail {
    padding-bottom: 0.1rem !important;
    border-bottom: 1px solid rgba(255, 255, 255, 0.14) !important;
}

[data-testid="stSidebar"] label,
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] {
    color: var(--oasis-menu-text) !important;
}

[data-testid="stSidebar"] [data-testid="stRadio"] div[role="radiogroup"] {
    gap: 0.25rem !important;
}

[data-testid="stSidebar"] [data-testid="stRadio"] div[role="radiogroup"] label {
    position: relative !important;
    display: flex !important;
    align-items: center !important;
    width: 100% !important;
    min-height: 44px !important;
    margin: 0 !important;
    padding: 0.58rem 0.75rem !important;
    color: var(--oasis-menu-text) !important;
    border: 1px solid rgba(255, 255, 255, 0.12) !important;
    border-radius: 11px !important;
    background: rgba(255, 255, 255, 0.055) !important;
    box-shadow: none !important;
    transition: background 0.14s ease, border-color 0.14s ease !important;
}

[data-testid="stSidebar"] [data-testid="stRadio"] div[role="radiogroup"] label:hover {
    border-color: rgba(255, 255, 255, 0.22) !important;
    background: rgba(255, 255, 255, 0.13) !important;
}

[data-testid="stSidebar"] [data-testid="stRadio"] div[role="radiogroup"] label:has(input:checked) {
    color: var(--oasis-navy-900) !important;
    border-color: #ffffff !important;
    background: #ffffff !important;
    box-shadow: 0 9px 22px rgba(0, 0, 0, 0.15) !important;
}

[data-testid="stSidebar"] [data-testid="stRadio"] div[role="radiogroup"] label p {
    margin: 0 !important;
    color: var(--oasis-menu-text) !important;
    font-size: 0.9rem !important;
    font-weight: 700 !important;
    line-height: 1.25 !important;
    letter-spacing: -0.015em !important;
    white-space: nowrap !important;
}

[data-testid="stSidebar"] [data-testid="stRadio"] div[role="radiogroup"] label:has(input:checked) p {
    color: var(--oasis-navy-900) !important;
    font-weight: 800 !important;
}

[data-testid="stSidebar"] [data-testid="stRadio"] label input {
    accent-color: #ffffff !important;
}

[data-testid="stSidebar"] [data-testid="stRadio"] label > div:first-child {
    margin-right: 0.55rem !important;
}

[data-testid="stSidebar"] [data-testid="stButtonGroup"] [role="radiogroup"] {
    display: grid !important;
    grid-template-columns: repeat(3, minmax(0, 1fr)) !important;
    gap: 0.25rem !important;
    width: 100% !important;
}

[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-variant="pills"] {
    min-width: 0 !important;
    min-height: 38px !important;
    padding: 0.38rem 0.3rem !important;
    justify-content: center !important;
    color: rgba(255, 255, 255, 0.9) !important;
    border: 1px solid rgba(255, 255, 255, 0.22) !important;
    border-radius: 10px !important;
    background: rgba(255, 255, 255, 0.07) !important;
    font-size: 0.76rem !important;
    font-weight: 700 !important;
    white-space: nowrap !important;
}

[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-variant="pills"] * {
    color: inherit !important;
}

[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-variant="pills"][data-selected="true"],
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-variant="pills"][aria-pressed="true"] {
    color: var(--oasis-navy-900) !important;
    border-color: #ffffff !important;
    background: #ffffff !important;
    box-shadow: 0 7px 18px rgba(0, 0, 0, 0.14) !important;
}

[data-testid="stSidebar"] hr {
    margin: 0.45rem 0 !important;
    border-color: rgba(255, 255, 255, 0.16) !important;
}

[data-testid="stSidebar"] [data-testid="stExpander"] details {
    border-color: rgba(255, 255, 255, 0.14) !important;
    background: rgba(255, 255, 255, 0.055) !important;
}

[data-testid="stSidebar"] [data-testid="stExpander"] details summary p {
    color: rgba(255, 255, 255, 0.9) !important;
}

[data-testid="stSidebar"] [data-testid="stButton"] button {
    color: #ffffff !important;
    border-color: rgba(255, 255, 255, 0.2) !important;
    background: rgba(255, 255, 255, 0.11) !important;
    box-shadow: none !important;
}

/* Login is compact on desktop and never inherits the logged-in mobile nav gap. */
[data-testid="stAppViewContainer"]:has(.login-panel) .block-container {
    max-width: 920px !important;
    padding-top: 0.75rem !important;
}

[data-testid="stAppViewContainer"]:has(.login-panel) [data-testid="stHeader"] {
    display: none !important;
}

.login-wrap {
    max-width: 920px;
    margin: 0 auto;
}

.login-logo {
    position: relative !important;
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
    width: 100% !important;
    height: 132px !important;
    margin: 0 auto 0.9rem !important;
    overflow: hidden !important;
}

.login-logo img {
    position: absolute !important;
    top: 50% !important;
    left: 50% !important;
    display: block !important;
    width: 440px !important;
    max-width: none !important;
    height: auto !important;
    margin: 0 !important;
    transform: translate(-50%, -50%) !important;
}

.login-panel {
    margin-bottom: 1rem !important;
    padding: 1.55rem 1.8rem !important;
    border: 1px solid var(--oasis-border) !important;
    border-radius: 18px !important;
    background: rgba(255, 255, 255, 0.96) !important;
    box-shadow: var(--oasis-shadow-card) !important;
}

.login-title {
    margin-bottom: 0.5rem !important;
    color: var(--oasis-navy-900) !important;
    font-size: clamp(1.55rem, 3vw, 1.9rem) !important;
    font-weight: 800 !important;
}

.login-desc {
    color: var(--oasis-muted) !important;
    font-size: 0.93rem !important;
    line-height: 1.65 !important;
    word-break: keep-all !important;
}

/* ================= Tablet ================= */
@media (min-width: 769px) and (max-width: 1100px) {
    .block-container {
        padding: 4.35rem 1.25rem 3.5rem !important;
    }

    .hero {
        padding: 2rem 2.25rem !important;
    }

    .hero-title {
        font-size: 2.15rem !important;
    }

    [data-testid="stHorizontalBlock"]:has([data-testid="stMetric"]) {
        flex-wrap: wrap !important;
    }

    [data-testid="stHorizontalBlock"]:has([data-testid="stMetric"])
    > [data-testid="stColumn"] {
        flex: 1 1 calc(50% - 0.5rem) !important;
        width: calc(50% - 0.5rem) !important;
    }
}

/* ================= Mobile ================= */
@media (max-width: 768px) {
    html {
        font-size: 16px;
    }

    [data-testid="stHeader"] {
        height: 0 !important;
        min-height: 0 !important;
        overflow: visible !important;
        background: transparent !important;
    }

    [data-testid="stHeader"] > div {
        height: 0 !important;
        min-height: 0 !important;
    }

    .block-container {
        max-width: 100% !important;
        padding:
            calc(3.7rem + env(safe-area-inset-top))
            0.75rem
            calc(4.5rem + env(safe-area-inset-bottom))
            !important;
    }

    [data-testid="stAppViewContainer"]:has(.login-panel) .block-container {
        padding: 0.4rem 0.75rem calc(2rem + env(safe-area-inset-bottom)) !important;
    }

    h1 {
        font-size: 1.72rem !important;
    }

    h2 {
        font-size: 1.48rem !important;
    }

    h3 {
        font-size: 1.23rem !important;
    }

    h4 {
        font-size: 1.05rem !important;
    }

    [data-testid="stSidebar"] {
        position: fixed !important;
        inset: 0 auto 0 0 !important;
        z-index: 1001 !important;
        width: min(58vw, 224px) !important;
        min-width: min(58vw, 224px) !important;
        max-width: min(58vw, 224px) !important;
        height: 100dvh !important;
        box-sizing: border-box !important;
    }

    [data-testid="stSidebar"] > div:first-child {
        width: 100% !important;
        padding:
            calc(0.2rem + env(safe-area-inset-top))
            0.55rem
            calc(0.65rem + env(safe-area-inset-bottom))
            !important;
        overflow-x: hidden !important;
    }

    [data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
        gap: 0.4rem !important;
    }

    [data-testid="stSidebarHeader"] {
        position: sticky !important;
        top: 0 !important;
        z-index: 1002 !important;
        height: 44px !important;
        min-height: 44px !important;
        margin: 0 !important;
        justify-content: flex-end !important;
        pointer-events: none !important;
    }

    [data-testid="stSidebarCollapseButton"] {
        display: inline-flex !important;
        visibility: visible !important;
        opacity: 1 !important;
        pointer-events: auto !important;
    }

    [data-testid="stSidebarCollapseButton"] button {
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
        gap: 0.2rem !important;
        width: auto !important;
        min-width: 64px !important;
        min-height: 44px !important;
        padding: 0 0.65rem !important;
        color: var(--oasis-navy-900) !important;
        border: 1px solid rgba(255, 255, 255, 0.78) !important;
        border-radius: 11px !important;
        background: rgba(255, 255, 255, 0.97) !important;
        box-shadow: 0 7px 20px rgba(0, 0, 0, 0.16) !important;
    }

    [data-testid="stSidebarCollapseButton"] button::after {
        content: "닫기" !important;
        color: var(--oasis-navy-900) !important;
        font-size: 0.75rem !important;
        font-weight: 700 !important;
        line-height: 1 !important;
    }

    [data-testid="stSidebarCollapseButton"] svg {
        color: var(--oasis-navy-900) !important;
        fill: var(--oasis-navy-900) !important;
    }

    [data-testid="stExpandSidebarButton"] {
        position: fixed !important;
        top: calc(0.55rem + env(safe-area-inset-top)) !important;
        left: 0.65rem !important;
        z-index: 1003 !important;
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
        gap: 0.3rem !important;
        visibility: visible !important;
        opacity: 1 !important;
        width: auto !important;
        min-width: 0 !important;
        height: 44px !important;
        padding: 0 0.8rem !important;
        color: #ffffff !important;
        border: 1px solid rgba(255, 255, 255, 0.9) !important;
        border-radius: 11px !important;
        background: var(--oasis-blue-600) !important;
        box-shadow: 0 7px 20px rgba(8, 72, 166, 0.24) !important;
    }

    [data-testid="stExpandSidebarButton"]::after {
        content: "사이드메뉴 열기" !important;
        color: #ffffff !important;
        font-size: 0.8rem !important;
        font-weight: 700 !important;
        line-height: 1 !important;
    }

    [data-testid="stExpandSidebarButton"] svg {
        color: #ffffff !important;
        fill: #ffffff !important;
    }

    .sidebar-logo {
        height: 62px !important;
        margin: 0 !important;
    }

    [data-testid="stSidebar"] .sidebar-logo img {
        width: 260px !important;
        filter: brightness(0) invert(1) !important;
    }

    .sidebar-user-card {
        padding: 0.55rem 0.65rem !important;
        border-radius: 10px !important;
    }

    .sidebar-user-card .name {
        font-size: 0.9rem !important;
    }

    .sidebar-user-card .role {
        font-size: 0.75rem !important;
    }

    .sidebar-section-label {
        margin-left: 0.15rem !important;
        font-size: 0.75rem !important;
    }

    [data-testid="stSidebar"] .st-key-sidebar_group_switcher,
    [data-testid="stSidebar"] .st-key-sidebar_detail_navigation {
        padding: 0.55rem !important;
        border-radius: 11px !important;
    }

    .sidebar-nav-heading {
        font-size: 0.75rem !important;
    }

    .sidebar-nav-heading small {
        min-height: 19px !important;
        padding: 0.1rem 0.38rem !important;
        font-size: 0.62rem !important;
    }

    [data-testid="stSidebar"] [data-testid="stButtonGroup"] {
        width: 100% !important;
        margin-bottom: 0 !important;
    }

    [data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-variant="pills"] {
        min-height: 44px !important;
        padding: 0.35rem 0.15rem !important;
        font-size: 0.75rem !important;
    }

    [data-testid="stSidebar"] [data-testid="stRadio"] div[role="radiogroup"] {
        align-items: flex-start !important;
        gap: 0.18rem !important;
        width: 100% !important;
    }

    [data-testid="stSidebar"] [data-testid="stRadio"] [data-testid="stRadioOption"] {
        width: fit-content !important;
        min-width: 0 !important;
        max-width: 100% !important;
    }

    [data-testid="stSidebar"] [data-testid="stRadio"] div[role="radiogroup"] label {
        display: inline-flex !important;
        width: fit-content !important;
        min-width: 0 !important;
        max-width: 100% !important;
        min-height: 44px !important;
        padding: 0.55rem 0.65rem !important;
        border-radius: 9px !important;
    }

    [data-testid="stSidebar"] [data-testid="stRadioOption"]
    > div > div:first-child > div:first-child {
        display: none !important;
    }

    [data-testid="stSidebar"] [data-testid="stRadio"] div[role="radiogroup"] label p {
        width: auto !important;
        min-width: 0 !important;
        font-size: 0.87rem !important;
        font-weight: 700 !important;
        white-space: nowrap !important;
    }

    [data-testid="stSidebar"] [data-testid="stExpander"] details summary {
        min-height: 44px !important;
        padding: 0.55rem 0.65rem !important;
    }

    [data-testid="stSidebar"] [data-testid="stButton"] button {
        min-height: 44px !important;
        font-size: 0.85rem !important;
    }

    .oasis-topbar,
    .oasis-topbar-compact {
        min-height: 54px !important;
        margin: 0 0 0.75rem !important;
        padding: 0.65rem 0.8rem !important;
        border-radius: 13px !important;
    }

    .oasis-topbar-compact .oasis-topbar-title {
        font-size: 1.12rem !important;
    }

    .oasis-topbar-compact .oasis-topbar-sub {
        font-size: 0.75rem !important;
    }

    .hero {
        margin-bottom: 1rem !important;
        padding: 1.5rem 1.25rem !important;
        border-radius: 18px !important;
    }

    .hero-title {
        max-width: 100%;
        margin-bottom: 0.65rem !important;
        font-size: clamp(1.65rem, 8vw, 2.05rem) !important;
        line-height: 1.28 !important;
    }

    .hero-sub {
        font-size: 0.94rem !important;
        line-height: 1.65 !important;
    }

    .badge {
        margin-bottom: 0.7rem !important;
        padding: 0.35rem 0.65rem !important;
        font-size: 0.7rem !important;
    }

    .oasis-card,
    .preview-box {
        padding: 1rem !important;
        border-radius: 15px !important;
    }

    .point-card {
        min-height: 0 !important;
        padding: 0.9rem !important;
        border-radius: 14px !important;
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

    [data-testid="stHorizontalBlock"]:has([data-testid="stMetric"])
    > [data-testid="stColumn"] {
        flex: 1 1 calc(50% - 0.35rem) !important;
        width: calc(50% - 0.35rem) !important;
    }

    [data-testid="stMetric"] {
        min-height: 96px !important;
        padding: 0.75rem 0.85rem !important;
    }

    [data-testid="stMetricLabel"] {
        font-size: 0.77rem !important;
    }

    [data-testid="stMetricValue"] {
        font-size: 1.48rem !important;
    }

    [data-testid="stWidgetLabel"] p,
    [data-testid="stWidgetLabel"] label {
        font-size: 0.88rem !important;
    }

    [data-baseweb="input"],
    [data-baseweb="select"] > div,
    [data-testid="stTextArea"] textarea,
    [data-testid="stDateInput"] input,
    [data-testid="stNumberInput"] input {
        min-height: 48px !important;
        font-size: 1rem !important;
    }

    [data-testid="stButton"] button,
    [data-testid="stDownloadButton"] button,
    [data-testid="stFormSubmitButton"] button {
        min-height: 46px !important;
        font-size: 0.9rem !important;
    }

    div[data-baseweb="tab-list"] {
        flex-wrap: nowrap !important;
    }

    button[data-baseweb="tab"] {
        min-height: 46px !important;
        padding: 0.65rem 0.75rem !important;
        font-size: 0.84rem !important;
    }

    [data-testid="stExpander"] details summary {
        min-height: 48px !important;
    }

    [data-testid="stDataFrame"],
    [data-testid="stDataEditor"] {
        overflow-x: auto !important;
        -webkit-overflow-scrolling: touch;
    }

    .login-logo {
        height: 104px !important;
        margin-bottom: 0.7rem !important;
    }

    .login-logo img {
        width: min(96vw, 370px) !important;
    }

    .login-panel {
        margin-bottom: 0.75rem !important;
        padding: 1.25rem 1.1rem !important;
        border-radius: 16px !important;
    }

    .login-title {
        margin-bottom: 0.45rem !important;
        font-size: 1.45rem !important;
    }

    .login-desc {
        font-size: 0.88rem !important;
        line-height: 1.6 !important;
    }
}

@media (max-width: 360px) {
    [data-testid="stSidebar"] {
        width: 200px !important;
        min-width: 200px !important;
        max-width: 200px !important;
    }

    [data-testid="stHorizontalBlock"]:has([data-testid="stMetric"])
    > [data-testid="stColumn"] {
        flex-basis: 100% !important;
        width: 100% !important;
    }

    .hero-title {
        font-size: 1.55rem !important;
    }
}

@media (prefers-reduced-motion: reduce) {
    *,
    *::before,
    *::after {
        scroll-behavior: auto !important;
        animation-duration: 0.01ms !important;
        animation-iteration-count: 1 !important;
        transition-duration: 0.01ms !important;
    }
}
</style>
        """,
        unsafe_allow_html=True,
    )
