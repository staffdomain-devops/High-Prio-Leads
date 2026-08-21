# High Priority Leads — 2-Agent Outbound Email Pipeline

## What This Is

A GitHub Actions pipeline that generates personalised 5-email cold sequences for prospects who have posted job ads that Staff Domain can help fill offshore. Triggered by Make.com when a contact is added to the HubSpot "High Priority Leads" list.

**Key difference from the Inbound pipeline:** This is cold outreach. The prospect doesn't know Staff Domain yet. The pipeline must research the contact and their job post, select appropriate social proof, then generate a highly personalised 5-email sequence.

## Two-Agent Architecture

The original `email-prompt.md` monolith is split into two Claude calls:

1. **Research Agent** — analyzes the company, job description, and buyer persona; selects the best case study, YouTube video, and 2 website resources from the resource library; outputs a structured JSON payload
2. **Email Generation Agent** — receives the research payload and contact data; writes the 5-email cold sequence using pre-selected resources and insights

This separation keeps each agent's prompt focused, makes resource selection auditable (it's in the research payload artifact), and lets us tune/swap each agent independently.

## Context

- Sister project to `C:\Users\irahfo\Outreach\Inbound` — shares architecture, utils, and enrichment approach
- Inbound scripts (fetch_hubspot, enrich_contact, utils) are adapted verbatim or near-verbatim
- Trigger: Make.com watches a HubSpot list; fires `workflow_dispatch` with `contact_id` and `contact_email`
- Output: 5 email subjects + bodies written to HubSpot contact properties; campaign JSON artifact uploaded

## Stack

- Python 3.12
- GitHub Actions (ubuntu-latest)
- Claude API (claude-sonnet-4-6)
- HubSpot API (contacts read/write, engagements read/write)
- ZoomInfo API (optional enrichment)
- Teams webhook (failure notifications)

## HubSpot Properties Required

Create these custom contact properties before first run:
- `subject_1` through `subject_5` — Single-line text
- `email_1` through `email_5` — Multi-line text
- `hpl_research_summary` — Multi-line text (stores reasoning from research agent)
- `hpl_generated_date` — Date

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Two Claude calls instead of one | Separates research (deterministic rules) from writing (creative) — easier to tune and audit | Chosen |
| Research payload as JSON file in RUNNER_TEMP | Same pattern as hubspot_contact.json — flows between steps cleanly | Chosen |
| Reuse Inbound's fetch/enrich/utils scripts | Already tested in production; no point rewriting | Chosen |
| 5-email output (vs Inbound's 1-email) | High Prio Leads uses a 5-touch cold sequence per spec | Chosen |

## Requirements

### Active

- [ ] PIPE-01: Trigger via GitHub Actions `workflow_dispatch` with `contact_id` and `contact_email`
- [ ] PIPE-02: Fetch contact + company data from HubSpot including job posting fields
- [ ] PIPE-03: ZoomInfo enrichment (skippable via `skip_enrichment` input)
- [ ] PIPE-04: Research Agent: Claude call outputting structured research payload JSON
- [ ] PIPE-05: Research payload contains company_summary, job_insights, buyer_frame, 4 selected resources with URLs and one-liners
- [ ] PIPE-06: Email Generation Agent: Claude call producing 5 emails from research payload
- [ ] PIPE-07: Write `subject_1..5` and `email_1..5` + research summary back to HubSpot
- [ ] PIPE-08: Upload `campaign_output.json` as artifact (7-day retention)
- [ ] PIPE-09: On failure: DLQ artifact + Teams webhook notification
- [ ] PIPE-10: All external API calls use exponential backoff (tenacity)
- [ ] PROMPT-01: Research prompt selects resources using Industry > Role > Pain priority logic
- [ ] PROMPT-02: Email prompt generates 5 emails conforming to all writing rules in original spec
- [ ] PROMPT-03: Emails: Australian English, no em dashes, no "offshoring"/"outsourcing" in emails 1-3

### Out of Scope

- Chorus/call transcript integration — high prio leads have no prior calls
- SDR call notes generation — not in the 5-email spec
- Webhook-based trigger — using workflow_dispatch (same as Inbound)

## Evolution

This document evolves at phase transitions and milestone boundaries.

---
*Last updated: 2026-08-21 after initialization*
