# High Priority Leads — 2-Agent Outbound Email Pipeline

## What This Does

GitHub Actions pipeline triggered by Make.com when a contact is added to the HubSpot "High Priority Leads" list. Generates a personalised 5-email cold sequence for prospects who have posted a job ad that Staff Domain can help fill with offshore talent.

## Two-Agent Architecture

**Agent 1 — Research** (`scripts/research_contact.py` + `prompts/research_prompt.md`):
- Analyzes company + job posting
- Selects best case study, YouTube video, and 2 website resources
- Outputs `research_payload.json`

**Agent 2 — Emails** (`scripts/generate_campaign.py` + `prompts/email_prompt.md`):
- Receives research payload (pre-selected resources + insights)
- Generates the 5-email cold sequence
- Outputs `campaign_output.json`

## Script Execution Order

```
fetch_hubspot.py
  → enrich_contact.py
    → compute_campaign_tokens.py
      → research_contact.py  (Agent 1 — Claude call 1)
        → generate_campaign.py  (Agent 2 — Claude call 2)
          → write_hubspot.py
```

## Temp Files (RUNNER_TEMP)

| File | Written by | Read by |
|------|-----------|---------|
| `hubspot_contact.json` | fetch_hubspot | enrich_contact, research_contact, generate_campaign |
| `campaign_tokens.json` | compute_campaign_tokens | research_contact, generate_campaign |
| `research_payload.json` | research_contact | generate_campaign, write_hubspot |
| `campaign_output.json` | generate_campaign | write_hubspot |
| `failed_contacts.json` | any script on failure | workflow (artifact upload + Teams) |

## HubSpot Properties Required

Create these before first run (Settings > Properties > Contact):

| Property | Type | Description |
|----------|------|-------------|
| `job_title_posted` | Single-line text | The role the prospect posted |
| `job_post_link` | Single-line text | URL of the job posting |
| `job_description` | Multi-line text | Full job description text |
| `subject_1` .. `subject_5` | Single-line text | Email subject lines |
| `email_1` .. `email_5` | Multi-line text | Email bodies |
| `hpl_research_summary` | Multi-line text | Research agent summary (for SDR reference) |
| `hpl_generated_date` | Date | Date of generation |

## GitHub Secrets Required

| Secret | Description |
|--------|-------------|
| `HUBSPOT_API_KEY` | HubSpot Private App token (contacts + engagements read/write, owners read) |
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `ZOOMINFO_USERNAME` | ZoomInfo username (optional) |
| `ZOOMINFO_PASSWORD` | ZoomInfo password (optional) |
| `TEAMS_WEBHOOK_URL` | Microsoft Teams incoming webhook URL |

## Reference

Sister project: `C:\Users\irahfo\Outreach\Inbound` — same architecture, different prompt and trigger.
Original prompt spec: `email-prompt.md` (now split into `prompts/research_prompt.md` + `prompts/email_prompt.md`)
