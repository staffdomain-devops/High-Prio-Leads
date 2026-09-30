import json
import os
import sys
import traceback
from datetime import datetime, timezone

import hubspot
from hubspot.crm.contacts import ApiException
from bs4 import BeautifulSoup
import requests as req_lib
from tenacity import retry, retry_if_exception

from utils import (
    write_dlq,
    _is_hubspot_transient,
    _is_requests_transient,
    HS_RETRY_KWARGS,
    REQ_RETRY_KWARGS,
)


_hs_retry = retry(retry=retry_if_exception(_is_hubspot_transient), **HS_RETRY_KWARGS)
_req_retry = retry(retry=retry_if_exception(_is_requests_transient), **REQ_RETRY_KWARGS)


@_hs_retry
def _get_contact(client, contact_id, properties):
    return client.crm.contacts.basic_api.get_by_id(contact_id, properties=properties)


@_hs_retry
def _get_company(client, company_id, properties):
    return client.crm.companies.basic_api.get_by_id(company_id, properties=properties)


@_hs_retry
def _get_owner(client, owner_id):
    return client.crm.owners.owners_api.get_by_id(owner_id=int(owner_id), id_property="id")


@_req_retry
def _get_engagements_page(headers, object_type, object_id, params):
    url = f"https://api.hubapi.com/engagements/v1/engagements/associated/{object_type}/{object_id}/paged"
    resp = req_lib.get(url, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    return resp


def strip_html(html_text):
    if not html_text:
        return ""
    try:
        return BeautifulSoup(html_text, "html.parser").get_text(separator=" ", strip=True)
    except Exception:
        import re
        return re.sub(r"<[^>]+>", " ", html_text).strip()


def fetch_contact_engagements(contact_id, headers):
    """Fetch emails, meetings, calls, and notes from the contact record."""
    email_history = []
    meeting_engagements = []
    call_history = []
    contact_notes = []
    offset = 0
    has_more = True

    while has_more:
        resp = _get_engagements_page(headers, "CONTACT", contact_id, {"limit": 100, "offset": offset})
        data = resp.json()

        for item in data.get("results", []):
            eng = item.get("engagement", {})
            meta = item.get("metadata", {})
            eng_type = eng.get("type")
            created_at = datetime.fromtimestamp(eng.get("createdAt", 0) / 1000, tz=timezone.utc)

            if eng_type in ("EMAIL", "INCOMING_EMAIL"):
                body_html = meta.get("html") or meta.get("body") or meta.get("text") or ""
                email_history.append({
                    "subject": meta.get("subject", ""),
                    "body_text": strip_html(body_html)[:3000],
                    "direction": meta.get("direction", ""),
                    "timestamp": created_at.isoformat(),
                })

            elif eng_type == "MEETING":
                notes_raw = meta.get("body") or meta.get("description") or ""
                start_time = meta.get("startTime")
                meeting_date = (
                    datetime.fromtimestamp(start_time / 1000, tz=timezone.utc).isoformat()
                    if start_time else created_at.isoformat()
                )
                duration_ms = meta.get("durationMilliseconds") or 0
                meeting_engagements.append({
                    "meeting_date": meeting_date,
                    "notes": strip_html(notes_raw),
                    "internal_notes": strip_html(meta.get("internalMeetingNotes") or ""),
                    "duration_minutes": round(duration_ms / 60000) if duration_ms else None,
                })

            elif eng_type in ("CALL", "INCOMING_CALL"):
                notes_raw = meta.get("body") or meta.get("text") or ""
                duration_ms = meta.get("durationMilliseconds") or 0
                call_history.append({
                    "timestamp": created_at.isoformat(),
                    "notes": strip_html(notes_raw)[:3000],
                    "disposition": meta.get("disposition", ""),
                    "duration_minutes": round(duration_ms / 60000) if duration_ms else None,
                })

            elif eng_type == "NOTE":
                body_raw = meta.get("body") or ""
                if body_raw.strip():
                    contact_notes.append({
                        "timestamp": created_at.isoformat(),
                        "body": strip_html(body_raw)[:3000],
                    })

        has_more = data.get("hasMore", False)
        offset = data.get("offset", offset + 100)

    return email_history, meeting_engagements, call_history, contact_notes


def fetch_company_notes(company_id, headers):
    notes = []
    offset = 0
    has_more = True

    while has_more:
        try:
            resp = _get_engagements_page(headers, "COMPANY", company_id, {"limit": 100, "offset": offset})
        except Exception as e:
            print(f"  Warning: could not fetch company engagements: {e}", file=sys.stderr)
            break
        data = resp.json()

        for item in data.get("results", []):
            eng = item.get("engagement", {})
            meta = item.get("metadata", {})
            if eng.get("type") == "NOTE":
                body_raw = meta.get("body") or ""
                if body_raw.strip():
                    created_at = datetime.fromtimestamp(eng.get("createdAt", 0) / 1000, tz=timezone.utc)
                    notes.append({
                        "timestamp": created_at.isoformat(),
                        "body": strip_html(body_raw)[:3000],
                    })

        has_more = data.get("hasMore", False)
        offset = data.get("offset", offset + 100)

    return notes


def main():
    contact_id = os.environ["INPUT_CONTACT_ID"]
    contact_email = os.environ["INPUT_CONTACT_EMAIL"]
    runner_temp = os.environ["RUNNER_TEMP"]

    if not contact_id or not contact_id.strip().isdigit():
        print(f"ERROR: contact_id is invalid: {contact_id!r}", file=sys.stderr)
        sys.exit(1)

    write_dlq(contact_id, contact_email, "fetch_hubspot", "Script started", retry_count=0)

    client = hubspot.Client.create(access_token=os.environ["HUBSPOT_API_KEY"])
    headers = {"Authorization": f"Bearer {os.environ['HUBSPOT_API_KEY']}"}

    try:
        contact = _get_contact(
            client,
            contact_id,
            properties=[
                "firstname", "lastname", "email", "jobtitle", "company",
                "industry", "num_employees", "city", "state", "country",
                "website", "hubspot_owner_id",
                # link_to_target_role is the contact-level job link (73.7% fill).
                # Job title/description/link come from the Opportunity object — fetched below.
                "link_to_target_role",
            ],
        )
        contact_properties = contact.properties or {}

        def resolve_owner_firstname(owner_id):
            if not owner_id:
                return ""
            try:
                owner = _get_owner(client, owner_id)
                return owner.first_name or ""
            except Exception as e:
                print(f"  Warning: could not resolve owner {owner_id}: {e}", file=sys.stderr)
                return ""

        contact_properties["current_owner_firstname"] = resolve_owner_firstname(
            contact_properties.get("hubspot_owner_id")
        )

        # Fetch associated company
        company_properties = {}
        company_id = None
        try:
            assoc_resp = req_lib.get(
                f"https://api.hubapi.com/crm/v3/objects/contacts/{contact_id}/associations/companies",
                headers=headers,
                timeout=30,
            )
            assoc_resp.raise_for_status()
            company_ids = [r["id"] for r in assoc_resp.json().get("results", [])]
            if company_ids:
                company_id = company_ids[0]
                company = _get_company(
                    client,
                    company_id,
                    properties=[
                        "name", "industry", "numberofemployees", "city", "country",
                        "website", "annualrevenue", "description",
                    ],
                )
                company_properties = company.properties or {}
                print(f"Company: {company_properties.get('name', '(unnamed)')} (ID {company_id})")
            else:
                print("No associated company found", file=sys.stderr)
        except Exception as e:
            print(f"  Warning: could not fetch company: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

        # Fetch associated opportunity (job ad) via association API
        # Object type: p5402982_opportunities — "captures job ads posted by each account"
        opportunity_properties = {}
        opportunity_id = None
        try:
            # Association results are NOT ordered by recency — collect all (paginated),
            # then pick the newest by object create date.
            opp_ids = []
            after = None
            while True:
                params = {"limit": 500}
                if after:
                    params["after"] = after
                opp_assoc_resp = req_lib.get(
                    f"https://api.hubapi.com/crm/v4/objects/contacts/{contact_id}/associations/p5402982_opportunities",
                    headers=headers,
                    params=params,
                    timeout=30,
                )
                opp_assoc_resp.raise_for_status()
                assoc_json = opp_assoc_resp.json()
                opp_ids.extend(str(r["toObjectId"]) for r in assoc_json.get("results", []))
                after = (assoc_json.get("paging") or {}).get("next", {}).get("after")
                if not after:
                    break
            if opp_ids:
                opp_resp = req_lib.post(
                    "https://api.hubapi.com/crm/v3/objects/p5402982_opportunities/batch/read",
                    headers=headers,
                    json={
                        "inputs": [{"id": i} for i in opp_ids[:100]],
                        "properties": [
                            "job_title", "job_title___proper", "job_description", "job_post_link",
                            "job_board", "vertical", "company_name", "date_job_posted",
                            "final_score", "status", "hs_createdate",
                        ],
                    },
                    timeout=30,
                )
                opp_resp.raise_for_status()
                opps = opp_resp.json().get("results", [])

                # Newest = latest object create date (what the HubSpot UI sorts by).
                # date_job_posted is unreliable on imported records, so it is not used.
                def _recency(o):
                    created = (o.get("properties") or {}).get("hs_createdate") or o.get("createdAt") or ""
                    try:
                        ts = datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
                    except ValueError:
                        ts = 0
                    return (ts, int(o["id"]))

                if not opps:
                    raise RuntimeError(f"batch read returned no opportunities for IDs {opp_ids}")
                newest = max(opps, key=_recency)
                opportunity_id = newest["id"]
                opportunity_properties = newest.get("properties") or {}
                print(
                    f"Opportunity: {opportunity_properties.get('job_title', '(no title)')} "
                    f"| {opportunity_properties.get('job_board', '')} "
                    f"| score={opportunity_properties.get('final_score', 'N/A')} "
                    f"(ID {opportunity_id})"
                )
                if len(opp_ids) > 1:
                    print(
                        f"  Note: contact has {len(opp_ids)} opportunities — using most recent "
                        f"(created {opportunity_properties.get('hs_createdate') or 'n/a'})"
                    )
            else:
                print("No associated opportunity found — job data will be empty", file=sys.stderr)
        except Exception as e:
            print(f"  Warning: could not fetch opportunity: {e}", file=sys.stderr)

        # Fetch engagements
        try:
            email_history, meeting_engagements, call_history, contact_notes = fetch_contact_engagements(
                contact_id, headers
            )
        except Exception as e:
            print(f"Warning: could not fetch contact engagements: {e}", file=sys.stderr)
            email_history, meeting_engagements, call_history, contact_notes = [], [], [], []

        company_notes = []
        if company_id:
            try:
                company_notes = fetch_company_notes(company_id, headers)
            except Exception as e:
                print(f"Warning: could not fetch company notes: {e}", file=sys.stderr)

        print(
            f"Contact engagements: {len(email_history)} emails, {len(meeting_engagements)} meetings, "
            f"{len(call_history)} calls, {len(contact_notes)} notes"
        )
        print(f"Company notes: {len(company_notes)}")

        output = {
            "contact_properties": contact_properties,
            "company_id": company_id,
            "company_properties": company_properties,
            "opportunity_id": opportunity_id,
            "opportunity_properties": opportunity_properties,
            "email_history": email_history,
            "meeting_engagements": meeting_engagements,
            "call_history": call_history,
            "contact_notes": contact_notes,
            "company_notes": company_notes,
        }

        out_path = os.path.join(runner_temp, "hubspot_contact.json")
        with open(out_path, "w") as f:
            json.dump(output, f, indent=2, default=str)

        print("hubspot_contact.json written")

    except Exception as exc:
        retry_count = getattr(getattr(exc, "__cause__", None), "statistics", {}).get("attempt_number", 1)
        write_dlq(contact_id, contact_email, "fetch_hubspot", exc, retry_count)
        raise


if __name__ == "__main__":
    main()
