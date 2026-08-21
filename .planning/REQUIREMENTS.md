# Requirements — High Priority Leads Pipeline

## v1 Requirements

### Pipeline Infrastructure

- [ ] **PIPE-01**: Workflow triggers via `workflow_dispatch` with inputs: `contact_id` (required), `contact_email` (required), `skip_enrichment` (optional boolean, default false)
- [ ] **PIPE-02**: `fetch_hubspot.py` fetches contact properties including job posting fields: `job_title_posted`, `job_post_link`, `job_description`, plus standard fields (name, company, industry, website, etc.)
- [ ] **PIPE-03**: `enrich_contact.py` runs ZoomInfo enrichment; skippable via `INPUT_SKIP_ENRICHMENT=true`
- [ ] **PIPE-04**: `compute_campaign_tokens.py` writes `campaign_tokens.json` with `current_date`
- [ ] **PIPE-08**: `campaign_output.json` uploaded as GitHub Actions artifact (7-day retention)
- [ ] **PIPE-09**: On failure: `failed_contacts.json` copied and uploaded; Teams webhook notification sent with contact email, failed step, error excerpt, and run log link
- [ ] **PIPE-10**: All HubSpot, Anthropic, and requests API calls wrapped with `tenacity` retry (exponential backoff, max 6 attempts / 60s, retry on 429 + 5xx only)

### Research Agent

- [ ] **PIPE-04**: `research_contact.py` reads `hubspot_contact.json` and `campaign_tokens.json`, builds research prompt, calls Claude, writes `research_payload.json` to RUNNER_TEMP
- [ ] **PIPE-05**: `research_payload.json` schema:
  ```json
  {
    "company_summary": "string",
    "job_description_insights": "string — 2-3 specific requirements from job post",
    "buyer_frame": "string — Founder/CEO/Ops/HR with primary concern",
    "resources": {
      "case_study": {"id": "string", "url": "string", "one_line": "string"},
      "youtube_video": {"id": "string", "url": "string", "one_line": "string"},
      "website_resource_email2": {"id": "string", "url": "string", "one_line": "string"},
      "website_resource_email5": {"id": "string", "url": "string", "one_line": "string"}
    }
  }
  ```
- [ ] **PROMPT-01**: Research prompt applies resource selection rules in order: Industry match → Role match → Pain match → Universal fallback

### Email Generation Agent

- [ ] **PIPE-06**: `generate_campaign.py` reads `hubspot_contact.json` AND `research_payload.json`, builds email prompt, calls Claude, writes `campaign_output.json` to RUNNER_TEMP
- [ ] **PROMPT-02**: Email prompt generates exactly 5 emails conforming to all specs (subject + body for each)
- [ ] **PROMPT-03**: All email writing rules enforced: Australian English, no em dashes, no "offshoring"/"outsourcing" in emails 1-3, no salutation/closing, short paragraphs

### HubSpot Write-back

- [ ] **PIPE-07**: `write_hubspot.py` writes `subject_1..5`, `email_1..5`, `hpl_research_summary`, `hpl_generated_date` to HubSpot contact; creates a note on the contact record

## v2 / Deferred

- Chorus transcript integration (no prior calls for cold outreach)
- SDR call notes generation
- Webhook trigger (workflow_dispatch sufficient for now)
- Repository dispatch trigger

## Out of Scope

- Building a new Make.com scenario — existing trigger pattern from Inbound is reused
- Automated A/B testing of prompts
- Email delivery — HubSpot sequences handle sending

## Traceability

| REQ-ID | Phase | Status |
|--------|-------|--------|
| PIPE-01..10 | Phase 1: Research Agent | Pending |
| PROMPT-01..03 | Phase 1: Research Agent / Phase 2: Email Agent | Pending |
