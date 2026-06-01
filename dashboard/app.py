"""
ERP-CRM Data Integrity Engine — Executive Dashboard
C-Suite view: sync health, quality trends, quarantine queue, ERP pipeline value
"""

import sqlite3
import json
import os
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from datetime import datetime

# ── Config ────────────────────────────────────────────────────────────────────

DB_PATH = os.environ.get("DB_PATH", "../data/sync_engine.db")

st.set_page_config(
    page_title="Data Integrity Engine",
    page_icon="🔄",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Colour palette ────────────────────────────────────────────────────────────

SYNCED_COLOR      = "#10b981"   # emerald
FLAGGED_COLOR     = "#f59e0b"   # amber
QUARANTINE_COLOR  = "#ef4444"   # red
HIGH_COLOR        = "#6366f1"   # indigo
BG_CARD           = "#1e293b"
BG_PAGE           = "#0f172a"

# ── CSS ───────────────────────────────────────────────────────────────────────

st.markdown("""
<style>
    /* Page background */
    .stApp { background-color: #0f172a; color: #e2e8f0; }
    [data-testid="stAppViewContainer"] { background-color: #0f172a; }
    [data-testid="stHeader"] { background-color: #0f172a; }

    /* Metric cards */
    [data-testid="metric-container"] {
        background: #1e293b;
        border: 1px solid #334155;
        border-radius: 12px;
        padding: 16px 20px;
    }
    [data-testid="metric-container"] label { color: #94a3b8 !important; font-size: 0.78rem !important; text-transform: uppercase; letter-spacing: 0.08em; }
    [data-testid="metric-container"] [data-testid="stMetricValue"] { color: #f1f5f9 !important; font-size: 2rem !important; font-weight: 700; }
    [data-testid="metric-container"] [data-testid="stMetricDelta"] { font-size: 0.8rem !important; }

    /* Section headers */
    .section-header {
        font-size: 0.7rem;
        text-transform: uppercase;
        letter-spacing: 0.12em;
        color: #64748b;
        margin-bottom: 12px;
        margin-top: 28px;
        border-bottom: 1px solid #1e293b;
        padding-bottom: 6px;
    }

    /* Status pills */
    .pill-synced     { background:#064e3b; color:#6ee7b7; padding:2px 10px; border-radius:20px; font-size:0.75rem; }
    .pill-flagged    { background:#451a03; color:#fcd34d; padding:2px 10px; border-radius:20px; font-size:0.75rem; }
    .pill-quarantine { background:#450a0a; color:#fca5a5; padding:2px 10px; border-radius:20px; font-size:0.75rem; }

    /* Hide Streamlit chrome */
    #MainMenu, footer, header { visibility: hidden; }
    .block-container { padding-top: 1.5rem; padding-bottom: 2rem; }

    /* Table */
    .dataframe { background-color: #1e293b !important; color: #e2e8f0 !important; }
</style>
""", unsafe_allow_html=True)


# ── Data helpers ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=30)
def load_sync_log():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql("SELECT * FROM sync_log ORDER BY timestamp DESC", conn)
    conn.close()
    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


@st.cache_data(ttl=30)
def load_crm_stats():
    conn = sqlite3.connect(DB_PATH)
    total   = pd.read_sql("SELECT COUNT(*) as n FROM crm_deals", conn).iloc[0]["n"]
    synced  = pd.read_sql("SELECT COUNT(*) as n FROM crm_deals WHERE synced=1", conn).iloc[0]["n"]
    by_prof = pd.read_sql("SELECT quality_profile, COUNT(*) as cnt FROM crm_deals GROUP BY quality_profile", conn)
    by_stage = pd.read_sql(
        "SELECT deal_stage, COUNT(*) as cnt, ROUND(SUM(deal_value),0) as total_value "
        "FROM crm_deals GROUP BY deal_stage ORDER BY total_value DESC", conn
    )
    conn.close()
    return total, synced, by_prof, by_stage


@st.cache_data(ttl=30)
def load_erp_stats():
    conn = sqlite3.connect(DB_PATH)
    try:
        accepted    = pd.read_sql("SELECT COUNT(*) as n FROM erp_orders", conn).iloc[0]["n"]
        rejected    = pd.read_sql("SELECT COUNT(*) as n FROM erp_rejections", conn).iloc[0]["n"]
        total_value = pd.read_sql("SELECT ROUND(SUM(order_value),2) as v FROM erp_orders", conn).iloc[0]["v"] or 0
        avg_score   = pd.read_sql("SELECT ROUND(AVG(quality_score),1) as s FROM erp_orders", conn).iloc[0]["s"] or 0
        rejections  = pd.read_sql(
            "SELECT reason_codes, COUNT(*) as cnt FROM erp_rejections GROUP BY reason_codes ORDER BY cnt DESC LIMIT 8",
            conn
        )
        orders_over_time = pd.read_sql(
            "SELECT DATE(received_at) as date, COUNT(*) as cnt, ROUND(SUM(order_value),0) as value "
            "FROM erp_orders GROUP BY DATE(received_at) ORDER BY date",
            conn
        )
    except Exception:
        accepted = rejected = total_value = avg_score = 0
        rejections = pd.DataFrame()
        orders_over_time = pd.DataFrame()
    conn.close()
    return accepted, rejected, total_value, avg_score, rejections, orders_over_time


# ── Page ──────────────────────────────────────────────────────────────────────

# Header
col_title, col_refresh = st.columns([5, 1])
with col_title:
    st.markdown("## 🔄 ERP-CRM Data Integrity Engine")
    st.markdown(f"<span style='color:#64748b;font-size:0.8rem'>Executive Dashboard · Last updated {datetime.now().strftime('%H:%M:%S')}</span>", unsafe_allow_html=True)
with col_refresh:
    if st.button("↻ Refresh", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

st.divider()

# ── Load data ─────────────────────────────────────────────────────────────────

log_df                                             = load_sync_log()
crm_total, crm_synced, by_prof, by_stage           = load_crm_stats()
accepted, rejected, total_value, avg_score, rejections, orders_over_time = load_erp_stats()

crm_pending     = crm_total - crm_synced
total_processed = len(log_df)
sync_rate       = round(accepted / max(total_processed, 1) * 100, 1)
quarantined_n   = len(log_df[log_df["action"] == "quarantined"]) if not log_df.empty else 0
flagged_n       = len(log_df[log_df["action"] == "flagged"])     if not log_df.empty else 0


# ── KPI Row ───────────────────────────────────────────────────────────────────

st.markdown('<div class="section-header">Key Performance Indicators</div>', unsafe_allow_html=True)

k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("Total CRM Records",  f"{crm_total:,}")
k2.metric("Synced to ERP",       f"{accepted:,}",        delta=f"{sync_rate}% rate")
k3.metric("Flagged for Review",  f"{flagged_n:,}",        delta="Needs attention", delta_color="inverse")
k4.metric("Quarantined",         f"{quarantined_n:,}",    delta="Low quality",     delta_color="inverse")
k5.metric("ERP Pipeline Value",  f"€{total_value/1_000_000:.1f}M")
k6.metric("Avg Quality Score",   f"{avg_score}/100")


# ── Charts Row 1 ─────────────────────────────────────────────────────────────

st.markdown('<div class="section-header">Sync Outcomes & Data Quality</div>', unsafe_allow_html=True)

ch1, ch2, ch3 = st.columns(3)

with ch1:
    # Sync action donut
    if not log_df.empty:
        action_counts = log_df["action"].value_counts().reset_index()
        action_counts.columns = ["action", "count"]
        color_map = {"synced": SYNCED_COLOR, "flagged": FLAGGED_COLOR, "quarantined": QUARANTINE_COLOR}
        fig = px.pie(
            action_counts, values="count", names="action",
            hole=0.6, title="Sync Outcome Distribution",
            color="action", color_discrete_map=color_map,
        )
        fig.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font_color="#e2e8f0", title_font_size=13,
            legend=dict(font=dict(size=11)),
            margin=dict(t=40, b=10, l=10, r=10),
        )
        fig.update_traces(textfont_color="#e2e8f0")
        st.plotly_chart(fig, use_container_width=True)

with ch2:
    # Quality score histogram
    if not log_df.empty:
        fig = px.histogram(
            log_df, x="quality_score", nbins=20,
            title="Quality Score Distribution",
            color_discrete_sequence=[HIGH_COLOR],
        )
        fig.add_vline(x=75, line_dash="dash", line_color=SYNCED_COLOR,    annotation_text="Auto-sync ≥75")
        fig.add_vline(x=50, line_dash="dash", line_color=FLAGGED_COLOR,   annotation_text="Review ≥50")
        fig.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font_color="#e2e8f0", title_font_size=13,
            xaxis_title="Score", yaxis_title="Records",
            margin=dict(t=40, b=10, l=10, r=10),
        )
        st.plotly_chart(fig, use_container_width=True)

with ch3:
    # Quality profile breakdown
    if not by_prof.empty:
        fig = px.bar(
            by_prof.sort_values("cnt"), x="cnt", y="quality_profile",
            orientation="h", title="Records by Quality Profile",
            color="cnt", color_continuous_scale="Blues",
        )
        fig.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font_color="#e2e8f0", title_font_size=13,
            xaxis_title="Records", yaxis_title="",
            coloraxis_showscale=False,
            margin=dict(t=40, b=10, l=10, r=10),
        )
        st.plotly_chart(fig, use_container_width=True)


# ── Charts Row 2 ─────────────────────────────────────────────────────────────

st.markdown('<div class="section-header">ERP Pipeline & Rejection Analysis</div>', unsafe_allow_html=True)

ch4, ch5 = st.columns([2, 1])

with ch4:
    # Deal stage pipeline (horizontal funnel)
    if not by_stage.empty:
        fig = px.bar(
            by_stage, x="total_value", y="deal_stage",
            orientation="h", title="CRM Pipeline Value by Stage (€)",
            color="total_value", color_continuous_scale="Teal",
            text="cnt",
        )
        fig.update_traces(texttemplate="%{text} deals", textposition="inside", textfont_color="white")
        fig.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font_color="#e2e8f0", title_font_size=13,
            xaxis_title="Total Value (€)", yaxis_title="",
            coloraxis_showscale=False,
            margin=dict(t=40, b=10, l=10, r=10),
        )
        st.plotly_chart(fig, use_container_width=True)

with ch5:
    # Top rejection reasons
    if not rejections.empty:
        # Parse reason_codes JSON and flatten
        all_reasons = []
        for _, row in rejections.iterrows():
            try:
                codes = json.loads(row["reason_codes"])
                for code in codes:
                    all_reasons.append({"reason": code.split(":")[0], "count": row["cnt"]})
            except Exception:
                all_reasons.append({"reason": str(row["reason_codes"])[:30], "count": row["cnt"]})

        if all_reasons:
            reasons_df = pd.DataFrame(all_reasons).groupby("reason")["count"].sum().reset_index()
            reasons_df = reasons_df.sort_values("count", ascending=True).tail(8)
            fig = px.bar(
                reasons_df, x="count", y="reason",
                orientation="h", title="Top ERP Rejection Reasons",
                color_discrete_sequence=[QUARANTINE_COLOR],
            )
            fig.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font_color="#e2e8f0", title_font_size=13,
                xaxis_title="Occurrences", yaxis_title="",
                margin=dict(t=40, b=10, l=10, r=10),
            )
            st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No rejections yet.")


# ── Quarantine Queue ──────────────────────────────────────────────────────────

st.markdown('<div class="section-header">Quarantine Queue — Requires Attention</div>', unsafe_allow_html=True)

if not log_df.empty:
    quarantine_df = log_df[log_df["action"].isin(["quarantined", "flagged"])].copy()

    if not quarantine_df.empty:
        quarantine_df["status"] = quarantine_df["action"].map({
            "quarantined": "🔴 Quarantined",
            "flagged":     "🟡 Flagged",
        })
        quarantine_df["score_bar"] = quarantine_df["quality_score"].apply(
            lambda s: f"{s:.1f}/100"
        )
        quarantine_df["failure_summary"] = quarantine_df["failure_reason"].apply(
            lambda r: ", ".join(json.loads(r)[:2]) if r else "—"
        )

        display_cols = {
            "deal_id":        "Deal ID",
            "status":         "Status",
            "quality_score":  "Score",
            "quality_label":  "Label",
            "failure_summary":"Failure Reason",
            "timestamp":      "Timestamp",
        }

        show_df = quarantine_df[list(display_cols.keys())].rename(columns=display_cols).head(20)
        show_df["Timestamp"] = show_df["Timestamp"].dt.strftime("%H:%M:%S")

        st.dataframe(
            show_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Score": st.column_config.ProgressColumn(
                    "Score", min_value=0, max_value=100, format="%.1f"
                ),
            }
        )
        st.caption(f"Showing top 20 of {len(quarantine_df)} records requiring review")
    else:
        st.success("✓ No records in quarantine queue.")
else:
    st.info("No sync events logged yet. Run the sync bridge to populate data.")


# ── Recent Sync Activity ──────────────────────────────────────────────────────

st.markdown('<div class="section-header">Recent Sync Activity</div>', unsafe_allow_html=True)

if not log_df.empty:
    recent = log_df.head(10)[["deal_id", "action", "quality_score", "quality_label", "timestamp"]].copy()
    recent["timestamp"] = recent["timestamp"].dt.strftime("%H:%M:%S")
    recent["action_icon"] = recent["action"].map({
        "synced":      "✅ Synced",
        "flagged":     "⚠️ Flagged",
        "quarantined": "❌ Quarantined",
    })
    recent = recent.rename(columns={
        "deal_id": "Deal ID", "action_icon": "Action",
        "quality_score": "Score", "quality_label": "Label",
        "timestamp": "Time",
    })
    st.dataframe(
        recent[["Deal ID", "Action", "Score", "Label", "Time"]],
        use_container_width=True, hide_index=True,
    )

st.markdown("---")
st.markdown(
    "<span style='color:#475569;font-size:0.72rem'>ERP-CRM Data Integrity Engine · Nikhil Chezian · github.com/NChezian</span>",
    unsafe_allow_html=True
)
