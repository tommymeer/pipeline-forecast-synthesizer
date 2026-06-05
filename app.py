"""
app.py — Pipeline and Forecast Synthesizer
Phase 2: Full reasoning layer. Results stored in session state so download doesn't clear output.
"""

import os
from datetime import date
import pandas as pd
import streamlit as st

from preprocessing import (
    preprocess_pipeline,
    STANDARD_STAGES,
    DEFAULT_PROBABILITIES,
)
from prompt import run_forecast_analysis

def safe_text(text: str):
    """Render narrative text safely — escapes dollar signs so Streamlit
    doesn't interpret amounts like $438,600 as markdown math."""
    st.markdown(text.replace("$", r"\$"))

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Pipeline & Forecast Synthesizer",
    page_icon="📊",
    layout="wide",
)

# ── Session state init ────────────────────────────────────────────────────────
if "forecast" not in st.session_state:
    st.session_state["forecast"] = None
if "metrics" not in st.session_state:
    st.session_state["metrics"] = None

# ── Header ────────────────────────────────────────────────────────────────────
st.title("📊 Pipeline & Forecast Synthesizer")
st.markdown(
    "Upload a CRM export and get a narrative revenue forecast with concentration risk, "
    "slippage analysis, and leadership-ready actions."
)
st.caption("Part of the Operational Coherence Stack — turning organizational data into executive clarity.")
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
st.caption("Supported sources: Salesforce, HubSpot, Attio, or any CSV export.")

if uploaded_file is None:
    st.info("Upload a CSV file to get started, or download the sample above.")
    st.stop()

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
st.caption("Match your CRM's column names to the standard schema. Required fields are marked with ★.")

available_cols = ["— not mapped —"] + list(df_raw.columns)

def col_selector(label, key, required=False, default_hint=""):
    marker = " ★" if required else ""
    default_idx = 0
    if default_hint:
        for i, c in enumerate(available_cols):
            if default_hint.lower() in c.lower():
                default_idx = i
                break
    selected = st.selectbox(f"{label}{marker}", options=available_cols, index=default_idx, key=key)
    return selected if selected != "— not mapped —" else ""

col1, col2 = st.columns(2)
with col1:
    map_stage      = col_selector("Deal Stage",       "map_stage",      required=True, default_hint="stage")
    map_amount     = col_selector("Deal Amount",      "map_amount",     required=True, default_hint="amount")
    map_close_date = col_selector("Close Date",       "map_close_date", required=True, default_hint="close")
    map_owner      = col_selector("Deal Owner / Rep", "map_owner",      required=True, default_hint="owner")
with col2:
    map_deal_name     = col_selector("Deal Name",          "map_deal_name",     default_hint="deal_name")
    map_last_activity = col_selector("Last Activity Date", "map_last_activity", default_hint="activity")
    map_created_date  = col_selector("Created Date",       "map_created_date",  default_hint="created")
    map_segment       = col_selector("Segment / Vertical", "map_segment",       default_hint="segment")

column_map = {
    "stage": map_stage, "amount": map_amount, "close_date": map_close_date,
    "owner": map_owner, "deal_name": map_deal_name, "last_activity_date": map_last_activity,
    "created_date": map_created_date, "segment": map_segment,
}

missing_required = [k for k in ["stage", "amount", "close_date", "owner"] if not column_map[k]]
if missing_required:
    st.warning(f"Map required fields before continuing: {', '.join(missing_required)}")
    st.stop()

st.divider()

# ── Stage normalization ───────────────────────────────────────────────────────
st.subheader("3. Normalize Stage Names")
st.caption("Map your CRM's stage names to the standard funnel stages.")

raw_stages = sorted(df_raw[map_stage].dropna().unique().tolist()) if map_stage else []
user_stage_map = {}
if raw_stages:
    stage_cols = st.columns(2)
    for i, raw_stage in enumerate(raw_stages):
        with stage_cols[i % 2]:
            auto_idx = 0
            key_lower = str(raw_stage).strip().lower()
            for j, ss in enumerate(STANDARD_STAGES):
                if key_lower in ss.lower() or ss.lower() in key_lower:
                    auto_idx = j
                    break
            mapped_stage = st.selectbox(f'"{raw_stage}"', options=STANDARD_STAGES, index=auto_idx, key=f"stage_{i}")
            user_stage_map[str(raw_stage)] = mapped_stage
else:
    st.info("No stage values detected — check your column mapping above.")

st.divider()

# ── Options ───────────────────────────────────────────────────────────────────
st.subheader("4. Set Quarter Parameters")

today = date.today()
month = today.month
if month <= 3:   default_qe = date(today.year, 3, 31)
elif month <= 6: default_qe = date(today.year, 6, 30)
elif month <= 9: default_qe = date(today.year, 9, 30)
else:            default_qe = date(today.year, 12, 31)

quarter_end = st.date_input("Quarter end date", value=default_qe,
    help="Deals closing on or before this date are counted as in-quarter pipeline.")

with st.expander("⚙️ Advanced: stage probability overrides", expanded=False):
    st.caption("Adjust to match your historical close rates.")
    stage_probabilities = {}
    prob_cols = st.columns(3)
    for i, stage in enumerate([s for s in STANDARD_STAGES if s not in ("Closed Won", "Closed Lost")]):
        with prob_cols[i % 3]:
            prob = st.slider(stage, 0, 100, int(DEFAULT_PROBABILITIES[stage] * 100), 5, key=f"prob_{stage}")
            stage_probabilities[stage] = prob / 100
    stage_probabilities["Closed Won"] = 1.0
    stage_probabilities["Closed Lost"] = 0.0

st.divider()

# ── Run ───────────────────────────────────────────────────────────────────────
st.subheader("Run")
api_key = os.environ.get("ANTHROPIC_API_KEY", "")
if not api_key:
    st.error("ANTHROPIC_API_KEY not found. Add it to Streamlit secrets.")

run_button = st.button("▶ Analyze Pipeline", type="primary", disabled=(not api_key))

if run_button:
    with st.spinner("Processing pipeline data..."):
        prep_result = preprocess_pipeline(
            df=df_raw, column_map=column_map, user_stage_map=user_stage_map,
            stage_probabilities=stage_probabilities, quarter_end=quarter_end,
        )

    quality = prep_result.get("quality", {})
    metrics = prep_result.get("metrics", {})

    if quality.get("flagged") and quality.get("issues"):
        for issue in quality["issues"]:
            st.warning(f"⚠️ {issue}")

    if not metrics:
        st.error("No metrics could be calculated. Check your column mapping and try again.")
        st.stop()

    with st.spinner("Generating forecast analysis with Claude..."):
        forecast, api_error = run_forecast_analysis(metrics=metrics, quality=quality, api_key=api_key)

    if api_error:
        st.error(f"Analysis error: {api_error}")
        st.stop()

    st.session_state["forecast"] = forecast
    st.session_state["metrics"] = metrics

# ── Output — rendered from session state so download doesn't clear it ─────────
if st.session_state.get("forecast") and st.session_state.get("metrics"):
    forecast = st.session_state["forecast"]
    metrics = st.session_state["metrics"]
    summary = metrics.get("summary", {})

    st.divider()

    # Metrics row
    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.metric("In-Quarter Pipeline", f"${summary.get('in_quarter_pipeline', 0):,.0f}")
    with m2:
        st.metric("Weighted Pipeline", f"${summary.get('in_quarter_weighted', 0):,.0f}")
    with m3:
        st.metric("In-Quarter Deals", summary.get("in_quarter_deal_count", 0))
    with m4:
        st.metric("Top 3 Concentration", f"{summary.get('top_3_deals_pct_of_quarter', 0):.1f}%",
            help="% of in-quarter pipeline held by the three largest deals.")

    st.divider()
    st.subheader("📋 Forecast Analysis")

    # Executive Summary
    if forecast.get("executive_summary"):
        st.markdown("### Executive Summary")
        safe_text(forecast["executive_summary"])

    # Forecast Confidence
    fc = forecast.get("forecast_confidence", {})
    if fc.get("narrative"):
        level = fc.get("level", "Medium")
        badge = "🟢" if level == "High" else "🟡" if level == "Medium" else "🔴"
        st.markdown(f"### Forecast Confidence: {badge} {level}")
        safe_text(fc["narrative"])

    st.divider()

    # Revenue Risks
    risks = forecast.get("revenue_risks", [])
    if risks:
        st.markdown("### 🚧 Revenue Risks")
        for r in risks:
            sev = r.get("severity", "Medium")
            icon = "🔴" if sev == "High" else "🟡" if sev == "Medium" else "🟢"
            label = r["description"][:80] + "..." if len(r["description"]) > 80 else r["description"]
            with st.expander(f"{icon} **[{sev}]** {label}", expanded=True):
                safe_text(r["description"])
                if r.get("deal_or_rep"):
                    st.caption(f"Related to: {r['deal_or_rep']}")

    # Revenue Opportunities
    opps = forecast.get("revenue_opportunities", [])
    if opps:
        st.markdown("### 🚀 Revenue Opportunities")
        for o in opps:
            label = o["description"][:80] + "..." if len(o["description"]) > 80 else o["description"]
            with st.expander(f"✅ {label}", expanded=False):
                safe_text(o["description"])
                st.caption(f"Upside: {o['upside_scenario']}")

    st.divider()

    # Rep Insights
    rep_insights = forecast.get("rep_insights", [])
    if rep_insights:
        st.markdown("### 👤 Rep Insights")
        for r in rep_insights:
            st.markdown(f"**{r['rep_name']}**")
            safe_text(r["observation"])
            st.caption(f"→ {r['implication']}")

    st.divider()

    # Leadership Actions
    actions = forecast.get("leadership_actions", [])
    if actions:
        st.markdown("### ⚡ Leadership Actions This Week")
        for i, a in enumerate(actions, 1):
            urgency = a.get("urgency", "This week")
            icon = "🔴" if urgency == "This week" else "🟡" if urgency == "Before quarter end" else "🟢"
            st.markdown(f"**{i}. {a['action']}**")
            st.caption(f"{icon} {urgency} — {a['rationale']}")

    st.divider()

    # Pipeline data details (collapsed)
    with st.expander("📊 Pipeline Data Details", expanded=False):
        stage_data = metrics.get("stage_distribution", [])
        if stage_data:
            st.markdown("**Stage Distribution**")
            sdf = pd.DataFrame(stage_data)
            sdf["total_amount"] = sdf["total_amount"].apply(lambda x: f"${x:,.0f}")
            st.dataframe(sdf.rename(columns={"stage": "Stage", "total_amount": "Total", "deal_count": "Deals"}),
                use_container_width=True, hide_index=True)

        rep_data = metrics.get("rep_summary", [])
        if rep_data:
            st.markdown("**Rep Summary**")
            rdf = pd.DataFrame(rep_data)
            for col in ["total_pipeline", "weighted_pipeline"]:
                rdf[col] = rdf[col].apply(lambda x: f"${x:,.0f}")
            st.dataframe(rdf.rename(columns={
                "rep": "Rep", "total_pipeline": "Pipeline", "weighted_pipeline": "Weighted",
                "deal_count": "Deals", "largest_deal_pct_of_pipeline": "Largest Deal %",
            }), use_container_width=True, hide_index=True)

        slippage = metrics.get("slippage", {})
        if slippage.get("count", 0) > 0:
            st.markdown(f"**⚠️ Slipped Deals ({slippage['count']})**")
            st.dataframe(pd.DataFrame(slippage["deals"]), use_container_width=True, hide_index=True)

        stale = metrics.get("stale_activity", {})
        if stale.get("count", 0) > 0:
            st.markdown(f"**🕐 Stale Deals — 14+ Days No Activity ({stale['count']})**")
            st.dataframe(pd.DataFrame(stale["deals"]), use_container_width=True, hide_index=True)

    # Export
    st.divider()

    def build_export(forecast, metrics, summary):
        lines = ["=" * 60, "PIPELINE & FORECAST SYNTHESIS", "=" * 60]
        lines += [
            f"Analysis date: {summary.get('analysis_date', '')}",
            f"Quarter end: {summary.get('quarter_end', '')}",
            f"In-quarter pipeline: ${summary.get('in_quarter_pipeline', 0):,.0f}",
            f"Weighted pipeline: ${summary.get('in_quarter_weighted', 0):,.0f}", "",
        ]
        if forecast.get("executive_summary"):
            lines += ["## EXECUTIVE SUMMARY", forecast["executive_summary"], ""]
        fc = forecast.get("forecast_confidence", {})
        if fc.get("narrative"):
            lines += [f"## FORECAST CONFIDENCE: {fc.get('level','Medium').upper()}", fc["narrative"], ""]
        if forecast.get("revenue_risks"):
            lines.append("## REVENUE RISKS")
            for r in forecast["revenue_risks"]:
                lines.append(f"[{r['severity']}] {r['description']}")
            lines.append("")
        if forecast.get("revenue_opportunities"):
            lines.append("## REVENUE OPPORTUNITIES")
            for o in forecast["revenue_opportunities"]:
                lines += [f"• {o['description']}", f"  Upside: {o['upside_scenario']}"]
            lines.append("")
        if forecast.get("rep_insights"):
            lines.append("## REP INSIGHTS")
            for r in forecast["rep_insights"]:
                lines += [f"{r['rep_name']}: {r['observation']}", f"  → {r['implication']}"]
            lines.append("")
        if forecast.get("leadership_actions"):
            lines.append("## LEADERSHIP ACTIONS")
            for i, a in enumerate(forecast["leadership_actions"], 1):
                lines += [f"{i}. [{a['urgency']}] {a['action']}", f"   {a['rationale']}"]
        return "\n".join(lines)

    export_text = build_export(forecast, metrics, summary)
    st.download_button(
        label="⬇ Download forecast report (.txt)",
        data=export_text,
        file_name="pipeline_forecast.txt",
        mime="text/plain",
    )
