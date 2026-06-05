"""
prompt.py — Reasoning layer for Pipeline and Forecast Synthesizer.

Single-pass Claude API call using structured tool use.
Claude receives pre-computed metrics from preprocessing.py and produces
six sections of executive-grade forecast judgment.

Six tools map to six output sections:
  write_executive_summary
  assess_forecast_confidence
  identify_revenue_risks
  identify_revenue_opportunities
  generate_rep_insights
  generate_leadership_actions

No raw CSV data is passed to Claude — only structured metrics.
"""

import json
import anthropic


# ── Tool definitions ───────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "write_executive_summary",
        "description": (
            "Write the executive summary — what is most likely to happen this quarter "
            "based on the current pipeline. This is the first thing leadership reads. "
            "It must be direct, specific, and grounded in the actual numbers. "
            "State the likely outcome, the primary driver of that outcome, and the "
            "single most important thing leadership needs to know. 2-4 sentences."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "narrative": {
                    "type": "string",
                    "description": (
                        "2-4 sentence executive summary. Lead with the likely outcome. "
                        "Name specific numbers. Do not use CRM jargon or field names."
                    ),
                }
            },
            "required": ["narrative"],
        },
    },
    {
        "name": "assess_forecast_confidence",
        "description": (
            "Assess how much confidence leadership should have in this forecast. "
            "Explain specifically why the forecast should or should not be trusted — "
            "concentration risk, data quality, stage distribution, activity signals. "
            "Assign a confidence level and explain the reasoning."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "narrative": {
                    "type": "string",
                    "description": (
                        "2-3 sentences explaining why the forecast is or isn't trustworthy. "
                        "Be specific — reference actual numbers and patterns from the data."
                    ),
                },
                "confidence_level": {
                    "type": "string",
                    "enum": ["High", "Medium", "Low"],
                    "description": (
                        "High: forecast is reliable, well-distributed, active pipeline. "
                        "Medium: some concentration or activity gaps but overall credible. "
                        "Low: heavily concentrated, stale, or thin pipeline."
                    ),
                },
            },
            "required": ["narrative", "confidence_level"],
        },
    },
    {
        "name": "identify_revenue_risks",
        "description": (
            "Identify the specific risks that could prevent the quarter from closing as forecast. "
            "Focus on concentration risk, slippage patterns, stale deals, and unowned pipeline. "
            "Each risk should be specific and actionable — not generic warnings. "
            "Call this once per distinct risk. Call it 2-4 times total."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": (
                        "Specific risk description. Name deals, amounts, or reps where relevant. "
                        "State what could go wrong and what the revenue impact would be. "
                        "Reason only from data that was provided — do not infer fields that weren't present."
                    ),
                },
                "severity": {
                    "type": "string",
                    "enum": ["High", "Medium", "Low"],
                },
                "deal_or_rep": {
                    "type": "string",
                    "description": "Deal name or rep name this risk is associated with, if applicable. Use null if systemic.",
                },
            },
            "required": ["description", "severity"],
        },
    },
    {
        "name": "identify_revenue_opportunities",
        "description": (
            "Identify specific upside opportunities in the current pipeline. "
            "What deals or patterns suggest the quarter could close above the weighted forecast? "
            "Focus on momentum signals, late-stage deals, and expansion potential. "
            "Call this once per distinct opportunity. Call it 1-3 times total."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": (
                        "Specific opportunity description. Name deals or amounts where relevant. "
                        "State what the upside scenario looks like and what would need to happen."
                    ),
                },
                "upside_scenario": {
                    "type": "string",
                    "description": "One sentence on what closing this opportunity would mean for the quarter.",
                },
            },
            "required": ["description", "upside_scenario"],
        },
    },
    {
        "name": "generate_rep_insights",
        "description": (
            "Generate insights about individual rep pipeline health. "
            "Look for concentration risk within a rep's book, reps carrying unowned deals, "
            "reps with stale activity, and reps outperforming weighted expectations. "
            "Call this once per rep with a meaningful insight. Skip reps with nothing notable. "
            "If all deals have the same owner or no activity data exists, note that explicitly "
            "rather than fabricating insights."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "rep_name": {
                    "type": "string",
                    "description": "Rep name, or 'Unowned' for deals with no assigned owner.",
                },
                "observation": {
                    "type": "string",
                    "description": (
                        "What the pipeline data shows about this rep. "
                        "Be specific — reference amounts, deal counts, or activity data. "
                        "Only state what the available data supports."
                    ),
                },
                "implication": {
                    "type": "string",
                    "description": "What leadership should do or watch for based on this observation.",
                },
            },
            "required": ["rep_name", "observation", "implication"],
        },
    },
    {
        "name": "generate_leadership_actions",
        "description": (
            "Generate exactly three concrete actions leadership should take this week "
            "based on the current pipeline state. These should be specific and immediately actionable — "
            "not generic sales advice. Each action should address a specific risk or opportunity "
            "identified in the analysis."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "Specific action. Name deals, reps, or amounts where relevant.",
                },
                "rationale": {
                    "type": "string",
                    "description": "One sentence on why this action matters right now.",
                },
                "urgency": {
                    "type": "string",
                    "enum": ["This week", "Before quarter end", "Monitor"],
                },
            },
            "required": ["action", "rationale", "urgency"],
        },
    },
]


# ── System prompt ──────────────────────────────────────────────────────────────

def build_system_prompt() -> str:
    return """\
You are an expert Chief Revenue Officer and revenue operations analyst. \
Your job is to turn pre-computed pipeline metrics into executive-grade forecast judgment.

You have been given structured pipeline metrics — not raw CRM data. \
The metrics have already been calculated by a deterministic preprocessing layer. \
Your job is to reason over them and produce judgment, not recalculate numbers.

Your core analytical frame:
- The difference between a signal and judgment: "3.2x coverage" is a signal. \
"The forecast risk is concentration, not volume" is judgment. Produce the latter.
- Always ask whether aggregate metrics are masking concentrated risk before \
characterizing pipeline health. A healthy-looking pipeline can be one deal away from a bad quarter.
- Leadership language: your output should be readable by a board member with no CRM context. \
No field names, no stage jargon, no technical terminology from the data.

Evidence discipline:
- Reason only from the data provided. If a field was not in the input \
(champion status, MEDDICC fields, stakeholder mapping, next steps), do not infer it.
- "No activity recorded in the past 21 days" is defensible. \
"This deal lacks executive sponsorship" is not unless that field exists.
- When data is limited, say so explicitly rather than fabricating sophistication.

Use the tools to record each section of the analysis. \
Call identify_revenue_risks 2-4 times for distinct risks. \
Call identify_revenue_opportunities 1-3 times for distinct opportunities. \
Call generate_rep_insights once per rep with a meaningful observation. \
Call generate_leadership_actions exactly 3 times — one per action.\

Extraction invariants — you must call all six tools before finishing:
- write_executive_summary: required, exactly once.
- assess_forecast_confidence: required, exactly once.
- identify_revenue_risks: required, at least twice.
- identify_revenue_opportunities: required, at least once.
- generate_rep_insights: required for every rep with data. If only one owner \
exists, call it once for that owner. If unowned deals exist, call it for "Unowned".
- generate_leadership_actions: required, exactly 3 times. Non-negotiable. \
Do not terminate without 3 leadership actions.\
"""


# ── User prompt ────────────────────────────────────────────────────────────────

def build_user_prompt(metrics: dict, quality: dict) -> str:
    lines = []
    # Checklist injected at the TOP — Claude reads this before the data,
    # priming it to cover all six tool types before finishing.
    lines.append("## EXTRACTION INSTRUCTIONS")
    lines.append(
        "Work through the pipeline metrics and call tools for every section. "
        "Before you finish, confirm you have called each of these tools:\n"
        "- write_executive_summary: call this first, exactly once.\n"
        "- assess_forecast_confidence: call this second, exactly once.\n"
        "- identify_revenue_risks: call this 2-4 times, once per distinct risk.\n"
        "- identify_revenue_opportunities: call this 1-3 times, once per opportunity.\n"
        "- generate_rep_insights: call this once per rep in the rep summary. Do not skip.\n"
        "- generate_leadership_actions: call this exactly 3 times. This is the last step. "
        "Do not finish without 3 leadership actions. An empty section is only correct if "
        "you actively looked and found nothing — not if you stopped early."
    )
    lines.append("")
    lines.append("## PIPELINE METRICS")
    lines.append("The following metrics have been pre-computed from the pipeline data.")
    lines.append("")


    summary = metrics.get("summary", {})
    lines.append("### Summary")
    lines.append(f"Analysis date: {summary.get('analysis_date', 'unknown')}")
    lines.append(f"Quarter end: {summary.get('quarter_end', 'unknown')}")
    lines.append(f"Total active pipeline: ${summary.get('total_active_pipeline', 0):,.0f}")
    lines.append(f"In-quarter pipeline (closing by quarter end): ${summary.get('in_quarter_pipeline', 0):,.0f}")
    lines.append(f"Weighted in-quarter pipeline: ${summary.get('in_quarter_weighted', 0):,.0f}")
    lines.append(f"Total active deals: {summary.get('total_deal_count', 0)}")
    lines.append(f"In-quarter deals: {summary.get('in_quarter_deal_count', 0)}")
    lines.append(f"Top 3 deals as % of in-quarter pipeline: {summary.get('top_3_deals_pct_of_quarter', 0):.1f}%")
    if summary.get("avg_deal_age_days"):
        lines.append(f"Average deal age: {summary['avg_deal_age_days']:.0f} days")
    lines.append("")

    stage_dist = metrics.get("stage_distribution", [])
    if stage_dist:
        lines.append("### Stage Distribution (In-Quarter)")
        for s in stage_dist:
            lines.append(f"  {s['stage']}: ${s['total_amount']:,.0f} across {s['deal_count']} deal(s)")
        lines.append("")

    top_deals = metrics.get("top_deals", [])
    if top_deals:
        lines.append("### Top 5 Deals by Amount (In-Quarter)")
        for d in top_deals:
            name = d.get("deal_name", "Unnamed deal")
            stage = d.get("stage", "unknown")
            amount = d.get("amount", 0)
            owner = d.get("owner", "unassigned")
            close = d.get("close_date", "unknown")
            activity = d.get("last_activity_date", None)
            line = f"  {name} | {stage} | ${amount:,.0f} | Owner: {owner} | Close: {close}"
            if activity:
                line += f" | Last activity: {activity}"
            lines.append(line)
        lines.append("")

    slippage = metrics.get("slippage", {})
    if slippage.get("count", 0) > 0:
        lines.append(f"### Slipped Deals ({slippage['count']} deal(s) past close date)")
        for d in slippage.get("deals", []):
            name = d.get("deal_name", "Unnamed")
            lines.append(f"  {name} | {d.get('stage')} | ${d.get('amount', 0):,.0f} | was due {d.get('close_date')}")
        lines.append("")

    stale = metrics.get("stale_activity", {})
    if stale.get("count", 0) > 0:
        lines.append(f"### Stale Deals — No Activity in 14+ Days ({stale['count']} deal(s))")
        for d in stale.get("deals", []):
            name = d.get("deal_name", "Unnamed")
            lines.append(f"  {name} | {d.get('stage')} | ${d.get('amount', 0):,.0f} | {d.get('days_since_activity')} days since activity")
        lines.append("")

    rep_summary = metrics.get("rep_summary", [])
    if rep_summary:
        lines.append("### Rep Pipeline Summary (In-Quarter)")
        for r in rep_summary:
            lines.append(
                f"  {r['rep']}: ${r['total_pipeline']:,.0f} total | "
                f"${r['weighted_pipeline']:,.0f} weighted | "
                f"{r['deal_count']} deals | "
                f"largest deal ${r['largest_deal']:,.0f} ({r['largest_deal_pct_of_pipeline']:.0f}% of their pipeline) | "
                f"{r['late_stage_deal_count']} late-stage deals"
            )
        lines.append("")

    concentration = metrics.get("concentration", {})
    lines.append("### Concentration")
    lines.append(f"Top 3 deals total: ${concentration.get('top_3_amount', 0):,.0f}")
    lines.append(f"Top 3 as % of in-quarter pipeline: {concentration.get('top_3_pct_of_quarter', 0):.1f}%")
    lines.append("")

    available = metrics.get("available_fields", [])
    lines.append(f"### Available data fields: {', '.join(available)}")
    lines.append("")

    if quality.get("issues"):
        lines.append("### Data quality notes")
        for issue in quality["issues"]:
            lines.append(f"  - {issue}")
        lines.append("")

    lines.append("## INSTRUCTIONS")
    lines.append(
        "Use the tools to produce the six sections of the forecast analysis. "
        "Reason like a CRO preparing for a board meeting — surface what isn't visible "
        "in the aggregate numbers, name the risk that matters most, and produce "
        "language leadership can use directly. "
        "Do not recalculate numbers already provided above — interpret them."
    )
    return "\n".join(lines)


# ── Response parsing ───────────────────────────────────────────────────────────

def parse_tool_calls(response) -> dict:
    """
    Walk response content blocks and assemble structured output from tool calls.
    Returns the six-section dict that app.py renders.
    """
    result = {
        "executive_summary": "",
        "forecast_confidence": {"narrative": "", "level": "Medium"},
        "revenue_risks": [],
        "revenue_opportunities": [],
        "rep_insights": [],
        "leadership_actions": [],
    }

    for block in response.content:
        if block.type != "tool_use":
            continue
        name = block.name
        inp = block.input

        if name == "write_executive_summary":
            result["executive_summary"] = inp.get("narrative", "")

        elif name == "assess_forecast_confidence":
            result["forecast_confidence"] = {
                "narrative": inp.get("narrative", ""),
                "level": inp.get("confidence_level", "Medium"),
            }

        elif name == "identify_revenue_risks":
            result["revenue_risks"].append({
                "description": inp.get("description", ""),
                "severity": inp.get("severity", "Medium"),
                "deal_or_rep": inp.get("deal_or_rep"),
            })

        elif name == "identify_revenue_opportunities":
            result["revenue_opportunities"].append({
                "description": inp.get("description", ""),
                "upside_scenario": inp.get("upside_scenario", ""),
            })

        elif name == "generate_rep_insights":
            result["rep_insights"].append({
                "rep_name": inp.get("rep_name", ""),
                "observation": inp.get("observation", ""),
                "implication": inp.get("implication", ""),
            })

        elif name == "generate_leadership_actions":
            result["leadership_actions"].append({
                "action": inp.get("action", ""),
                "rationale": inp.get("rationale", ""),
                "urgency": inp.get("urgency", "This week"),
            })

    return result


# ── Main entry point ───────────────────────────────────────────────────────────

def run_forecast_analysis(
    metrics: dict,
    quality: dict,
    api_key: str,
) -> tuple[dict, str | None]:
    """
    Run the Claude reasoning layer over pre-computed pipeline metrics.

    Args:
        metrics:  Output from preprocessing.calculate_metrics()
        quality:  Output from preprocessing.score_quality()
        api_key:  Anthropic API key

    Returns:
        (result_dict, error_string_or_None)
    """
    client = anthropic.Anthropic(api_key=api_key)
    system_prompt = build_system_prompt()
    user_prompt = build_user_prompt(metrics, quality)

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8192,
            system=system_prompt,
            tools=TOOLS,
            tool_choice={"type": "any"},
            messages=[{"role": "user", "content": user_prompt}],
        )
    except anthropic.APIConnectionError as e:
        return {}, f"Connection error: {e}"
    except anthropic.RateLimitError:
        return {}, "Rate limit reached. Please wait a moment and try again."
    except anthropic.APIStatusError as e:
        return {}, f"API error {e.status_code}: {e.message}"
    except Exception as e:
        return {}, f"Unexpected error: {e}"

    result = parse_tool_calls(response)

    # Guard: if all sections empty, Claude may have returned prose instead of tool calls
    if (
        not result["executive_summary"]
        and not result["revenue_risks"]
        and not result["leadership_actions"]
    ):
        return {}, (
            "Analysis returned no results. Please try again — "
            "if the problem persists, check that your pipeline data contains sufficient content."
        )

    return result, None
