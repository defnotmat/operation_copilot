import sqlite3
import html
import pandas as pd
import streamlit as st
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "support_events.db"

st.set_page_config(page_title="Support Dashboard", layout="wide")
st.title("Support Dashboard")


def apply_jira_theme() -> None:
    st.markdown(
        """
        <style>
          :root {
            --jira-bg: #f4f5f7;
            --jira-panel: #ffffff;
            --jira-border: #dfe1e6;
            --jira-text: #172b4d;
            --jira-muted: #5e6c84;
            --jira-blue: #0c66e4;
          }
          [data-testid="stAppViewContainer"] {
            background: var(--jira-bg);
          }
          [data-testid="stHeader"] {
            background: transparent;
          }
          .block-container {
            padding-top: 1.2rem;
          }
          h1, h2, h3 {
            color: var(--jira-blue) !important;
            letter-spacing: -0.01em;
          }
          [data-testid="metric-container"] {
            background: var(--jira-panel);
            border: 1px solid var(--jira-border);
            border-radius: 10px;
            box-shadow: 0 1px 2px rgba(9, 30, 66, 0.10);
            padding: 10px 14px;
          }
          [data-testid="stMetricLabel"] p {
            color: var(--jira-blue) !important;
            font-weight: 700;
          }
          [data-testid="stMetricValue"] {
            color: var(--jira-blue) !important;
            font-weight: 800;
          }
          [data-testid="stWidgetLabel"] p {
            color: var(--jira-blue) !important;
            font-weight: 700;
          }
          [data-baseweb="select"] > div {
            border-color: #b3d4ff !important;
            background: #ffffff !important;
          }
          [data-baseweb="select"] span,
          [data-baseweb="select"] input {
            color: var(--jira-blue) !important;
          }
          [role="listbox"] [role="option"] {
            color: var(--jira-blue) !important;
          }
          [role="listbox"] [role="option"][aria-selected="true"] {
            background: #f5efe2 !important;
            color: #5b4b2f !important;
          }
          [data-baseweb="tag"] {
            background: #f5efe2 !important;
            border: 1px solid #d8c9a6 !important;
            border-radius: 8px !important;
            color: #5b4b2f !important;
          }
          [data-baseweb="tag"] span {
            color: #5b4b2f !important;
          }
          [data-baseweb="tag"] svg {
            fill: #8a6f3d !important;
          }
        </style>
        """,
        unsafe_allow_html=True,
    )


apply_jira_theme()


@st.cache_data(ttl=5)
def load_events():
    con = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        """
        SELECT *
        FROM support_events
        WHERE request_type IN ('bug_report', 'feature_request')
        ORDER BY datetime(created_at) DESC
        """,
        con,
    )
    con.close()
    return df


def safe_text(value: object, fallback: str = "-") -> str:
    if value is None or pd.isna(value):
        return fallback
    text = str(value).strip()
    return text if text else fallback


def badge_class(request_type: str) -> str:
    if request_type == "bug_report":
        return "badge bug"
    if request_type == "feature_request":
        return "badge feature"
    if request_type == "question":
        return "badge question"
    return "badge default"


def render_event_cards(df: pd.DataFrame, cols_count: int = 3) -> None:
    st.markdown(
        """
        <style>
          .event-card {
            position: relative;
            background: #ffffff;
            border: 1px solid #dfe1e6;
            border-radius: 12px;
            padding: 14px;
            height: 500px;
            min-height: 500px;
            display: flex;
            flex-direction: column;
            gap: 10px;
            box-shadow: 0 1px 2px rgba(9, 30, 66, 0.12);
            overflow: hidden;
          }
          .event-card::before {
            content: "";
            position: absolute;
            left: 0;
            top: 0;
            bottom: 0;
            width: 4px;
            background: #7a869a;
          }
          .event-card.card-bug::before { background: #0c66e4; }
          .event-card.card-feature::before { background: #0052cc; }
          .event-card > * {
            position: relative;
            z-index: 1;
          }
          .event-card-head {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 10px;
          }
          .event-card-date {
            color: #5e6c84;
            font-size: 12px;
            font-weight: 600;
            white-space: normal;
            text-align: right;
          }
          .badge {
            display: inline-block;
            padding: 4px 9px;
            border-radius: 999px;
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 0.01em;
            text-transform: uppercase;
          }
          .badge.bug { background: #deebff; color: #0747a6; border: 1px solid #b3d4ff; }
          .badge.feature { background: #e9f2ff; color: #0052cc; border: 1px solid #c5ddff; }
          .badge.question { background: #deebff; color: #0747a6; border: 1px solid #b3d4ff; }
          .badge.default { background: #ebecf0; color: #42526e; border: 1px solid #dfe1e6; }
          .event-card-title {
            margin: 0;
            color: #172b4d !important;
            font-size: 14px;
            font-weight: 800;
            line-height: 1.3;
            max-height: 3.9em;
            overflow-y: auto;
            padding-right: 4px;
          }
          .event-card-title::-webkit-scrollbar,
          .event-card-next-text::-webkit-scrollbar {
            width: 8px;
          }
          .event-card-title::-webkit-scrollbar-thumb,
          .event-card-next-text::-webkit-scrollbar-thumb {
            background: #97b8e6;
            border-radius: 999px;
          }
          .event-card-meta {
            margin-top: 2px;
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 6px;
          }
          .event-chip {
            background: #f7f9fc;
            border: 1px solid #dfe1e6;
            border-radius: 8px;
            padding: 7px 8px 6px 8px;
            min-height: 44px;
          }
          .event-chip-label {
            margin: 0;
            font-size: 10px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.03em;
            color: #5e6c84;
          }
          .event-chip-value {
            margin: 2px 0 0 0;
            font-size: 13px;
            font-weight: 700;
            color: #172b4d;
            line-height: 1.25;
            word-break: break-word;
          }
          .event-card-next {
            margin-top: auto;
            background: #ffffff;
            border: 1px solid #dfe1e6;
            border-radius: 10px;
            padding: 8px;
            height: 220px;
            display: flex;
            flex-direction: column;
          }
          .event-card-next-label {
            margin: 0;
            font-size: 11px;
            font-weight: 700;
            color: #0c66e4;
            text-transform: uppercase;
            letter-spacing: 0.02em;
            padding: 0 4px;
          }
          .event-card-next-body {
            margin-top: 6px;
            height: 178px;
            min-height: 178px;
            border: 1px solid #b3d4ff;
            border-radius: 8px;
            background: #ffffff;
            padding: 8px 10px;
            overflow: hidden;
          }
          .event-card-next-text {
            margin: 0;
            font-size: 13px;
            line-height: 1.4;
            color: #172b4d;
            height: 100%;
            max-height: 100%;
            overflow-y: auto !important;
            word-break: break-word;
            padding-right: 2px;
            white-space: pre-wrap;
            display: block;
            min-height: 100%;
          }
        </style>
        """,
        unsafe_allow_html=True,
    )

    cols = st.columns(cols_count)
    for idx, (_, row) in enumerate(df.iterrows()):
        req_type = safe_text(row.get("request_type"), "unknown")
        summary = html.escape(safe_text(row.get("summary"), "No summary"))
        priority = html.escape(safe_text(row.get("priority_level"), "-"))
        source = html.escape(safe_text(row.get("source"), "-"))
        sender = html.escape(safe_text(row.get("sender"), "-"))
        message_id = html.escape(safe_text(row.get("message_id"), "-"))
        next_action = html.escape(safe_text(row.get("suggested_next_action"), "no suggested action"))
        created_at = html.escape(safe_text(row.get("created_at"), "-"))
        req_type_label = html.escape(req_type.replace("_", " ").title())
        req_type_class = "bug" if req_type == "bug_report" else "feature" if req_type == "feature_request" else "default"

        card_html = f"""
        <article class="event-card card-{req_type_class}">
          <div class="event-card-head">
            <span class="{badge_class(req_type)}">{req_type_label}</span>
            <span class="event-card-date">{created_at}</span>
          </div>
          <h4 class="event-card-title">{summary}</h4>
          <div class="event-card-meta">
            <div class="event-chip"><p class="event-chip-label">Priority</p><p class="event-chip-value">{priority}</p></div>
            <div class="event-chip"><p class="event-chip-label">Source</p><p class="event-chip-value">{source}</p></div>
            <div class="event-chip"><p class="event-chip-label">Sender</p><p class="event-chip-value">{sender}</p></div>
            <div class="event-chip"><p class="event-chip-label">Message</p><p class="event-chip-value">{message_id}</p></div>
          </div>
          <div class="event-card-next">
            <p class="event-card-next-label">Suggested Next Action</p>
            <div class="event-card-next-body">
              <p class="event-card-next-text">{next_action}</p>
            </div>
          </div>
        </article>
        """
        with cols[idx % cols_count]:
            st.markdown(card_html, unsafe_allow_html=True)


df = load_events()

if df.empty:
    st.info("No events yet. Run a few /extract calls first.")
    st.stop()

# Filters
c1, c2, c3 = st.columns(3)
with c1:
    req_types = st.multiselect(
        "Request type",
        sorted(df["request_type"].dropna().unique()),
        default=[t for t in ["bug_report", "feature_request"] if t in df["request_type"].unique()],
    )
with c2:
    sources = st.multiselect(
        "Source",
        sorted(df["source"].dropna().unique()),
        default=list(sorted(df["source"].dropna().unique())),
    )
with c3:
    days = st.selectbox("Time window", [1, 7, 14, 30, 90], index=1)

# Time window
df["created_at_dt"] = pd.to_datetime(df["created_at"], errors="coerce", utc=True)
cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)
filtered = df[df["created_at_dt"].notna() & (df["created_at_dt"] >= cutoff)]

# Apply filters
if req_types:
    filtered = filtered[filtered["request_type"].isin(req_types)]
if sources:
    filtered = filtered[filtered["source"].isin(sources)]

# KPIs
k1, k2, k3 = st.columns(3)
k1.metric("Total", len(filtered))
k2.metric("Bug reports", int((filtered["request_type"] == "bug_report").sum()))
k3.metric("Feature requests", int((filtered["request_type"] == "feature_request").sum()))

st.subheader("Latest events")
if filtered.empty:
    st.caption("No cards to display.")
else:
    render_event_cards(filtered, cols_count=3)
