"""
prompt.py — Reasoning layer for Pipeline and Forecast Synthesizer.

Multi-turn forced tool call approach. Each section is generated via a
separate API call with tool_choice forced to that specific tool.
This guarantees all six sections fire regardless of input structure.

Six forced calls:
  1. write_executive_summary
  2. assess_forecast_confidence
  3. identify_revenue_risks (any — multiple calls allowed)
  4. identify_leverage_points (any — multiple calls allowed)
  5. generate_rep_insights (any — one per rep)
  6. generate_leadership_actions (any — exactly 3, one per intervention layer)
"""

import anthropic


# ── Tool definitions ───────────────────────────────────────────────────────────

TOOL_EXECUTIVE_SUMMARY = {
    "name": "write_executive_summary",
    "description": (
        "Write the executive summary — what is most likely to happen this quarter "
        "based on the current pipeline. Direct, specific, grounded in actual numbers. "
        "State the likely outcome, the primary driver, and the single most important "
        "thing leadership needs to know. 2-4 sentences."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "narrative": {
                "type": "string",
                "description": "2-4 sentence executive summary. Lead with the likely outcome. Name specific numbers.",
            }
        },
        "required": ["narrative"],
    },
}

TOOL_FORECAST_CONFIDENCE = {
    "name": "assess_forecast_confidence",
    "description": (
        "Assess how much confidence leadership should have in this forecast. "
        "Explain specifically why — concentration risk, stage distribution, activity signals. "
        "Assign a confidence level."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "narrative": {
                "type": "string",
                "description": "2-3 sentences on why the forecast is or isn't trustworthy. Reference actual numbers.",
            },
            "confidence_level": {
                "type": "string",
                "enum": ["High", "Medium", "Low"],
            },
        },
        "required": ["narrative", "confidence_level"],
    },
}

TOOL_REVENUE_RISKS = {
    "name": "identify_revenue_risks",
    "description": (
        "Identify one specific risk that could prevent the quarter from closing as forecast. "
        "Focus on concentration risk, slippage, stale deals, unowned pipeline. "
        "Be specific — name deals, amounts, reps. Call this once per distinct risk."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "Specific risk. Name deals or reps. State the revenue impact. Reason only from provided data.",
            },
            "severity": {"type": "string", "enum": ["High", "Medium", "Low"]},
            "deal_or_rep": {
                "type": "string",
                "description": "Deal or rep name if applicable. Null if systemic.",
            },
        },
        "required": ["description", "severity"],
    },
}

TOOL_LEVERAGE_POINTS = {
    "name": "identify_leverage_points",
    "description": (
        "Identify one specific leverage point or path to outperformance in the current pipeline. "
        "Do not simply restate that a deal could close — identify what makes this deal, rep, or "
        "cohort a genuine upside lever: recent engagement signals, favorable stage velocity, "
        "deal clustering that could be accelerated together, or a rep whose pipeline health "
        "is stronger than the aggregate suggests. "
        "A leverage point answers 'where should leadership focus attention to beat the forecast?' "
        "not just 'what deals exist?'. "
        "Call this once per distinct leverage point."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": (
                    "Specific leverage point. Name the deal, rep, or cohort. "
                    "Explain what makes this an outperformance lever — not just a deal summary. "
                    "State what action or condition would convert this into realized upside."
                ),
            },
            "upside_scenario": {
                "type": "string",
                "description": "One sentence on what this means for the quarter if the lever is pulled.",
            },
        },
        "required": ["description", "upside_scenario"],
    },
}

TOOL_REP_INSIGHTS = {
    "name": "generate_rep_insights",
    "description": (
        "Generate an insight about one rep's pipeline health. "
        "Look for concentration within their book, stale activity, single-deal dependency. "
        "If a rep has limited data, still produce a brief observation about their pipeline "
        "contribution and what it means for Q3 coverage — do not leave the analysis blank. "
        "Call once per rep. If only one owner exists, call once for that owner. "
        "If unowned deals exist, call once for 'Unowned'."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "rep_name": {"type": "string"},
            "observation": {
                "type": "string",
                "description": (
                    "What the data shows about this rep. Reference amounts and deal counts. "
                    "If data is sparse, note the pipeline contribution and coverage implications — "
                    "never return a blank or placeholder observation."
                ),
            },
            "implication": {
                "type": "string",
                "description": "What leadership should do or watch for.",
            },
        },
        "required": ["rep_name", "observation", "implication"],
    },
}

TOOL_LEADERSHIP_ACTIONS = {
    "name": "generate_leadership_actions",
    "description": (
        "Generate one concrete action leadership should take this week. "
        "Specific and immediately actionable — not generic sales advice. "
        "Call this exactly 3 times total, once per intervention layer."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "description": "Specific action. Name deals, reps, or amounts."},
            "rationale": {"type": "string", "description": "One sentence on why this matters right now."},
            "urgency": {"type": "string", "enum": ["This week", "Before quarter end", "Monitor"]},
        },
        "required": ["action", "rationale", "urgency"],
    },
}


# ── Prompt construction ────────────────────────────────────────────────────────

def build_system_prompt() -> str:
    return """\
You are an expert Chief Revenue Officer and revenue operations analyst. \
Turn pre-computed pipeline metrics into executive-grade forecast judgment.

Core analytical frame:
- Signal vs judgment: "3.2x coverage" is a signal. "The forecast risk is concentration, \
not volume" is judgment. Produce the latter.
- Always check whether aggregate metrics mask concentrated risk.
- Leadership language only — no CRM field names, no stage jargon.

Evidence discipline:
- Reason only from data provided. Do not infer fields that weren't in the input.
- "No activity in 21 days" is defensible. "No executive sponsorship" is not unless \
that field exists.\
"""


def build_metrics_prompt(metrics: dict, quality: dict) -> str:
    """Build the shared metrics context passed to every API call."""
    lines = ["## PIPELINE METRICS"]
    summary = metrics.get("summary", {})
    lines += [
        f"Analysis date: {summary.get('analysis_date', 'unknown')}",
        f"Quarter end: {summary.get('quarter_end', 'unknown')}",
        f"Total active pipeline: ${summary.get('total_active_pipeline', 0):,.0f}",
        f"In-quarter pipeline: ${summary.get('in_quarter_pipeline', 0):,.0f}",
        f"Weighted in-quarter pipeline: ${summary.get('in_quarter_weighted', 0):,.0f}",
        f"Total active deals: {summary.get('total_deal_count', 0)}",
        f"In-quarter deals: {summary.get('in_quarter_deal_count', 0)}",
        f"Top 3 deals as % of in-quarter pipeline: {summary.get('top_3_deals_pct_of_quarter', 0):.1f}%",
        "",
    ]

    stage_dist = metrics.get("stage_distribution", [])
    if stage_dist:
        lines.append("### Stage Distribution (In-Quarter)")
        for s in stage_dist:
            lines.append(f"  {s['stage']}: ${s['total_amount']:,.0f} across {s['deal_count']} deal(s)")
        lines.append("")

    top_deals = metrics.get("top_deals", [])
    if top_deals:
        lines.append("### Top 5 Deals")
        for d in top_deals:
            name = d.get("deal_name", "Unnamed")
            line = f"  {name} | {d.get('stage')} | ${d.get('amount', 0):,.0f} | Owner: {d.get('owner', 'unassigned')} | Close: {d.get('close_date')}"
            if d.get("last_activity_date"):
                line += f" | Last activity: {d['last_activity_date']}"
            lines.append(line)
        lines.append("")

    slippage = metrics.get("slippage", {})
    if slippage.get("count", 0) > 0:
        lines.append(f"### Slipped Deals ({slippage['count']} past close date)")
        for d in slippage.get("deals", []):
            lines.append(f"  {d.get('deal_name','Unnamed')} | {d.get('stage')} | ${d.get('amount',0):,.0f} | due {d.get('close_date')}")
        lines.append("")

    stale = metrics.get("stale_activity", {})
    if stale.get("count", 0) > 0:
        lines.append(f"### Stale Deals — No Activity 14+ Days ({stale['count']})")
        for d in stale.get("deals", []):
            lines.append(f"  {d.get('deal_name','Unnamed')} | {d.get('stage')} | ${d.get('amount',0):,.0f} | {d.get('days_since_activity')} days")
        lines.append("")

    rep_summary = metrics.get("rep_summary", [])
    if rep_summary:
        lines.append("### Rep Pipeline Summary")
        for r in rep_summary:
            lines.append(
                f"  {r['rep']}: ${r['total_pipeline']:,.0f} total | ${r['weighted_pipeline']:,.0f} weighted | "
                f"{r['deal_count']} deals | largest ${r['largest_deal']:,.0f} ({r['largest_deal_pct_of_pipeline']:.0f}% of their pipeline)"
            )
        lines.append("")

    conc = metrics.get("concentration", {})
    lines += [
        "### Concentration",
        f"Top 3 deals: ${conc.get('top_3_amount', 0):,.0f} ({conc.get('top_3_pct_of_quarter', 0):.1f}% of in-quarter pipeline)",
        "",
    ]

    if quality.get("issues"):
        lines.append("### Data quality notes")
        for issue in quality["issues"]:
            lines.append(f"  - {issue}")
        lines.append("")

    return "\n".join(lines)


# ── Forced single-tool calls ───────────────────────────────────────────────────

def _forced_call(client, system, metrics_prompt, tool, user_instruction, max_tokens=1024):
    """Make a single forced tool call and return the input dict."""
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=max_tokens,
            system=system,
            tools=[tool],
            tool_choice={"type": "tool", "name": tool["name"]},
            messages=[{"role": "user", "content": f"{metrics_prompt}\n\n{user_instruction}"}],
        )
        for block in response.content:
            if block.type == "tool_use":
                return block.input
    except Exception:
        pass
    return {}


def _any_call(client, system, metrics_prompt, tool, user_instruction, max_tokens=2048):
    """Make an 'any' tool call allowing multiple invocations. Returns list of inputs."""
    results = []
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=max_tokens,
            system=system,
            tools=[tool],
            tool_choice={"type": "any"},
            messages=[{"role": "user", "content": f"{metrics_prompt}\n\n{user_instruction}"}],
        )
        for block in response.content:
            if block.type == "tool_use":
                results.append(block.input)
    except Exception:
        pass
    return results


# ── Main entry point ───────────────────────────────────────────────────────────

def run_forecast_analysis(
    metrics: dict,
    quality: dict,
    api_key: str,
) -> tuple[dict, str | None]:
    """
    Run the Claude reasoning layer over pre-computed pipeline metrics.
    Uses six separate forced API calls to guarantee all sections fire.
    """
    client = anthropic.Anthropic(api_key=api_key)
    system = build_system_prompt()
    mp = build_metrics_prompt(metrics, quality)

    result = {
        "executive_summary": "",
        "forecast_confidence": {"narrative": "", "level": "Medium"},
        "revenue_risks": [],
        "revenue_opportunities": [],
        "rep_insights": [],
        "leadership_actions": [],
    }

    try:
        # ── 1. Executive Summary ──────────────────────────────────────────────
        r = _forced_call(client, system, mp, TOOL_EXECUTIVE_SUMMARY,
            "Write the executive summary for this pipeline.")
        result["executive_summary"] = r.get("narrative", "")

        # ── 2. Forecast Confidence ────────────────────────────────────────────
        r = _forced_call(client, system, mp, TOOL_FORECAST_CONFIDENCE,
            "Assess the forecast confidence level and explain why.")
        result["forecast_confidence"] = {
            "narrative": r.get("narrative", ""),
            "level": r.get("confidence_level", "Medium"),
        }

        # ── 3. Revenue Risks ──────────────────────────────────────────────────
        risks_raw = _any_call(client, system, mp, TOOL_REVENUE_RISKS,
            "Identify all distinct revenue risks in this pipeline. "
            "Call identify_revenue_risks once per distinct risk. Aim for 2-4 risks.",
            max_tokens=2048)
        result["revenue_risks"] = [
            {"description": r.get("description", ""), "severity": r.get("severity", "Medium"),
             "deal_or_rep": r.get("deal_or_rep")}
            for r in risks_raw
        ]

        # ── 4. Leverage Points (Paths to Outperformance) ──────────────────────
        # Reframed from "Revenue Opportunities" to focus on genuine leverage:
        # where should leadership direct attention to beat the forecast?
        # Not just a list of deals that could close.
        opps_raw = _any_call(client, system, mp, TOOL_LEVERAGE_POINTS,
            "Identify the specific leverage points in this pipeline — where could "
            "leadership focus attention to outperform the weighted forecast? "
            "Look for: deals with recent positive engagement, rep books that are "
            "healthier than the aggregate suggests, deal cohorts that could be "
            "accelerated together, or early-close signals. "
            "Do not simply list deals as opportunities — identify what makes each "
            "a genuine lever and what action converts it to upside. "
            "Call identify_leverage_points once per distinct leverage point. Aim for 3-5.",
            max_tokens=2048)
        result["revenue_opportunities"] = [
            {"description": r.get("description", ""), "upside_scenario": r.get("upside_scenario", "")}
            for r in opps_raw
            if r.get("description", "").strip() and r.get("upside_scenario", "").strip()
        ]

        # ── 5. Rep Insights ───────────────────────────────────────────────────
        rep_summary = metrics.get("rep_summary", [])
        rep_names = [r["rep"] for r in rep_summary] if rep_summary else []
        has_unowned = any(
            not r.get("owner") or str(r.get("owner", "")).strip().lower() in
            {"", "null", "none", "unassigned", "tbd"}
            for row in [metrics]
            for r in metrics.get("rep_summary", [])
        )
        rep_context = f"Reps in pipeline: {', '.join(rep_names) if rep_names else 'unknown'}."
        if has_unowned:
            rep_context += " There are also unowned deals with no assigned rep."

        rep_raw = _any_call(client, system, mp, TOOL_REP_INSIGHTS,
            f"Generate rep insights. {rep_context} "
            "Call generate_rep_insights once per rep. "
            "For reps with limited data, still produce a brief observation about "
            "pipeline contribution and Q3 coverage implications — never return blank. "
            "Include an insight for 'Unowned' if unowned deals exist.",
            max_tokens=1024)
        result["rep_insights"] = [
            {"rep_name": r.get("rep_name", ""), "observation": r.get("observation", ""),
             "implication": r.get("implication", "")}
            for r in rep_raw
            if r.get("observation", "").strip()
        ]

        # ── 6. Leadership Actions ─────────────────────────────────────────────
        # Force exactly 3 by making 3 sequential calls, one per intervention layer.
        # Layer 1: Deal intervention — specific deal-level action
        # Layer 2: Rep intervention — rep capacity, coverage, or accountability action
        # Layer 3: Process intervention — structural or organizational action
        # Each call receives prior actions to prevent repetition.
        action_layers = [
            (
                "DEAL INTERVENTION: Generate one action focused on a specific deal or set of deals. "
                "Name the deal(s), the dollar amount at stake, and the exact step needed this week "
                "(e.g. executive call, contract review, buyer outreach). "
                "This is action 1 of 3."
            ),
            (
                "REP INTERVENTION: Generate one action focused on rep capacity, accountability, "
                "or pipeline ownership. This should address something about how the rep is managing "
                "their book — coverage gaps, bandwidth, forecast hygiene, or coaching needs. "
                "Do not repeat the deal-level action already identified. "
                "This is action 2 of 3."
            ),
            (
                "PROCESS INTERVENTION: Generate one action focused on an organizational or structural "
                "improvement — pipeline hygiene, ownership assignment, stage criteria, Q3 coverage "
                "planning, or deal desk readiness. This should address a systemic issue, not a "
                "specific deal or rep already covered. "
                "This is action 3 of 3."
            ),
        ]

        for i, layer_instruction in enumerate(action_layers):
            prior_actions = [a["action"][:80] for a in result["leadership_actions"]]
            prior_note = (
                f" Actions already identified: {prior_actions}. "
                "Do not repeat or restate these."
            ) if prior_actions else ""
            r = _forced_call(client, system, mp, TOOL_LEADERSHIP_ACTIONS,
                f"{layer_instruction}{prior_note}",
                max_tokens=512)
            if r.get("action"):
                result["leadership_actions"].append({
                    "action": r.get("action", ""),
                    "rationale": r.get("rationale", ""),
                    "urgency": r.get("urgency", "This week"),
                })

    except anthropic.APIConnectionError as e:
        return {}, f"Connection error: {e}"
    except anthropic.RateLimitError:
        return {}, "Rate limit reached. Please wait a moment and try again."
    except anthropic.APIStatusError as e:
        return {}, f"API error {e.status_code}: {e.message}"
    except Exception as e:
        return {}, f"Unexpected error: {e}"

    # Guard: executive summary is the minimum viable output
    if not result["executive_summary"]:
        return {}, (
            "Analysis returned no results. Please try again — "
            "if the problem persists, check that your pipeline data contains sufficient content."
        )

    return result, None
