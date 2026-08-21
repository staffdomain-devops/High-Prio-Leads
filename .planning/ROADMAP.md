# Roadmap — High Priority Leads Pipeline

## Overview

**3 phases** | **13 requirements** | All v1 requirements covered

| # | Phase | Goal | Requirements |
|---|-------|------|--------------|
| 1 | Research Agent | Data fetching + Agent 1 (research + resource selection) | PIPE-01..05, PIPE-09..10, PROMPT-01 |
| 2 | Email Generation Agent | Agent 2 (email writing) + HubSpot write-back | PIPE-06..08, PROMPT-02..03 |
| 3 | Pipeline Wiring | GitHub Actions workflow + end-to-end validation | All PIPE requirements wired together |

---

### Phase 1: Research Agent Infrastructure

**Goal:** Fetch contact data from HubSpot (with job posting fields), enrich via ZoomInfo, and run the Research Agent (Claude call 1) that selects case studies/videos/resources and outputs a structured research payload JSON.

**Success Criteria:**
1. `fetch_hubspot.py` fetches all required fields including `job_title_posted`, `job_post_link`, `job_description`
2. `enrich_contact.py` fills missing fields via ZoomInfo; respects `skip_enrichment` flag
3. `research_contact.py` produces valid `research_payload.json` with all 4 resource slots populated
4. Resource selection follows Industry > Role > Pain > Fallback priority order
5. `research_payload.json` conforms to the defined JSON schema

**Deliverables:**
- `scripts/fetch_hubspot.py`
- `scripts/enrich_contact.py`
- `scripts/compute_campaign_tokens.py`
- `scripts/utils.py`
- `scripts/research_contact.py`
- `prompts/research_prompt.md`

---

### Phase 2: Email Generation Agent

**Goal:** Generate the 5-email cold sequence using the research payload from Phase 1, then write all emails and metadata back to HubSpot.

**Success Criteria:**
1. `generate_campaign.py` reads research payload and produces `campaign_output.json` with `email_1..5` keys
2. Each email has a `subject` and `body` conforming to all writing rules
3. Emails 1-3 do not use "offshoring" or "outsourcing"
4. `write_hubspot.py` writes all 10 properties (`subject_1..5`, `email_1..5`) + research summary + date to HubSpot
5. HubSpot note created on contact with campaign summary

**Deliverables:**
- `scripts/generate_campaign.py`
- `scripts/write_hubspot.py`
- `prompts/email_prompt.md`

---

### Phase 3: Pipeline Wiring

**Goal:** Wire all scripts into a GitHub Actions workflow; add error handling, DLQ, Teams notifications, and artifact upload.

**Success Criteria:**
1. `campaign.yml` runs all 5 script steps in order
2. All required secrets/env vars passed to each step
3. On success: `campaign_output.json` uploaded as artifact
4. On failure: `failed_contacts.json` uploaded and Teams webhook fires
5. Pipeline runs end-to-end without errors on a test contact

**Deliverables:**
- `.github/workflows/campaign.yml`
- `requirements.txt`
- `CLAUDE.md`
- `.gitignore`

---

## State

Current phase: **Phase 1** (not started)
