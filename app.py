"""
app.py — Pipeline and Forecast Synthesizer
Streamlit UI: CSV upload, column mapping, stage normalization, preprocessing preview.
Phase 1: deterministic layer only. Claude reasoning layer added in Phase 2.
"""

import os
import io
from datetime import date, datetime
import pandas as pd
import streamlit as st

from preprocessing import (
    preprocess_pipeline,
    STANDARD_STAGES,
    DEFAULT_PROBABILITIES,
)

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Pipeline & Forecast Synthesizer",
    page_icon="📊",
    layout="wide",
)

# ── Header ────────────────────────────────────────────────────────────────────
st.title("📊 Pipeline & Forecast Synthesizer")
st.markdown(
    "Upload a CRM export and get a narrative revenue forecast with concentration risk, "
    "slippage analysis, and leadership-ready actions."
)
st.caption(
    "Part of the [Operational Coherence Stack](https://github.com/thomasmeerschwam) — "
    "turning organizational data into executive clarity."
)
st.markdown(
    "1. Upload your pipeline CSV (Salesforce, HubSpot, Attio, or any CRM export).  \n"
    "2. Map your columns to the standard schema.  \n"
    "3. Normalize your stage names and set your quarter end date.  \n"
    "4. Run the analysis — get a forecast narrative ready to hand to leadership."
)
st.divider()

# ── Sample CSV download ───────────────────────────────────────────────────────
sample_path = os.path.join(os.path.dirname(__file__), "sample_pipeline.csv")
if os.path.exists(sample_path):
    with open(sample_path, "rb") as f:
        st.download_button(
            label="⬇ Download sample pipeline CSV",
            data=f,
            file_name="sample_pipeline.csv",
            mime="text/csv",
            help="Use this to test the tool without your own CRM data.",
        )
st.divider()

# ── Upload ────────────────────────────────────────────────────────────────────
st.subheader("1. Upload Pipeline CSV")
uploaded_file = st.file_uploader(
    "Upload your CRM export",
    type=["csv"],
    label_visibility="collapsed",
)
st.caption("Supported sources: Salesforce, HubSpot, Attio, Pipedrive, or any CSV export.")

if uploaded_file is None:
    st.info("Upload a CSV file to get started, or download the sample above.")
    st.stop()

# Parse uploaded file
try:
    df_raw = pd.read_csv(uploaded_file)
except Exception as e:
    st.error(f"Could not parse CSV: {e}")
    st.stop()

if df_raw.empty:
    st.error("The uploaded file appears to be empty.")
    st.stop()

st.success(f"Loaded {len(df_raw):,} rows and {len(df_raw.columns)} columns.")

with st.expander("Preview uploaded data", expanded=False):
    st.dataframe(df_raw.head(10), use_container_width=True)

st.divider()

# ── Column mapping ────────────────────────────────────────────────────────────
st.subheader("2. Map Your Columns")
st.caption(
    "Match your CRM's column names to the standard schema. "
    "Required fields are marked with ★."
)

available_cols = ["— not mapped —"] + list(df_raw.columns)

def col_selector(label: str, key: str, required: bool = False, default_hint: str = "") -> str:
    marker = " ★" if required else ""
    # Try to auto-detect by matching hint
    default_idx = 0
    if default_hint:
        for i, c in enumerate(available_cols):
            if default_hint.lower() in c.lower():
                default_idx = i
                break
    selected = st.selectbox(
        f"{label}{marker}",
        options=available_cols,
        index=default_idx,
        key=key,
    )
    return selected if selected != "— not mapped —" else ""

col1, col2 = st.columns(2)

with col1:
    map_stage      = col_selector("Deal Stage",        "map_stage",      required=True,  default_hint="stage")
    map_amount     = col_selector("Deal Amount",       "map_amount",     required=True,  default_hint="amount")
    map_close_date = col_selector("Close Date",        "map_close_date", required=True,  default_hint="close")
    map_owner      = col_selector("Deal Owner / Rep",  "map_owner",      required=True,  default_hint="owner")

with col2:
    map_deal_name      = col_selector("Deal Name",          "map_deal_name",      default_hint="deal_name")
    map_last_activity  = col_selector("Last Activity Date", "map_last_activity",  default_hint="activity")
    map_created_date   = col_selector("Created Date",       "map_created_date",   default_hint="created")
    map_segment        = col_selector("Segment / Vertical", "map_segment",        default_hint="segment")

column_map = {
    "stage":              map_stage,
    "amount":             map_amount,
    "close_date":         map_close_date,
    "owner":              map_owner,
    "deal_name":          map_deal_name,
    "last_activity_date": map_last_activity,
    "created_date":       map_created_date,
    "segment":            map_segment,
}

# Validate required mappings
missing_required = [k for k in ["stage", "amount", "close_date", "owner"] if not column_map[k]]
if missing_required:
    st.warning(f"Map required fields before continuing: {', '.join(missing_required)}")
    st.stop()

st.divider()

# ── Stage normalization ───────────────────────────────────────────────────────
st.subheader("3. Normalize Stage Names")
st.caption(
    "Map your CRM's stage names to the standard funnel stages. "
    "Unmapped stages use your original names and may affect probability weighting."
)

# Get unique stage values from the mapped column
raw_stages = sorted(df_raw[map_stage].dropna().unique().tolist()) if map_stage else []

user_stage_map = {}
if raw_stages:
    stage_cols = st.columns(2)
    for i, raw_stage in enumerate(raw_stages):
        with stage_cols[i % 2]:
            # Try to auto-detect
            auto_idx = 0
            key_lower = str(raw_stage).strip().lower()
            for j, ss in enumerate(STANDARD_STAGES):
                if key_lower in ss.lower() or ss.lower() in key_lower:
                    auto_idx = j
                    break
            mapped_stage = st.selectbox(
                f'"{raw_stage}"',
                options=STANDARD_STAGES,
                index=auto_idx,
                key=f"stage_{i}",
            )
            user_stage_map[str(raw_stage)] = mapped_stage
else:
    st.info("No stage values detected — check your column mapping above.")

st.divider()

# ── Options ───────────────────────────────────────────────────────────────────
st.subheader("4. Set Quarter Parameters")

today = date.today()
# Default quarter end: last day of current quarter
month = today.month
if month <= 3:
    default_qe = date(today.year, 3, 31)
elif month <= 6:
    default_qe = date(today.year, 6, 30)
elif month <= 9:
    default_qe = date(today.year, 9, 30)
else:
    default_qe = date(today.year, 12, 31)

quarter_end = st.date_input(
    "Quarter end date",
    value=default_qe,
    help="Deals closing on or before this date are counted as in-quarter pipeline.",
)

with st.expander("⚙️ Advanced: stage probability overrides", expanded=False):
    st.caption(
        "Default close probabilities by stage. Adjust to match your historical close rates."
    )
    stage_probabilities = {}
    prob_cols = st.columns(3)
    active_stages = [s for s in STANDARD_STAGES if s not in ("Closed Won", "Closed Lost")]
    for i, stage in enumerate(active_stages):
        with prob_cols[i % 3]:
            prob = st.slider(
                stage,
                min_value=0,
                max_value=100,
                value=int(DEFAULT_PROBABILITIES[stage] * 100),
                step=5,
                key=f"prob_{stage}",
            )
            stage_probabilities[stage] = prob / 100
    stage_probabilities["Closed Won"] = 1.0
    stage_probabilities["Closed Lost"] = 0.0

st.divider()

# ── Run ───────────────────────────────────────────────────────────────────────
st.subheader("Run")

api_key = os.environ.get("ANTHROPIC_API_KEY", "")
phase2_ready = bool(api_key)

if not phase2_ready:
    st.info(
        "**Analysis preview mode.** "
        "Pipeline metrics will be calculated below. "
        "To generate the full narrative forecast, add your ANTHROPIC_API_KEY to Streamlit secrets."
    )

run_button = st.button("▶ Analyze Pipeline", type="primary")

if run_button:
    with st.spinner("Processing pipeline data..."):
        result = preprocess_pipeline(
            df=df_raw,
            column_map=column_map,
            user_stage_map=user_stage_map,
            stage_probabilities=stage_probabilities,
            quarter_end=quarter_end,
        )

    quality = result.get("quality", {})
    metrics = result.get("metrics", {})
    warnings = result.get("warnings", [])

    # Quality warnings
    if quality.get("flagged") and quality.get("issues"):
        for issue in quality["issues"]:
            st.warning(f"⚠️ {issue}")

    if not metrics:
        st.error("No metrics could be calculated. Check your column mapping and try again.")
        st.stop()

    st.divider()

    # ── Pipeline summary ──────────────────────────────────────────────────────
    st.subheader("📈 Pipeline Summary")

    summary = metrics.get("summary", {})
    m1, m2, m3, m4 = st.columns(4)

    with m1:
        st.metric(
            "In-Quarter Pipeline",
            f"${summary.get('in_quarter_pipeline', 0):,.0f}",
            help="Total deal value closing on or before quarter end.",
        )
    with m2:
        st.metric(
            "Weighted Pipeline",
            f"${summary.get('in_quarter_weighted', 0):,.0f}",
            help="Pipeline weighted by stage probability.",
        )
    with m3:
        st.metric(
            "In-Quarter Deals",
            summary.get("in_quarter_deal_count", 0),
        )
    with m4:
        st.metric(
            "Top 3 Deal Concentration",
            f"{summary.get('top_3_deals_pct_of_quarter', 0):.1f}%",
            help="Percentage of in-quarter pipeline held by the three largest deals.",
        )

    # ── Stage distribution ────────────────────────────────────────────────────
    st.markdown("**Stage Distribution (In-Quarter)**")
    stage_data = metrics.get("stage_distribution", [])
    if stage_data:
        stage_df = pd.DataFrame(stage_data)
        stage_df["total_amount"] = stage_df["total_amount"].apply(lambda x: f"${x:,.0f}")
        st.dataframe(
            stage_df.rename(columns={
                "stage": "Stage",
                "total_amount": "Total Amount",
                "deal_count": "Deal Count",
            }),
            use_container_width=True,
            hide_index=True,
        )

    # ── Rep summary ───────────────────────────────────────────────────────────
    rep_data = metrics.get("rep_summary", [])
    if rep_data:
        st.markdown("**Rep Pipeline Summary**")
        rep_df = pd.DataFrame(rep_data)
        rep_df["total_pipeline"] = rep_df["total_pipeline"].apply(lambda x: f"${x:,.0f}")
        rep_df["weighted_pipeline"] = rep_df["weighted_pipeline"].apply(lambda x: f"${x:,.0f}")
        rep_df["largest_deal"] = rep_df["largest_deal"].apply(lambda x: f"${x:,.0f}")
        st.dataframe(
            rep_df.rename(columns={
                "rep": "Rep",
                "total_pipeline": "Total Pipeline",
                "weighted_pipeline": "Weighted",
                "deal_count": "Deals",
                "largest_deal": "Largest Deal",
                "largest_deal_pct_of_pipeline": "Largest Deal %",
                "late_stage_deal_count": "Late Stage Deals",
                "late_stage_amount": "Late Stage Amount",
            }),
            use_container_width=True,
            hide_index=True,
        )

    # ── Slippage ──────────────────────────────────────────────────────────────
    slippage = metrics.get("slippage", {})
    if slippage.get("count", 0) > 0:
        st.markdown(f"**⚠️ Slipped Deals ({slippage['count']} deal(s) past close date)**")
        slip_df = pd.DataFrame(slippage["deals"])
        if "amount" in slip_df.columns:
            slip_df["amount"] = slip_df["amount"].apply(lambda x: f"${x:,.0f}")
        st.dataframe(slip_df, use_container_width=True, hide_index=True)

    # ── Stale activity ────────────────────────────────────────────────────────
    stale = metrics.get("stale_activity", {})
    if stale.get("count", 0) > 0:
        st.markdown(f"**🕐 Stale Deals — No Activity in 14+ Days ({stale['count']} deal(s))**")
        stale_df = pd.DataFrame(stale["deals"])
        if "amount" in stale_df.columns:
            stale_df["amount"] = stale_df["amount"].apply(lambda x: f"${x:,.0f}")
        st.dataframe(stale_df, use_container_width=True, hide_index=True)

    st.divider()

    if phase2_ready:
        st.info("Claude reasoning layer coming in Phase 2 — narrative forecast will appear here.")
    else:
        st.info(
            "**Narrative forecast unavailable.** "
            "Add ANTHROPIC_API_KEY to Streamlit secrets to enable the full analysis."
        )
