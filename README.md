# Pipeline & Forecast Synthesizer

> *Third tool in the [Operational Coherence Stack](https://github.com/your-username) — turning organizational data into executive clarity.*

**WBR Generator** answers *what happened?*
**Meeting Intelligence** answers *what was decided?*
**Pipeline & Forecast Synthesizer** answers *what will happen?*

---

## What it does

Upload a CRM export or connect HubSpot directly. The tool produces a six-section narrative forecast ready to hand to leadership:

- **Executive Summary** — what's most likely to happen this quarter, in plain language
- **Forecast Confidence** — why the forecast should or shouldn't be trusted, with reasoning made explicit
- **Revenue Risks** — concentration risk, slippage patterns, stale deals, dependency flags
- **Revenue Opportunities** — specific upside scenarios and positive momentum signals
- **Rep Insights** — who's outperforming and who needs support, inferred from pipeline structure
- **Leadership Actions** — three concrete things leadership should do this week

The output is not a formatted version of your CRM data. It's the judgment layer — the narrative a CRO produces after an hour with the spreadsheet, generated in seconds.

**Example output:**
> "Pipeline coverage is 3.1x on paper, but the forecast carries meaningful concentration risk. Three opportunities represent 44% of remaining ARR — two are enterprise deals with no recorded activity in the past 18 days, and one has a close date that has slipped twice. At current close rates, the most likely outcome is 78–85% of target, with the primary risk being late-stage slippage rather than top-of-funnel volume."

---

## Quick start

### Option A — HubSpot live connection (recommended)

No export needed. The tool fetches your deals directly from HubSpot, maps them automatically, and skips the column mapping step.

**What you need:** A HubSpot Private App token. [See instructions below.](#getting-your-hubspot-token)

1. Open the app → click the **Connect HubSpot (Live)** tab
2. Paste your Private App token
3. Click **Fetch Pipeline from HubSpot**
4. Normalize stage names, set your quarter end date, and run

### Option B — CSV upload

1. Export your pipeline as a CSV from Salesforce, HubSpot, Attio, or any CRM
2. Open the app → click the **Upload CSV** tab
3. Upload the file and map your columns to the standard schema
4. Normalize stage names, set your quarter end date, and run

---

## Getting your HubSpot token

HubSpot uses **Private App tokens** for API access (legacy API keys were deprecated in 2022). Creating one takes about 2 minutes.

### Step-by-step

**1. Go to Private Apps**
In your HubSpot account:
→ Click the **Settings** gear (top-right corner)
→ **Integrations** → **Private Apps**
→ Click **Create a private app**

**2. Name the app**
Give it any name — e.g., *"Pipeline Synthesizer"*. Description is optional.

**3. Set scopes**
Click the **Scopes** tab. Under **CRM**, enable:
- ✅ `crm.objects.deals.read`
- ✅ `crm.objects.owners.read` *(required for rep name resolution — without this, owner IDs appear instead of names)*

You don't need write access.

**4. Create and copy the token**
Click **Create app** → confirm → copy the token that appears.
It looks like: `pat-na1-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`

> ⚠️ **HubSpot only shows the full token once.** Copy it before closing the dialog. If you lose it, you can generate a new one from the same Private App settings page.

**5. Paste it into the app**
The token is never stored — it lives only in your browser session and is used only for the current analysis run.

### Using an AI assistant to navigate HubSpot's UI

If HubSpot's settings UI has changed since this guide was written, or if you want guided help navigating it, copy this prompt into ChatGPT, Claude, or any AI tool:

```
I use HubSpot CRM. Walk me through creating a Private App token with
crm.objects.deals.read and crm.objects.owners.read scopes so I can
connect my pipeline data to an external tool. My HubSpot account is
[your account name or URL if relevant]. Give me step-by-step instructions
for the current HubSpot UI.
```

This is especially useful if you're not familiar with HubSpot's developer settings or if the UI layout has been updated.

### Token security

- The token is not saved to any database or file — it exists only in your active browser session
- Treat it like a password: don't paste it into a shared screen or commit it to any file in your repo
- You can revoke access at any time by deleting the Private App in HubSpot Settings → Integrations → Private Apps
- For shared or team deployments, consider storing the token as a Streamlit secret rather than having each user paste it manually

---

## Architecture

### Layer 1 — Deterministic preprocessing (`preprocessing.py`)

Runs before Claude sees anything. No API calls. Pure Python.

- CSV parsing and validation
- Stage normalization — maps CRM-specific stage names to standard funnel stages
- Derived metric calculation: coverage ratio, weighted pipeline (amount × stage probability), deal age, days since last activity, slippage detection
- Concentration analysis: top N deals as % of total pipeline, single-rep dependency
- Quality scoring: minimum row count, required field completeness, date validity

**Standard stage probabilities (user-overridable):**
| Stage | Default probability |
|---|---|
| Prospecting | 10% |
| Qualification | 25% |
| Discovery | 40% |
| Proposal | 60% |
| Negotiation | 80% |
| Closed Won | 100% |
| Closed Lost | 0% |

### Layer 2 — HubSpot fetch layer (`hubspot.py`)

Handles live data integration. Called only on the HubSpot path.

- Authenticates via Private App token
- Resolves HubSpot owner IDs to rep names via the owners endpoint
- Resolves HubSpot internal stage IDs to human-readable stage labels via the pipelines endpoint
- Pages through all deals (up to 500 by default)
- Maps HubSpot native properties to the standard schema
- Returns the same DataFrame shape as the CSV path — both paths feed the same preprocessing layer

**HubSpot → Standard schema mapping:**
| HubSpot property | Standard field |
|---|---|
| dealname | deal_name |
| dealstage (resolved to label) | stage |
| amount | amount |
| closedate | close_date |
| hubspot_owner_id (resolved to name) | owner |
| notes_last_updated | last_activity_date |
| createdate | created_date |

### Layer 3 — Reasoning layer (`prompt.py`)

Single-pass Claude API calls using structured tool use. Six forced calls map directly to the six output sections. Claude receives the preprocessed metrics — not the raw CRM data — and reasons like a CRO preparing for a board meeting.

**Design principle:** Signal vs. judgment. `3.2x coverage` is a signal. *"The forecast risk is concentration, not volume"* is judgment. The tool produces the latter.

### Layer 4 — UI (`app.py`)

Streamlit. Two input tabs (HubSpot live, CSV upload) feed the same analysis pipeline. Results stored in session state so export doesn't clear output.

---

## Input requirements

**Required fields (all paths):**
- Deal stage
- Deal amount
- Close date
- Owner / rep name

**Optional but recommended:**
- Deal name (improves narrative specificity)
- Last activity date (enables staleness analysis — significantly improves rep insights)
- Created date (enables deal age calculation)
- Segment / vertical (enables segment concentration analysis)

**Minimums:** 5 deals required. Below this, the run is blocked.
**Soft cap:** 500 deals. Warning shown above this threshold; run not blocked.
**Supported formats:** `.csv` only for direct upload. Excel export → Save As CSV covers all major CRMs.

---

## Stack

| Layer | Technology |
|---|---|
| Frontend | Streamlit |
| Backend | Python 3.10+ |
| Reasoning API | Anthropic Claude Sonnet |
| Data parsing | pandas |
| HubSpot integration | HubSpot CRM API v3 (requests) |
| Hosting | Streamlit Community Cloud (free tier) |

---

## Running locally

```bash
# Clone the repo
git clone https://github.com/your-username/pipeline-forecast-synthesizer
cd pipeline-forecast-synthesizer

# Install dependencies
pip install -r requirements.txt

# Set your Anthropic API key
export ANTHROPIC_API_KEY=sk-ant-...

# Run
streamlit run app.py
```

For Streamlit Community Cloud deployment, add `ANTHROPIC_API_KEY` to your app's Secrets in the Streamlit dashboard. The HubSpot token is entered by the user at runtime — it doesn't need to be in secrets for single-user deployments.

---

## Security and privacy

- CRM deal data is sent to Anthropic's API for analysis. Review [Anthropic's privacy policy](https://www.anthropic.com/privacy) for data handling details.
- No data is written to a database in v1 — single-run, stateless.
- HubSpot tokens are never stored or logged by this application.
- No API keys are stored in the repository.
- For sensitive pipeline data, consider removing deal names before uploading — the tool operates on pipeline structure and amounts; specific deal names improve narrative but aren't required.

---

## Known limitations (v1)

- **No persistence** — no week-over-week pipeline trend tracking across sessions
- **HubSpot only** for live integration in v1 — Salesforce, Attio, and others are CSV path only
- **No OAuth auto-redirect** — users paste a Private App token manually. OAuth auto-redirect (the "Connect with HubSpot" button flow) is a production-tier consideration that requires a registered HubSpot app, a public callback URL, and token refresh logic. The Private App token path is correct for single-user and portfolio use.
- **Stage probabilities are assumptions** — defaults are reasonable but not calibrated to your specific historical close rates. Use the probability overrides for better accuracy.
- **Rep insights are inferred from pipeline structure** — last activity date improves quality significantly; champion status, MEDDICC fields, and next steps are not used unless present in your data
- **Single-threaded hosting** — Streamlit Community Cloud free tier is not designed for high concurrency

---

## What this tool demonstrates

Commercial systems thinking — the ability to look at a pipeline and explain why the quarter is actually at risk. Not just the mechanics of building an AI tool, but the judgment a CRO exercises every week: what does this data mean, what's the risk that isn't visible in the aggregate, and what should leadership do about it?

Combined with the WBR Generator and Meeting Intelligence, it completes the Operational Coherence Stack:
- **WBR Generator** → measurement
- **Meeting Intelligence** → execution
- **Pipeline Synthesizer** → forecasting

The HubSpot live integration demonstrates understanding of how AI agents connect to live organizational systems — not just processing uploaded files, but pulling data from where it actually lives.

---

## Cost

- **Streamlit Community Cloud** — free tier
- **Anthropic API** — approximately $0.03–0.05 per analysis run at Claude Sonnet pricing
- **HubSpot API** — free for Private App access within standard rate limits

---

*Part of the [Operational Coherence Stack](https://github.com/your-username) — WBR Generator · Meeting Intelligence · Pipeline Synthesizer · Prioritization Engine · Eval Harness*
