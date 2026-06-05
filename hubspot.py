"""
hubspot.py — HubSpot live data integration for Pipeline and Forecast Synthesizer.

Fetches deals directly from the HubSpot CRM API using a Private App token.
Maps HubSpot's native deal properties to the standard schema used by preprocessing.py.
Returns a DataFrame and a schema_report dict — same contract as parse_csv() in preprocessing.py.

Auth: HubSpot Private App token (not OAuth). User pastes token into the Streamlit UI.
The column mapping step is skipped entirely for HubSpot — field mapping happens here.

No OAuth redirect in v1. OAuth auto-redirect is a production-tier consideration
(requires a registered HubSpot app, callback URL, and token refresh logic).
For single-user and low-volume portfolio use, the Private App token path is
the correct approach: simpler, more secure for personal use, and fully functional.
"""

import requests
import pandas as pd
from datetime import datetime, date
from typing import Optional


# ── HubSpot API config ─────────────────────────────────────────────────────────

HUBSPOT_API_BASE = "https://api.hubapi.com"
DEALS_ENDPOINT   = f"{HUBSPOT_API_BASE}/crm/v3/objects/deals"
MAX_DEALS        = 500   # Hard cap to keep preprocessing performant

# HubSpot native deal properties → standard schema field names
# These are the default HubSpot property names. Custom properties may differ.
PROPERTY_MAP = {
    "dealname":                "deal_name",
    "dealstage":               "stage",
    "amount":                  "amount",
    "closedate":               "close_date",
    "hubspot_owner_id":        "_owner_id",     # Resolved to name via owners endpoint
    "notes_last_updated":      "last_activity_date",
    "createdate":              "created_date",
    "deal_type":               "deal_type",     # Optional — not always present
}

# Properties to request from HubSpot (their native names)
REQUESTED_PROPERTIES = list(PROPERTY_MAP.keys()) + ["hs_object_id"]


# ── Owner resolution ───────────────────────────────────────────────────────────

def fetch_owners(token: str) -> dict[str, str]:
    """
    Fetch all HubSpot owners and return {owner_id: full_name}.
    Used to resolve hubspot_owner_id to a human-readable rep name.
    Returns empty dict on failure — deals will show owner ID instead of name.
    """
    url = f"{HUBSPOT_API_BASE}/crm/v3/owners"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        owners = {}
        for o in resp.json().get("results", []):
            owner_id = str(o.get("id", ""))
            first    = o.get("firstName", "")
            last     = o.get("lastName", "")
            email    = o.get("email", "")
            name     = f"{first} {last}".strip() or email or owner_id
            owners[owner_id] = name
        return owners
    except Exception:
        return {}


# ── Deal pagination ────────────────────────────────────────────────────────────

def fetch_all_deals(token: str, max_deals: int = MAX_DEALS) -> tuple[list[dict], Optional[str]]:
    """
    Page through HubSpot deals API and return all deal property dicts.
    Respects max_deals cap. Returns (deals_list, error_string_or_None).
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    }
    params = {
        "limit":      100,
        "properties": ",".join(REQUESTED_PROPERTIES),
    }

    all_deals = []
    after     = None

    while len(all_deals) < max_deals:
        if after:
            params["after"] = after

        try:
            resp = requests.get(DEALS_ENDPOINT, headers=headers, params=params, timeout=15)
        except requests.exceptions.ConnectionError:
            return [], "Could not connect to HubSpot. Check your internet connection."
        except requests.exceptions.Timeout:
            return [], "HubSpot API request timed out. Please try again."

        if resp.status_code == 401:
            return [], (
                "Authentication failed — your token was rejected by HubSpot. "
                "Check that the token is copied correctly and hasn't expired."
            )
        if resp.status_code == 403:
            return [], (
                "Permission denied — the token doesn't have CRM read access. "
                "Make sure 'CRM > Deals: Read' is enabled when creating the Private App."
            )
        if not resp.ok:
            return [], f"HubSpot API error {resp.status_code}: {resp.text[:200]}"

        data    = resp.json()
        results = data.get("results", [])
        all_deals.extend(results)

        paging = data.get("paging", {})
        after  = paging.get("next", {}).get("after")
        if not after:
            break

    return all_deals[:max_deals], None


# ── Stage name resolution ──────────────────────────────────────────────────────

def fetch_pipeline_stages(token: str) -> dict[str, str]:
    """
    Fetch all deal pipeline stages from HubSpot and return
    {stage_internal_id: stage_label}.

    HubSpot stores dealstage as an internal key like "appointmentscheduled"
    or "qualifiedtobuy". This resolves those to human-readable names.
    Falls back gracefully — if the call fails, the internal IDs are shown
    and the user can still map them manually in the stage normalizer.
    """
    url = f"{HUBSPOT_API_BASE}/crm/v3/pipelines/deals"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        stage_map = {}
        for pipeline in resp.json().get("results", []):
            for stage in pipeline.get("stages", []):
                stage_id    = stage.get("id", "")
                stage_label = stage.get("label", stage_id)
                stage_map[stage_id] = stage_label
        return stage_map
    except Exception:
        return {}


# ── Schema mapping ─────────────────────────────────────────────────────────────

def _parse_hs_date(value: Optional[str]) -> Optional[date]:
    """Parse HubSpot ISO date strings to Python date. Returns None on failure."""
    if not value:
        return None
    try:
        # HubSpot timestamps are milliseconds since epoch OR ISO strings
        if value.isdigit():
            return datetime.utcfromtimestamp(int(value) / 1000).date()
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except Exception:
        return None


def _parse_hs_amount(value: Optional[str]) -> float:
    """Parse HubSpot amount strings (may include currency symbols) to float."""
    if not value:
        return 0.0
    try:
        return float(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


def deals_to_dataframe(
    deals: list[dict],
    owners: dict[str, str],
    stage_labels: dict[str, str],
) -> tuple[pd.DataFrame, dict]:
    """
    Map raw HubSpot deal objects to a standard-schema DataFrame.

    Returns:
        df:            DataFrame with standard column names matching preprocessing.py
        schema_report: Summary of what was and wasn't mapped successfully
    """
    rows = []
    missing_owners  = 0
    missing_amounts = 0
    missing_stages  = 0

    for deal in deals:
        props = deal.get("properties", {})

        # Stage — resolve internal ID to human label first
        stage_raw = props.get("dealstage", "") or ""
        stage     = stage_labels.get(stage_raw, stage_raw) or "Unknown"
        if not stage_raw:
            missing_stages += 1

        # Amount
        amount = _parse_hs_amount(props.get("amount"))
        if amount == 0.0:
            missing_amounts += 1

        # Owner — resolve ID to name
        owner_id = str(props.get("hubspot_owner_id") or "")
        owner    = owners.get(owner_id, owner_id) if owner_id else "Unassigned"
        if not owner_id:
            missing_owners += 1

        row = {
            "deal_name":          props.get("dealname") or f"Deal {deal.get('id', '')}",
            "stage":              stage,
            "amount":             amount,
            "close_date":         _parse_hs_date(props.get("closedate")),
            "owner":              owner,
            "last_activity_date": _parse_hs_date(props.get("notes_last_updated")),
            "created_date":       _parse_hs_date(props.get("createdate")),
        }
        # deal_type is optional — only include if populated
        if props.get("deal_type"):
            row["deal_type"] = props["deal_type"]

        rows.append(row)

    df = pd.DataFrame(rows) if rows else pd.DataFrame()

    schema_report = {
        "total_deals":      len(deals),
        "mapped_deals":     len(rows),
        "missing_owners":   missing_owners,
        "missing_amounts":  missing_amounts,
        "missing_stages":   missing_stages,
        "has_activity_dates": any(r.get("last_activity_date") for r in rows),
        "has_created_dates":  any(r.get("created_date") for r in rows),
        "fields_mapped": [
            "deal_name", "stage", "amount", "close_date", "owner",
            "last_activity_date", "created_date",
        ],
    }

    return df, schema_report


# ── Main entry point ───────────────────────────────────────────────────────────

def fetch_hubspot_pipeline(token: str) -> tuple[pd.DataFrame, dict, Optional[str]]:
    """
    Full HubSpot fetch pipeline. Called by app.py when the user submits a token.

    Steps:
      1. Fetch all owners (for name resolution)
      2. Fetch all pipeline stage labels (for stage name resolution)
      3. Page through all deals (up to MAX_DEALS)
      4. Map to standard DataFrame schema

    Returns:
        df:            Standard-schema DataFrame, ready for preprocess_pipeline()
        schema_report: Mapping quality summary for UI display
        error:         Error string if fetch failed, None on success
    """
    token = token.strip()
    if not token:
        return pd.DataFrame(), {}, "No token provided."

    # Resolve owners first — non-fatal if it fails
    owners = fetch_owners(token)

    # Resolve stage labels — non-fatal if it fails
    stage_labels = fetch_pipeline_stages(token)

    # Fetch deals
    deals, error = fetch_all_deals(token)
    if error:
        return pd.DataFrame(), {}, error

    if not deals:
        return pd.DataFrame(), {}, (
            "No deals found in your HubSpot account. "
            "Make sure your pipeline has active deals and the token has CRM read access."
        )

    # Map to standard schema
    df, schema_report = deals_to_dataframe(deals, owners, stage_labels)

    if df.empty:
        return pd.DataFrame(), schema_report, "Deal data could not be parsed after fetching."

    return df, schema_report, None
