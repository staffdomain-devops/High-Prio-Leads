"""
Research Agent — Agent 1 in the 2-agent pipeline.

Reads HubSpot contact data (including job posting fields), calls Claude to:
1. Summarise the company and its current situation
2. Extract 2-3 specific insights from the job description
3. Identify the buyer frame (Founder/CEO vs Ops/HR etc.)
4. Select the best case study, YouTube video, and 2 website resources
   using the Industry > Role > Pain > Fallback priority logic

Writes research_payload.json to RUNNER_TEMP.
"""

import json
import os
import re
import sys
from pathlib import Path

import anthropic
from tenacity import retry, retry_if_exception

from utils import write_dlq, _is_anthropic_transient, ANTHROPIC_RETRY_KWARGS


RESEARCH_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "research_prompt.md"
MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 2048

_anthropic_retry = retry(retry=retry_if_exception(_is_anthropic_transient), **ANTHROPIC_RETRY_KWARGS)


@_anthropic_retry
def _call_claude(client, system, messages):
    return client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=messages,
    )


def strip_code_fence(text):
    text = text.strip()
    match = re.match(r'^```(?:json)?\s*([\s\S]*?)```\s*$', text, re.DOTALL)
    if match:
        return match.group(1).strip()
    match = re.search(r'(\{[\s\S]*\})', text)
    if match:
        return match.group(1).strip()
    return text


def substitute_tokens(template, tokens):
    def replacer(match):
        key = match.group(1)
        return str(tokens.get(key, ""))
    return re.sub(r"\{\{([^}]+)\}\}", replacer, template)


def validate_research_payload(payload):
    required_top = ["company_summary", "job_description_insights", "buyer_frame", "resources"]
    required_resources = ["case_study", "youtube_video", "website_resource_email2", "website_resource_email5"]
    required_resource_fields = ["id", "url", "one_line"]

    missing = [k for k in required_top if k not in payload]
    if missing:
        return False, f"Missing top-level keys: {missing}"

    resources = payload.get("resources", {})
    for slot in required_resources:
        if slot not in resources:
            return False, f"Missing resource slot: {slot}"
        res = resources[slot]
        missing_fields = [f for f in required_resource_fields if f not in res or not res[f]]
        if missing_fields:
            return False, f"Resource '{slot}' missing fields: {missing_fields}"

    return True, "OK"


def main():
    runner_temp   = os.environ["RUNNER_TEMP"]
    contact_id    = os.environ["INPUT_CONTACT_ID"]
    contact_email = os.environ.get("INPUT_CONTACT_EMAIL", "unknown")

    write_dlq(contact_id, contact_email, "research_contact", "Script started", retry_count=0)

    with open(os.path.join(runner_temp, "hubspot_contact.json")) as f:
        hubspot_data = json.load(f)
    with open(os.path.join(runner_temp, "campaign_tokens.json")) as f:
        campaign_tokens = json.load(f)

    contact_props = hubspot_data.get("contact_properties") or {}
    company_props = hubspot_data.get("company_properties") or {}
    opp_props     = hubspot_data.get("opportunity_properties") or {}

    tokens = {
        # Contact identity
        "contact.first_name":    contact_props.get("firstname", ""),
        "contact.last_name":     contact_props.get("lastname", ""),
        "contact.jobtitle":      contact_props.get("jobtitle", ""),
        "contact.company":       contact_props.get("company", ""),
        "contact.industry":      contact_props.get("industry", ""),
        "contact.website":       contact_props.get("website", ""),
        # Job posting — sourced from the Opportunity custom object (p5402982_opportunities)
        # 100% fill on job_title, 99.99% on job_post_link, 99.8% on job_description
        "opportunity.job_title":       opp_props.get("job_title___proper") or opp_props.get("job_title", ""),
        "opportunity.job_post_link":   opp_props.get("job_post_link", ""),
        "opportunity.job_description": opp_props.get("job_description", ""),
        "opportunity.job_board":       opp_props.get("job_board", ""),
        "opportunity.vertical":        opp_props.get("vertical", ""),
        "opportunity.final_score":     opp_props.get("final_score", ""),
        # Company data
        "company.name":              company_props.get("name", "") or opp_props.get("company_name", ""),
        "company.industry":          company_props.get("industry", ""),
        "company.numberofemployees": company_props.get("numberofemployees", ""),
        "company.city":              company_props.get("city", ""),
        "company.country":           company_props.get("country", ""),
        "company.website":           company_props.get("website", ""),
        "company.description":       company_props.get("description", ""),
        # Date
        "campaign.current_date": campaign_tokens.get("current_date", ""),
    }

    template = RESEARCH_PROMPT_PATH.read_text(encoding="utf-8")
    prompt = substitute_tokens(template, tokens)

    print(f"Research prompt: {len(prompt):,} chars")
    print(
        f"  Contact: {tokens['contact.first_name']} {tokens['contact.last_name']}"
        f" @ {tokens['contact.company'] or tokens['company.name']}"
    )
    print(f"  Title:           {tokens['contact.jobtitle'] or '(not provided)'}")
    print(f"  Job posted:      {tokens['opportunity.job_title'] or '(not provided)'}")
    print(f"  Job board:       {tokens['opportunity.job_board'] or '(not provided)'}")
    print(f"  Vertical:        {tokens['opportunity.vertical'] or '(not provided)'}")
    print(f"  Final score:     {tokens['opportunity.final_score'] or '(not provided)'}")
    print(f"  Industry:        {tokens['company.industry'] or tokens['contact.industry'] or '(not provided)'}")
    print(f"Calling {MODEL} for research...")

    system = (
        "You are a research analyst for an offshore staffing company. "
        "Your job is to analyse a prospect's company and job posting, then select the most relevant "
        "social proof assets from a fixed resource library. "
        "Return only the raw JSON object — no markdown, no code blocks, no explanation."
    )

    try:
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], max_retries=0)
        message = _call_claude(
            client, system,
            [{"role": "user", "content": prompt}],
        )

        raw = message.content[0].text
        print(f"Research response: {len(raw):,} chars | stop_reason={message.stop_reason}")

        raw_path = os.path.join(runner_temp, "research_payload_raw.txt")
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(raw)

        if message.stop_reason == "max_tokens":
            raise RuntimeError("Research response truncated (max_tokens) — increase MAX_TOKENS or shorten prompt")

        cleaned = strip_code_fence(raw)

        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError as e:
            print(f"Raw response saved to {raw_path}", file=sys.stderr)
            raise RuntimeError(f"Research response is not valid JSON: {e}") from e

        valid, msg = validate_research_payload(payload)
        if not valid:
            print(f"WARNING: Research payload validation failed: {msg}", file=sys.stderr)
        else:
            print("Research payload validated OK")

        # Log what was selected
        resources = payload.get("resources", {})
        for slot, label in [
            ("case_study", "Case study"),
            ("youtube_video", "YouTube video"),
            ("website_resource_email2", "Website resource (email 2)"),
            ("website_resource_email5", "Website resource (email 5)"),
        ]:
            res = resources.get(slot, {})
            print(f"  {label}: {res.get('id', '?')} — {res.get('one_line', '')[:80]}")

        out_path = os.path.join(runner_temp, "research_payload.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

        print(f"research_payload.json written")

    except Exception as exc:
        retry_count = getattr(getattr(exc, "__cause__", None), "statistics", {}).get("attempt_number", 1)
        write_dlq(contact_id, contact_email, "research_contact", exc, retry_count)
        raise


if __name__ == "__main__":
    main()
