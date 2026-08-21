"""
Enrichment gate — runs after fetch_hubspot, before research_contact.

1. Checks which important contact/company fields are present.
2. If fields are missing, calls ZoomInfo contact enrich (single call).
   Company data is extracted from the contact result.
3. Updates HubSpot contact and/or company with any newly found data.
4. If important gaps remain, posts an SDR review note.
"""

import json
import os
import sys
from datetime import datetime, timezone

import requests as req_lib

from utils import write_dlq


ZOOMINFO_BASE = "https://api.zoominfo.com"

PERSONAL_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "icloud.com", "me.com", "live.com", "aol.com", "protonmail.com",
    "msn.com", "ymail.com",
}

IMPORTANT_CONTACT_FIELDS = [
    ("firstname",    "Contact first name"),
    ("jobtitle",     "Contact job title / seniority"),
]
IMPORTANT_COMPANY_FIELDS = [
    ("name",              "Company name"),
    ("numberofemployees", "Number of employees"),
]

_ZI_OUTPUT_FIELDS = [
    "id", "firstName", "lastName", "email", "jobTitle",
    "companyName", "companyWebsite", "companyEmployeeCount", "companyEmployeeRange",
    "companyPrimaryIndustry", "companyIndustries", "companyCity", "companyState",
    "companyCountry", "companyRevenue", "managementLevel",
]


def _get_zoominfo_jwt():
    username = os.environ.get("ZOOMINFO_USERNAME", "")
    password = os.environ.get("ZOOMINFO_PASSWORD", "")
    if not username or not password:
        return None
    try:
        resp = req_lib.post(
            f"{ZOOMINFO_BASE}/authenticate",
            json={"username": username, "password": password},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("jwt")
    except Exception as exc:
        print(f"  ZoomInfo auth failed: {exc}", file=sys.stderr)
        return None


def _zi_enrich_contact(jwt, *, email=None, first_name=None, last_name=None, company_name=None):
    if not jwt:
        return {}, {}

    attempts = []
    if email:
        primary = {"personEmailAddress": email}
        if first_name:
            primary["firstName"] = first_name
        if last_name:
            primary["lastName"] = last_name
        attempts.append(primary)

    if first_name and last_name:
        fallback = {"firstName": first_name, "lastName": last_name}
        if company_name:
            fallback["companyName"] = company_name
        if fallback not in attempts:
            attempts.append(fallback)

    for match_input in attempts:
        try:
            resp = req_lib.post(
                f"{ZOOMINFO_BASE}/enrich/contact",
                headers={"Authorization": f"Bearer {jwt}"},
                json={"matchPersonInput": [match_input], "outputFields": _ZI_OUTPUT_FIELDS},
                timeout=30,
            )
            resp.raise_for_status()
            payload = resp.json()

            result_list = (payload.get("data") or {}).get("result") or []
            if not result_list:
                continue

            first_result = result_list[0]
            data_list = first_result.get("data") or []
            if not data_list:
                continue

            contact_data = data_list[0]
            company_data = {
                "name":            contact_data.get("companyName", ""),
                "website":         contact_data.get("companyWebsite", ""),
                "employeeCount":   contact_data.get("companyEmployeeCount"),
                "employeeRange":   contact_data.get("companyEmployeeRange", ""),
                "primaryIndustry": contact_data.get("companyPrimaryIndustry") or contact_data.get("companyIndustries") or [],
                "city":            contact_data.get("companyCity", ""),
                "state":           contact_data.get("companyState", ""),
                "country":         contact_data.get("companyCountry", ""),
            }

            print(
                f"  ZI matched: {contact_data.get('firstName', '')} {contact_data.get('lastName', '')} "
                f"| {contact_data.get('jobTitle', 'no title')}"
            )
            return contact_data, company_data

        except Exception as exc:
            print(f"  ZI contact enrich failed: {exc}", file=sys.stderr)

    return {}, {}


def _extract_zi_company_props(zi_company):
    primary_industry = zi_company.get("primaryIndustry") or []
    if isinstance(primary_industry, list):
        primary_industry = primary_industry[0] if primary_industry else ""

    employees = ""
    if zi_company.get("employeeCount"):
        employees = str(zi_company["employeeCount"])
    elif zi_company.get("employeeRange"):
        employees = str(zi_company["employeeRange"])

    return {
        "name":              zi_company.get("name", ""),
        "industry":          primary_industry,
        "numberofemployees": employees,
        "website":           zi_company.get("website", ""),
        "city":              zi_company.get("city", ""),
        "country":           zi_company.get("country", ""),
    }


def _clean_domain(url):
    if not url:
        return ""
    d = url.lower().replace("https://", "").replace("http://", "").lstrip("www.")
    return d.split("/")[0].strip()


def _hs_search_company_by_domain(headers, domain):
    try:
        resp = req_lib.post(
            "https://api.hubapi.com/crm/v3/objects/companies/search",
            headers={**headers, "Content-Type": "application/json"},
            json={
                "filterGroups": [{
                    "filters": [{"propertyName": "domain", "operator": "EQ", "value": domain}],
                }],
                "properties": ["name", "domain", "industry", "numberofemployees"],
                "limit": 1,
            },
            timeout=30,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        return results[0] if results else None
    except Exception as exc:
        print(f"  HubSpot company search failed: {exc}", file=sys.stderr)
        return None


def _hs_patch_contact(headers, contact_id, properties):
    if not properties:
        return
    try:
        resp = req_lib.patch(
            f"https://api.hubapi.com/crm/v3/objects/contacts/{contact_id}",
            headers={**headers, "Content-Type": "application/json"},
            json={"properties": properties},
            timeout=30,
        )
        resp.raise_for_status()
        print(f"  HubSpot contact {contact_id} patched: {list(properties.keys())}")
    except Exception as exc:
        print(f"  HubSpot contact patch failed: {exc}", file=sys.stderr)


def _hs_patch_company(headers, company_id, properties):
    if not properties or not company_id:
        return
    try:
        resp = req_lib.patch(
            f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}",
            headers={**headers, "Content-Type": "application/json"},
            json={"properties": properties},
            timeout=30,
        )
        if not resp.ok:
            print(f"  HubSpot company patch failed {resp.status_code}: {resp.text[:400]}", file=sys.stderr)
        else:
            print(f"  HubSpot company {company_id} patched: {list(properties.keys())}")
    except Exception as exc:
        print(f"  HubSpot company patch failed: {exc}", file=sys.stderr)


def _check_gaps(contact_props, company_props):
    gaps = []
    for field, label in IMPORTANT_CONTACT_FIELDS:
        if not (contact_props.get(field) or "").strip():
            gaps.append(f"{label}  [contact.{field}]")
    for field, label in IMPORTANT_COMPANY_FIELDS:
        if not (company_props.get(field) or "").strip():
            gaps.append(f"{label}  [company.{field}]")
    return gaps


def main():
    contact_id    = os.environ["INPUT_CONTACT_ID"]
    contact_email = os.environ.get("INPUT_CONTACT_EMAIL", "")
    runner_temp   = os.environ["RUNNER_TEMP"]

    write_dlq(contact_id, contact_email, "enrich_contact", "Script started", retry_count=0)

    if os.environ.get("INPUT_SKIP_ENRICHMENT", "").lower() in ("true", "1", "yes"):
        print("Enrichment skipped (INPUT_SKIP_ENRICHMENT=true)")
        return

    hs_path = os.path.join(runner_temp, "hubspot_contact.json")
    with open(hs_path) as f:
        data = json.load(f)

    contact_props = data.get("contact_properties") or {}
    company_props = data.get("company_properties") or {}
    company_id    = data.get("company_id")

    headers = {"Authorization": f"Bearer {os.environ['HUBSPOT_API_KEY']}"}

    initial_gaps = _check_gaps(contact_props, company_props)
    print(f"Gap check — {len(initial_gaps)} missing field(s):")
    for g in initial_gaps:
        print(f"  - {g}")

    if not initial_gaps:
        print("All important fields present — skipping enrichment")
        data["enrichment_log"]  = []
        data["enrichment_gaps"] = []
        with open(hs_path, "w") as f:
            json.dump(data, f, indent=2, default=str)
        return

    jwt = _get_zoominfo_jwt()
    zi_contact, zi_company = {}, {}
    enrichment_log = []

    if jwt:
        fn = (contact_props.get("firstname") or "").strip()
        ln = (contact_props.get("lastname") or "").strip()
        co = (company_props.get("name") or contact_props.get("company") or "").strip()

        zi_contact, zi_company = _zi_enrich_contact(
            jwt,
            email=contact_email or None,
            first_name=fn or None,
            last_name=ln or None,
            company_name=co or None,
        )

        if zi_contact:
            enrichment_log.append(
                f"ZoomInfo matched: {zi_contact.get('firstName', '')} {zi_contact.get('lastName', '')} "
                f"| {zi_contact.get('jobTitle', 'no title')}"
            )
        else:
            enrichment_log.append("ZoomInfo: no match")
    else:
        enrichment_log.append("ZoomInfo unavailable — credentials not configured")

    if zi_contact:
        hs_ct_patch = {}
        for prop_name, zi_key in [
            ("firstname", "firstName"),
            ("lastname",  "lastName"),
            ("jobtitle",  "jobTitle"),
        ]:
            if not (contact_props.get(prop_name) or "").strip() and zi_contact.get(zi_key):
                contact_props[prop_name] = zi_contact[zi_key]
                hs_ct_patch[prop_name]   = zi_contact[zi_key]

        if hs_ct_patch:
            _hs_patch_contact(headers, contact_id, hs_ct_patch)

    if zi_company and zi_company.get("name"):
        zi_co_props = _extract_zi_company_props(zi_company)
        for field, value in zi_co_props.items():
            if value and not (company_props.get(field) or "").strip():
                company_props[field] = value

        if company_id:
            hs_co_patch = {}
            for field in ["name", "numberofemployees", "city", "country"]:
                if zi_co_props.get(field) and not (data.get("company_properties") or {}).get(field, "").strip():
                    hs_co_patch[field] = zi_co_props[field]
            if hs_co_patch:
                _hs_patch_company(headers, company_id, hs_co_patch)

    data["contact_properties"] = contact_props
    data["company_properties"] = company_props
    data["enrichment_log"]     = enrichment_log
    data["enrichment_gaps"]    = _check_gaps(contact_props, company_props)

    with open(hs_path, "w") as f:
        json.dump(data, f, indent=2, default=str)

    print("hubspot_contact.json updated — enrichment complete")


if __name__ == "__main__":
    main()
