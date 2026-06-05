"""
app.py — Pipeline and Forecast Synthesizer
Phase 2b: HubSpot live data integration via Private App token.

Two input paths feed the same preprocessing and reasoning layers:
  Path A — CSV upload:     user uploads CRM export, maps columns manually
  Path B — HubSpot live:   user pastes Private App token, deals fetched directly,
                           schema mapping is automatic, column mapping step skipped

Both paths produce the same standard DataFrame → preprocess_pipeline() → run_forecast_analysis().
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
from hubspot import fetch_hubspot_pipeline


# ── Helpers ───────────────────────────────────────────────────────────────────

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
if "df_loaded" not in st.session_state:
    st.session_state["df_loaded"] = None          # Loaded DataFrame, either path
if "input_source" not in st.session_state:
    st.session_state["input_source"] = None       # "csv" or "hubspot"
if "hs_schema_report" not in st.session_state:
    st.session_state["hs_schema_report"] = None

# ── Header ────────────────────────────────────────────────────────────────────
st.title("📊 Pipeline & Forecast Synthesizer")
st.markdown(
    "Connect your CRM and get a narrative revenue forecast with concentration risk, "
    "slippage analysis, and leadership-ready actions."
)
st.caption("Part of the Operational Coherence Stack — turning organizational data into executive clarity.")
st.divider()

# ── Input path selector ───────────────────────────────────────────────────────
st.subheader("1. Connect Your Pipeline Data")

tab_hs, tab_csv = st.tabs(["🔗 Connect HubSpot (Live)", "📄 Upload CSV"])

df_raw        = None
column_map    = None
input_source  = None

# ════════════════════════════════════════════════════════════════════════════════
# PATH B — HubSpot live data
# ════════════════════════════════════════════════════════════════════════════════
with tab_hs:
    st.markdown("#### Connect HubSpot directly — no export needed.")
    st.markdown(
        "Paste your HubSpot Private App token below. Your deals are fetched live, "
        "mapped automatically to the standard schema, and the column mapping step is skipped."
    )

    # ── Token instructions (collapsed by default) ─────────────────────────────
    with st.expander("📋 How to get your HubSpot token — step by step", expanded=False):
        st.markdown("""
**What you need:** A HubSpot Private App token. This is different from your API key
(HubSpot deprecated legacy API keys in 2022). Creating one takes about 2 minutes.

---

**Step 1 — Go to Private Apps**
In your HubSpot account, click the **Settings** gear (top right) →
**Integrations** → **Private Apps** → **Create a private app**.

**Step 2 — Name it**
Give it any name, e.g. *"Pipeline Synthesizer"*. The description is optional.

**Step 3 — Set scopes**
Click the **Scopes** tab. Under **CRM**, enable:
- ✅ `crm.objects.deals.read`
- ✅ `crm.objects.owners.read` *(needed to show rep names instead of IDs)*

You don't need write access — this tool only reads.

**Step 4 — Create and copy the token**
Click **Create app** → confirm → copy the token that appears.
It starts with `pat-na1-` (or a similar region prefix).
**Important:** HubSpot only shows the full token once. Copy it now.

**Step 5 — Paste it below**
The token is never stored — it's used only for this session.

---

**Using an AI assistant to navigate HubSpot's settings?**
You can copy and paste this prompt into ChatGPT, Claude, or any AI tool:

> *"I use HubSpot CRM. Walk me through creating a Private App token with
> `crm.objects.deals.read` and `crm.objects.owners.read` scopes so I can
> connect my pipeline data to an external tool. My HubSpot account is
> [your account name/URL if relevant]. Give me step-by-step instructions
> for the current HubSpot UI."*

This is especially useful if HubSpot has updated their UI since this guide was written.

---

**Token security**
- The token is not saved anywhere — it lives only in your browser session.
- Treat it like a password. Don't paste it into a shared screen or commit it to GitHub.
- You can delete the Private App in HubSpot at any time to revoke access.
        """)

    hs_token = st.text_input(
        "HubSpot Private App token",
        type="password",
        placeholder="pat-na1-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
        help="Starts with pat-na1- or similar. Found in HubSpot Settings → Integrations → Private Apps.",
        key="hs_token_input",
    )

    fetch_button = st.button("🔗 Fetch Pipeline from HubSpot", type="primary", key="hs_fetch_btn")

    if fetch_button:
        if not hs_token or not hs_token.strip():
            st.error("Paste your HubSpot Private App token above.")
        else:
            with st.spinner("Connecting to HubSpot and fetching deals..."):
                hs_df, schema_report, hs_error = fetch_hubspot_pipeline(hs_token)

            if hs_error:
                st.error(f"❌ {hs_error}")
            elif hs_df.empty:
                st.error("No deals returned. Check token permissions and try again.")
            else:
                st.session_state["df_loaded"]      = hs_df
                st.session_state["input_source"]   = "hubspot"
                st.session_state["hs_schema_report"] = schema_report
                # Clear prior analysis when new data is loaded
                st.session_state["forecast"] = None
                st.session_state["metrics"]  = None
                st.rerun()

    # Show confirmation if HubSpot data is loaded
    if (
        st.session_state.get("input_source") == "hubspot"
        and st.session_state.get("df_loaded") is not None
    ):
        hs_df = st.session_state["df_loaded"]
        report = st.session_state.get("hs_schema_report", {})
        st.success(
            f"✅ Connected — {report.get('mapped_deals', len(hs_df)):,} deals fetched from HubSpot."
        )

        # Schema mapping summary
        warn_parts = []
        if report.get("missing_owners", 0) > 0:
            warn_parts.append(f"{report['missing_owners']} deals have no assigned owner")
        if report.get("missing_amounts", 0) > 0:
            warn_parts.append(f"{report['missing_amounts']} deals have no amount")
        if warn_parts:
            st.warning("⚠️ " + "; ".join(warn_parts) + ". These will be included but may affect analysis quality.")

        with st.expander("Fields mapped automatically from HubSpot", expanded=False):
            st.markdown(
                "The following fields were mapped automatically — no column mapping needed:\n\n"
                "| HubSpot Property | Standard Field |\n"
                "|---|---|\n"
                "| dealname | Deal Name |\n"
                "| dealstage (resolved to label) | Stage |\n"
                "| amount | Amount |\n"
                "| closedate | Close Date |\n"
                "| hubspot_owner_id (resolved to name) | Owner |\n"
                "| notes_last_updated | Last Activity Date |\n"
                "| createdate | Created Date |\n"
            )

        with st.expander("Preview fetched deals", expanded=False):
            st.dataframe(hs_df.head(10), use_container_width=True)

        df_raw       = hs_df
        input_source = "hubspot"

        # For HubSpot path: build the column_map automatically (identity map)
        column_map = {
            "stage":              "stage",
            "amount":             "amount",
            "close_date":         "close_date",
            "owner":              "owner",
            "deal_name":          "deal_name",
            "last_activity_date": "last_activity_date",
            "created_date":       "created_date",
        }

# ════════════════════════════════════════════════════════════════════════════════
# PATH A — CSV upload
# ════════════════════════════════════════════════════════════════════════════════
with tab_csv:
    st.markdown("#### Upload a CRM export — Salesforce, HubSpot, Attio, or any CSV.")
    st.markdown(
        "1. Export your pipeline as a CSV from your CRM.  \n"
        "2. Upload it below and map your columns to the standard schema.  \n"
        "3. Normalize stage names, set your quarter end date, and run."
    )

    # Sample CSV download
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

    uploaded_file = st.file_uploader(
        "Upload your CRM export",
        type=["csv"],
        label_visibility="collapsed",
        key="csv_upload",
    )

    if uploaded_file is not None:
        try:
            csv_df = pd.read_csv(uploaded_file)
        except Exception as e:
            st.error(f"Could not parse CSV: {e}")
            csv_df = None

        if csv_df is not None and not csv_df.empty:
            st.success(f"Loaded {len(csv_df):,} rows and {len(csv_df.columns)} columns.")
            with st.expander("Preview uploaded data", expanded=False):
                st.dataframe(csv_df.head(10), use_container_width=True)

            st.session_state["df_loaded"]    = csv_df
            st.session_state["input_source"] = "csv"
            st.session_state["forecast"]     = None
            st.session_state["metrics"]      = None
            df_raw       = csv_df
            input_source = "csv"
        elif csv_df is not None:
            st.error("The uploaded file appears to be empty.")

# ── Restore loaded data from session state if page reruns ─────────────────────
if df_raw is None and st.session_state.get("df_loaded") is not None:
    df_raw       = st.session_state["df_loaded"]
    input_source = st.session_state["input_source"]
    if input_source == "hubspot":
        column_map = {
            "stage":              "stage",
            "amount":             "amount",
            "close_date":         "close_date",
            "owner":              "owner",
            "deal_name":          "deal_name",
            "last_activity_date": "last_activity_date",
            "created_date":       "created_date",
        }

if df_raw is None:
    st.info("Connect HubSpot or upload a CSV to get started.")
    st.stop()

st.divider()

# ════════════════════════════════════════════════════════════════════════════════
# COLUMN MAPPING — CSV path only
# HubSpot path skips this entirely (mapping done in hubspot.py)
# ════════════════════════════════════════════════════════════════════════════════
if input_source == "csv":
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

    stage_source_col = map_stage
    step_prefix = "3"
    st.divider()

else:
    # HubSpot path: stage column is already named "stage" in df_raw
    stage_source_col = "stage"
    step_prefix = "2"

# ════════════════════════════════════════════════════════════════════════════════
# STAGE NORMALIZATION — both paths
# ════════════════════════════════════════════════════════════════════════════════
stage_step = f"{step_prefix}. Normalize Stage Names" if input_source == "csv" else "2. Normalize Stage Names"
st.subheader(stage_step)
st.caption(
    "Map your CRM's stage names to the standard funnel stages. "
    + ("HubSpot stage labels have been resolved automatically — review and adjust if needed."
       if input_source == "hubspot" else "")
)

raw_stages = sorted(df_raw[stage_source_col].dropna().unique().tolist()) if stage_source_col in df_raw.columns else []
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
            mapped_stage = st.selectbox(
                f'"{raw_stage}"',
                options=STANDARD_STAGES,
                index=auto_idx,
                key=f"stage_{i}",
            )
            user_stage_map[str(raw_stage)] = mapped_stage
else:
    st.info("No stage values detected — check your data connection above.")

st.divider()

# ════════════════════════════════════════════════════════════════════════════════
# QUARTER PARAMETERS — both paths
# ════════════════════════════════════════════════════════════════════════════════
param_step = ("4. Set Quarter Parameters" if input_source == "csv" else "3. Set Quarter Parameters")
st.subheader(param_step)

today = date.today()
month = today.month
if month <= 3:   default_qe = date(today.year, 3, 31)
elif month <= 6: default_qe = date(today.year, 6, 30)
elif month <= 9: default_qe = date(today.year, 9, 30)
else:            default_qe = date(today.year, 12, 31)

quarter_end = st.date_input(
    "Quarter end date",
    value=default_qe,
    help="Deals closing on or before this date are counted as in-quarter pipeline.",
)

with st.expander("⚙️ Advanced: stage probability overrides", expanded=False):
    st.caption("Adjust to match your historical close rates.")
    stage_probabilities = {}
    prob_cols = st.columns(3)
    for i, stage in enumerate([s for s in STANDARD_STAGES if s not in ("Closed Won", "Closed Lost")]):
        with prob_cols[i % 3]:
            prob = st.slider(stage, 0, 100, int(DEFAULT_PROBABILITIES[stage] * 100), 5, key=f"prob_{stage}")
            stage_probabilities[stage] = prob / 100
    stage_probabilities["Closed Won"]  = 1.0
    stage_probabilities["Closed Lost"] = 0.0

st.divider()

# ════════════════════════════════════════════════════════════════════════════════
# RUN
# ════════════════════════════════════════════════════════════════════════════════
run_step = ("5. Run" if input_source == "csv" else "4. Run")
st.subheader(run_step)

source_label = "HubSpot (live)" if input_source == "hubspot" else "uploaded CSV"
st.caption(f"Data source: {source_label}")

api_key = os.environ.get("ANTHROPIC_API_KEY", "")
if not api_key:
    st.error("ANTHROPIC_API_KEY not found. Add it to Streamlit secrets.")

run_button = st.button("▶ Analyze Pipeline", type="primary", disabled=(not api_key))

if run_button:
    with st.spinner("Processing pipeline data..."):
        prep_result = preprocess_pipeline(
            df=df_raw,
            column_map=column_map,
            user_stage_map=user_stage_map,
            stage_probabilities=stage_probabilities,
            quarter_end=quarter_end,
        )

    quality = prep_result.get("quality", {})
    metrics = prep_result.get("metrics", {})

    if quality.get("flagged") and quality.get("issues"):
        for issue in quality["issues"]:
            st.warning(f"⚠️ {issue}")

    if not metrics:
        st.error("No metrics could be calculated. Check your data and try again.")
        st.stop()

    with st.spinner("Generating forecast analysis — this takes about 60 seconds..."):
        forecast, api_error = run_forecast_analysis(metrics=metrics, quality=quality, api_key=api_key)

    if api_error:
        st.error(f"Analysis error: {api_error}")
        st.stop()

    st.session_state["forecast"] = forecast
    st.session_state["metrics"]  = metrics

# ════════════════════════════════════════════════════════════════════════════════
# OUTPUT — rendered from session state so download doesn't clear it
# ════════════════════════════════════════════════════════════════════════════════
if st.session_state.get("forecast") and st.session_state.get("metrics"):
    forecast = st.session_state["forecast"]
    metrics  = st.session_state["metrics"]
    summary  = metrics.get("summary", {})

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
        st.metric(
            "Top 3 Concentration",
            f"{summary.get('top_3_deals_pct_of_quarter', 0):.1f}%",
            help="% of in-quarter pipeline held by the three largest deals.",
        )

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
            sev   = r.get("severity", "Medium")
            icon  = "🔴" if sev == "High" else "🟡" if sev == "Medium" else "🟢"
            label = r["description"][:80] + "..." if len(r["description"]) > 80 else r["description"]
            label_safe = label.replace("$", r"\$")
            with st.expander(f"{icon} **[{sev}]** {label_safe}", expanded=True):
                safe_text(r["description"])
                if r.get("deal_or_rep"):
                    st.caption(f"Related to: {r['deal_or_rep']}")

    # Revenue Opportunities
    opps = [o for o in forecast.get("revenue_opportunities", []) if o.get("description")]
    if opps:
        st.markdown("### 🚀 Revenue Opportunities")
        for o in opps:
            label = o["description"][:80] + "..." if len(o["description"]) > 80 else o["description"]
            label_safe = label.replace("$", r"\$")
            with st.expander(f"✅ {label_safe}", expanded=False):
                safe_text(o["description"])
                if o.get("upside_scenario"):
                    st.caption(f"Upside: {o['upside_scenario']}")

    st.divider()

    # Rep Insights
    rep_insights = [r for r in forecast.get("rep_insights", []) if r.get("observation")]
    if rep_insights:
        st.markdown("### 👤 Rep Insights")
        for r in rep_insights:
            st.markdown(f"**{r['rep_name']}**")
            safe_text(r["observation"])
            st.caption(f"→ {r['implication']}")

    st.divider()

    # Leadership Actions
    actions = [a for a in forecast.get("leadership_actions", []) if a.get("action")]
    if actions:
        st.markdown("### ⚡ Leadership Actions This Week")
        for i, a in enumerate(actions, 1):
            urgency     = a.get("urgency", "This week")
            icon        = "🔴" if urgency == "This week" else "🟡" if urgency == "Before quarter end" else "🟢"
            action_safe = a["action"].replace("$", r"\$")
            st.markdown(f"**{i}. {action_safe}**")
            st.caption(f"{icon} {urgency} — {a['rationale']}")

    st.divider()

    # Pipeline Data Details (collapsed)
    with st.expander("📊 Pipeline Data Details", expanded=False):
        stage_data = metrics.get("stage_distribution", [])
        if stage_data:
            st.markdown("**Stage Distribution**")
            sdf = pd.DataFrame(stage_data)
            sdf["total_amount"] = sdf["total_amount"].apply(lambda x: f"${x:,.0f}")
            st.dataframe(
                sdf.rename(columns={"stage": "Stage", "total_amount": "Total", "deal_count": "Deals"}),
                use_container_width=True, hide_index=True,
            )

        rep_data = metrics.get("rep_summary", [])
        if rep_data:
            st.markdown("**Rep Summary**")
            rdf = pd.DataFrame(rep_data)
            for col in ["total_pipeline", "weighted_pipeline"]:
                rdf[col] = rdf[col].apply(lambda x: f"${x:,.0f}")
            st.dataframe(
                rdf.rename(columns={
                    "rep": "Rep", "total_pipeline": "Pipeline", "weighted_pipeline": "Weighted",
                    "deal_count": "Deals", "largest_deal_pct_of_pipeline": "Largest Deal %",
                }),
                use_container_width=True, hide_index=True,
            )

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

    def build_export(forecast, metrics, summary, source):
        lines = ["=" * 60, "PIPELINE & FORECAST SYNTHESIS", "=" * 60]
        lines += [
            f"Analysis date:      {summary.get('analysis_date', '')}",
            f"Quarter end:        {summary.get('quarter_end', '')}",
            f"Data source:        {source}",
            f"In-quarter pipeline: ${summary.get('in_quarter_pipeline', 0):,.0f}",
            f"Weighted pipeline:   ${summary.get('in_quarter_weighted', 0):,.0f}",
            "",
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

    export_text = build_export(
        forecast, metrics, summary,
        source="HubSpot (live)" if st.session_state.get("input_source") == "hubspot" else "CSV upload",
    )
    st.download_button(
        label="⬇ Download forecast report (.txt)",
        data=export_text,
        file_name="pipeline_forecast.txt",
        mime="text/plain",
    )
