"""
preprocessing.py — Deterministic layer for Pipeline and Forecast Synthesizer.

Runs before Claude sees anything. Responsible for:
  - CSV parsing and validation
  - Stage normalization
  - Derived metric calculation (coverage, weighted pipeline, slippage, concentration)
  - Rep-level analysis
  - Quality scoring

Returns a structured dict that prompt.py consumes.
No API calls. No Claude. Pure Python.
"""

import pandas as pd
from datetime import date, datetime
from typing import Optional


# ── Stage configuration ────────────────────────────────────────────────────────

STANDARD_STAGES = [
    "Prospecting",
    "Qualification",
    "Discovery",
    "Proposal",
    "Negotiation",
    "Closed Won",
    "Closed Lost",
]

DEFAULT_PROBABILITIES = {
    "Prospecting":   0.10,
    "Qualification": 0.25,
    "Discovery":     0.40,
    "Proposal":      0.60,
    "Negotiation":   0.80,
    "Closed Won":    1.00,
    "Closed Lost":   0.00,
}

STAGE_ALIASES = {
    "prospect":          "Prospecting",
    "prospecting":       "Prospecting",
    "lead":              "Prospecting",
    "new":               "Prospecting",
    "open":              "Prospecting",
    "qualification":     "Qualification",
    "qualifying":        "Qualification",
    "qualified":         "Qualification",
    "mql":               "Qualification",
    "sql":               "Qualification",
    "discovery":         "Discovery",
    "exploring":         "Discovery",
    "needs analysis":    "Discovery",
    "evaluation":        "Discovery",
    "proposal":          "Proposal",
    "demo":              "Proposal",
    "presented":         "Proposal",
    "value proposition": "Proposal",
    "negotiation":       "Negotiation",
    "negotiating":       "Negotiation",
    "verbal":            "Negotiation",
    "commit":            "Negotiation",
    "contract":          "Negotiation",
    "closing":           "Negotiation",
    "closed won":        "Closed Won",
    "won":               "Closed Won",
    "closed-won":        "Closed Won",
    "closed lost":       "Closed Lost",
    "lost":              "Closed Lost",
    "closed-lost":       "Closed Lost",
}


# ── Required and optional columns ─────────────────────────────────────────────

REQUIRED_COLUMNS = ["stage", "amount", "close_date", "owner"]
OPTIONAL_COLUMNS = ["deal_name", "last_activity_date", "created_date", "deal_type", "segment"]


def parse_csv(df: pd.DataFrame, column_map: dict) -> tuple[pd.DataFrame, list[str]]:
    """
    Apply user-supplied column mapping and validate required fields.
    column_map: {standard_field: user_column_name}
    Returns (mapped_df, list_of_warnings).
    """
    warnings = []
    mapped = {}

    for standard, user_col in column_map.items():
        if user_col and user_col in df.columns:
            mapped[standard] = df[user_col]
        elif standard in REQUIRED_COLUMNS:
            warnings.append(f"Required column '{standard}' not mapped or not found.")

    result = pd.DataFrame(mapped)

    # Parse amount
    if "amount" in result.columns:
        result["amount"] = (
            result["amount"]
            .astype(str)
            .str.replace(r"[$,€£\s]", "", regex=True)
            .pipe(pd.to_numeric, errors="coerce")
            .fillna(0)
        )

    # Parse dates
    for date_col in ["close_date", "last_activity_date", "created_date"]:
        if date_col in result.columns:
            result[date_col] = pd.to_datetime(result[date_col], errors="coerce").dt.date

    # Drop rows missing required fields
    before = len(result)
    for col in REQUIRED_COLUMNS:
        if col in result.columns:
            result = result[result[col].notna()]
    dropped = before - len(result)
    if dropped > 0:
        warnings.append(f"{dropped} row(s) dropped due to missing required fields.")

    return result, warnings


def normalize_stage(stage_raw: str, user_stage_map: dict) -> str:
    """
    Normalize a raw stage value to a standard stage.
    user_stage_map: {raw_stage_name: standard_stage} — user overrides.
    Falls back to alias lookup, then returns raw value if unrecognized.
    """
    if not isinstance(stage_raw, str):
        return "Unknown"
    if stage_raw in user_stage_map:
        return user_stage_map[stage_raw]
    key = stage_raw.strip().lower()
    if key in STAGE_ALIASES:
        return STAGE_ALIASES[key]
    return stage_raw.strip()


def calculate_metrics(
    df: pd.DataFrame,
    stage_probabilities: dict,
    quarter_end: date,
    today: Optional[date] = None,
) -> dict:
    """
    Compute all derived metrics from the normalized pipeline DataFrame.
    Returns a structured metrics dict that Claude reasons over.
    """
    if today is None:
        today = date.today()

    # Active pipeline — exclude closed
    active = df[~df["stage"].isin(["Closed Won", "Closed Lost"])].copy()
    # In-quarter — close date on or before quarter end
    if "close_date" in active.columns:
        in_quarter = active[active["close_date"] <= quarter_end].copy()
    else:
        in_quarter = active.copy()

    total_pipeline = float(active["amount"].sum())
    in_quarter_pipeline = float(in_quarter["amount"].sum())

    # Weighted pipeline
    def get_weighted(row):
        return row["amount"] * stage_probabilities.get(row["stage"], 0.0)

    active["weighted_amount"] = active.apply(get_weighted, axis=1)
    in_quarter["weighted_amount"] = in_quarter.apply(get_weighted, axis=1)
    in_quarter_weighted = float(in_quarter["weighted_amount"].sum())

    # Stage distribution
    stage_dist = (
        in_quarter.groupby("stage")["amount"]
        .agg(["sum", "count"])
        .rename(columns={"sum": "total_amount", "count": "deal_count"})
        .reset_index()
    )
    stage_dist["total_amount"] = stage_dist["total_amount"].round(0)
    stage_summary = stage_dist.to_dict("records")

    # Concentration
    top_3_amount = float(in_quarter.nlargest(3, "amount")["amount"].sum())
    top_3_pct = round(top_3_amount / max(in_quarter_pipeline, 1) * 100, 1)

    top_deal_cols = ["stage", "amount", "owner", "close_date"]
    if "deal_name" in in_quarter.columns:
        top_deal_cols = ["deal_name"] + top_deal_cols
    if "last_activity_date" in in_quarter.columns:
        top_deal_cols.append("last_activity_date")
    top_deals = in_quarter.nlargest(5, "amount")[top_deal_cols].copy()
    for dc in ["close_date", "last_activity_date"]:
        if dc in top_deals.columns:
            top_deals[dc] = top_deals[dc].astype(str)
    top_deals["amount"] = top_deals["amount"].round(0)
    top_deals_records = top_deals.to_dict("records")

    # Slippage — close date already passed
    slippage_deals = []
    if "close_date" in active.columns:
        past = active[active["close_date"] < today].copy()
        slip_cols = ["stage", "amount", "owner", "close_date"]
        if "deal_name" in past.columns:
            slip_cols = ["deal_name"] + slip_cols
        slippage_deals = past[slip_cols].to_dict("records")
        for d in slippage_deals:
            if "close_date" in d:
                d["close_date"] = str(d["close_date"])

    # Activity staleness
    stale_deals = []
    if "last_activity_date" in active.columns:
        active["days_since_activity"] = active["last_activity_date"].apply(
            lambda d: (today - d).days if pd.notna(d) and isinstance(d, date) else None
        )
        stale = active[
            active["days_since_activity"].notna() &
            (active["days_since_activity"] >= 14)
        ].copy()
        stale_cols = ["stage", "amount", "owner", "days_since_activity"]
        if "deal_name" in stale.columns:
            stale_cols = ["deal_name"] + stale_cols
        stale_deals = (
            stale[stale_cols]
            .sort_values("days_since_activity", ascending=False)
            .head(10)
            .to_dict("records")
        )

    # Rep analysis
    rep_summary = []
    if "owner" in in_quarter.columns:
        for rep, group in in_quarter.groupby("owner"):
            rep_total = float(group["amount"].sum())
            rep_weighted = float(group["weighted_amount"].sum())
            rep_top = float(group["amount"].max())
            rep_top_pct = round(rep_top / max(rep_total, 1) * 100, 1)
            late = group[group["stage"].isin(["Proposal", "Negotiation"])]
            rep_summary.append({
                "rep": rep,
                "total_pipeline": round(rep_total, 0),
                "weighted_pipeline": round(rep_weighted, 0),
                "deal_count": len(group),
                "largest_deal": round(rep_top, 0),
                "largest_deal_pct_of_pipeline": rep_top_pct,
                "late_stage_deal_count": len(late),
                "late_stage_amount": round(float(late["amount"].sum()), 0),
            })
        rep_summary.sort(key=lambda x: x["total_pipeline"], reverse=True)

    # Deal age
    avg_deal_age = None
    if "created_date" in active.columns:
        active["deal_age_days"] = active["created_date"].apply(
            lambda d: (today - d).days if pd.notna(d) and isinstance(d, date) else None
        )
        valid = active["deal_age_days"].dropna()
        if len(valid) > 0:
            avg_deal_age = round(float(valid.mean()), 0)

    return {
        "summary": {
            "total_active_pipeline":   round(total_pipeline, 0),
            "in_quarter_pipeline":     round(in_quarter_pipeline, 0),
            "in_quarter_weighted":     round(in_quarter_weighted, 0),
            "total_deal_count":        len(active),
            "in_quarter_deal_count":   len(in_quarter),
            "top_3_deals_pct_of_quarter": top_3_pct,
            "avg_deal_age_days":       avg_deal_age,
            "quarter_end":             str(quarter_end),
            "analysis_date":           str(today),
        },
        "stage_distribution":  stage_summary,
        "top_deals":           top_deals_records,
        "slippage": {
            "count": len(slippage_deals),
            "deals": slippage_deals[:10],
        },
        "stale_activity": {
            "count": len(stale_deals),
            "deals": stale_deals,
        },
        "rep_summary":  rep_summary,
        "concentration": {
            "top_3_amount":          round(top_3_amount, 0),
            "top_3_pct_of_quarter":  top_3_pct,
        },
        "available_fields": list(df.columns),
    }


def score_quality(df: pd.DataFrame, metrics: dict, warnings: list[str]) -> dict:
    """Quality gate — returns flagged=True when issues require user attention."""
    issues = list(warnings)

    if len(df) < 5:
        issues.append("Fewer than 5 deals — analysis may not be meaningful.")

    if "stage" in df.columns:
        unrecognized = [
            s for s in df["stage"].unique()
            if s not in STANDARD_STAGES and s != "Unknown"
        ]
        if unrecognized:
            issues.append(
                f"Unrecognized stage(s): {', '.join(str(s) for s in unrecognized[:5])}. "
                "Map these in the stage mapper for accurate probability weighting."
            )

    if "last_activity_date" not in df.columns:
        issues.append(
            "No last activity date column mapped. "
            "Activity staleness analysis is unavailable — this limits rep insights."
        )

    if "amount" in df.columns:
        zero_amt = (df["amount"] == 0).sum()
        if zero_amt > 0:
            issues.append(f"{zero_amt} deal(s) have zero or missing amount.")

    return {
        "flagged":            len(issues) > 0,
        "issues":             issues,
        "deal_count":         len(df),
        "has_activity_dates": "last_activity_date" in df.columns,
        "has_created_dates":  "created_date" in df.columns,
        "has_deal_names":     "deal_name" in df.columns,
        "has_segments":       "segment" in df.columns,
    }


def preprocess_pipeline(
    df: pd.DataFrame,
    column_map: dict,
    user_stage_map: dict,
    stage_probabilities: dict,
    quarter_end: date,
) -> dict:
    """
    Full preprocessing pipeline. Called by app.py before the Claude API call.

    Args:
        df:                  Raw uploaded DataFrame
        column_map:          {standard_field: user_column_name}
        user_stage_map:      {raw_stage_name: standard_stage}
        stage_probabilities: {standard_stage: float}
        quarter_end:         Last day of the current quarter

    Returns:
        {
            "metrics":  {...},   # derived metrics Claude reasons over
            "quality":  {...},   # quality flags
            "warnings": [...],   # warning strings for UI
        }
    """
    mapped_df, warnings = parse_csv(df, column_map)

    if mapped_df.empty:
        return {
            "metrics":  {},
            "quality":  {"flagged": True, "issues": ["No valid rows after parsing."], "deal_count": 0},
            "warnings": warnings,
        }

    if "stage" in mapped_df.columns:
        mapped_df["stage"] = mapped_df["stage"].apply(
            lambda s: normalize_stage(str(s), user_stage_map)
        )

    metrics = calculate_metrics(mapped_df, stage_probabilities, quarter_end)
    quality = score_quality(mapped_df, metrics, warnings)

    return {
        "metrics":  metrics,
        "quality":  quality,
        "warnings": warnings,
    }
