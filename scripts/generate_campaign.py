"""
Email Generation Agent — Agent 2 in the 2-agent pipeline.

Reads HubSpot contact data AND research_payload.json from Agent 1.
Builds the email prompt with pre-selected resources and pre-researched insights.
Calls Claude to generate the 5-email cold sequence.
Writes campaign_output.json to RUNNER_TEMP.
"""

import json
import os
import re
import sys
from pathlib import Path

import anthropic
from tenacity import retry, retry_if_exception

from utils import write_dlq, _is_anthropic_transient, ANTHROPIC_RETRY_KWARGS


EMAIL_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "email_prompt.md"
MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 8192
EMAIL_BODY_CAP = 3000

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


def clean_text(text):
    if not isinstance(text, str):
        return text
    text = re.sub(r"\s*[—–]\s*", ", ", text)
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r"  +", " ", text)
    return text.strip()


def clean_emails(output):
    for i in range(1, 6):
        key = f"email_{i}"
        if key in output and isinstance(output[key], dict):
            email = output[key]
            if "subject" in email:
                email["subject"] = clean_text(email["subject"])
            if "body" in email:
                email["body"] = clean_text(email["body"])
    return output


def main():
    runner_temp   = os.environ["RUNNER_TEMP"]
    contact_id    = os.environ["INPUT_CONTACT_ID"]
    contact_email = os.environ.get("INPUT_CONTACT_EMAIL", "unknown")

    write_dlq(contact_id, contact_email, "generate_campaign", "Script started", retry_count=0)

    with open(os.path.join(runner_temp, "hubspot_contact.json")) as f:
        hubspot_data = json.load(f)
    with open(os.path.join(runner_temp, "research_payload.json")) as f:
        research = json.load(f)
    with open(os.path.join(runner_temp, "campaign_tokens.json")) as f:
        campaign_tokens = json.load(f)

    contact_props = hubspot_data.get("contact_properties") or {}
    company_props = hubspot_data.get("company_properties") or {}
    opp_props     = hubspot_data.get("opportunity_properties") or {}

    # Flatten selected resources for easy substitution in prompt
    resources = research.get("resources", {})
    cs   = resources.get("case_study", {})
    yt   = resources.get("youtube_video", {})
    wr2  = resources.get("website_resource_email2", {})
    wr5  = resources.get("website_resource_email5", {})

    tokens = {
        # Prospect details
        "contact.first_name":    contact_props.get("firstname", ""),
        "contact.last_name":     contact_props.get("lastname", ""),
        "contact.jobtitle":      contact_props.get("jobtitle", ""),
        "contact.company":       contact_props.get("company", "") or company_props.get("name", "") or opp_props.get("company_name", ""),
        "contact.industry":      company_props.get("industry", "") or contact_props.get("industry", "") or opp_props.get("vertical", ""),
        "contact.website":       contact_props.get("website", "") or company_props.get("website", ""),
        # Job posting — from Opportunity custom object
        "opportunity.job_title":       opp_props.get("job_title___proper") or opp_props.get("job_title", ""),
        "opportunity.job_post_link":   opp_props.get("job_post_link", ""),
        "opportunity.job_description": opp_props.get("job_description", ""),
        "opportunity.job_board":       opp_props.get("job_board", ""),
        "opportunity.vertical":        opp_props.get("vertical", ""),
        # Research output — pre-digested by Agent 1
        "research.company_summary":       research.get("company_summary", ""),
        "research.job_description_insights": research.get("job_description_insights", ""),
        "research.buyer_frame":           research.get("buyer_frame", ""),
        # Pre-selected resources (Agent 1 already chose these)
        "resource.case_study_one_line": cs.get("one_line", ""),
        "resource.case_study_url":      cs.get("url", ""),
        "resource.youtube_one_line":    yt.get("one_line", ""),
        "resource.youtube_url":         yt.get("url", ""),
        "resource.wr2_one_line":        wr2.get("one_line", ""),
        "resource.wr2_url":             wr2.get("url", ""),
        "resource.wr5_one_line":        wr5.get("one_line", ""),
        "resource.wr5_url":             wr5.get("url", ""),
        # Date
        "campaign.current_date": campaign_tokens.get("current_date", ""),
    }

    template = EMAIL_PROMPT_PATH.read_text(encoding="utf-8")
    prompt = substitute_tokens(template, tokens)

    print(f"Email prompt: {len(prompt):,} chars")
    print(
        f"  Contact: {tokens['contact.first_name']} {tokens['contact.last_name']}"
        f" @ {tokens['contact.company']}"
    )
    print(f"  Job posted: {tokens['opportunity.job_title'] or '(not provided)'}")
    print(f"  Case study: {cs.get('id', '?')}")
    print(f"  YouTube:    {yt.get('id', '?')}")
    print(f"  WR email 2: {wr2.get('id', '?')}")
    print(f"  WR email 5: {wr5.get('id', '?')}")
    print(f"Calling {MODEL} for email generation...")

    system = (
        "You are writing personalised cold sales emails in strict Australian English. "
        "NEVER use em dashes (—), en dashes (–), or hyphens as sentence separators. "
        "Use commas, colons, or full stops instead. Compound adjectives (no-lock-in, all-in) are fine. "
        "Never use the words 'offshoring' or 'outsourcing' in emails 1, 2, or 3. "
        "Return only the raw JSON object — no markdown, no code blocks."
    )

    try:
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], max_retries=0)
        message = _call_claude(
            client, system,
            [{"role": "user", "content": prompt}],
        )

        raw = message.content[0].text
        print(f"Email response: {len(raw):,} chars | stop_reason={message.stop_reason}")

        raw_path = os.path.join(runner_temp, "campaign_output_raw.txt")
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(raw)

        if message.stop_reason == "max_tokens":
            raise RuntimeError("Email response truncated (max_tokens) — increase MAX_TOKENS")

        cleaned = strip_code_fence(raw)

        try:
            output = json.loads(cleaned)
        except json.JSONDecodeError as e:
            print(f"Raw response saved to {raw_path}", file=sys.stderr)
            raise RuntimeError(f"Email response is not valid JSON: {e}") from e

        required_keys = [f"email_{i}" for i in range(1, 6)]
        missing = [k for k in required_keys if k not in output]
        if missing:
            print(f"WARNING: response missing email keys: {missing}", file=sys.stderr)

        output = clean_emails(output)

        # Attach research payload for write_hubspot.py to reference
        output["_research"] = research

        out_path = os.path.join(runner_temp, "campaign_output.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)

        generated_emails = [k for k in required_keys if k in output]
        print(f"campaign_output.json written: {', '.join(generated_emails)}")

    except Exception as exc:
        retry_count = getattr(getattr(exc, "__cause__", None), "statistics", {}).get("attempt_number", 1)
        write_dlq(contact_id, contact_email, "generate_campaign", exc, retry_count)
        raise


if __name__ == "__main__":
    main()
