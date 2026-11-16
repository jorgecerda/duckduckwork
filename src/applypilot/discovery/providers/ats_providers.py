"""Direct ATS REST API Connectors for Greenhouse, Ashby, and Lever.

Provides zero-scraping, zero-token, high-fidelity job ingestion from public ATS REST APIs.
Automatically parses postings, strips HTML noise, evaluates posting liveness,
tracks scan history, and stores standardized jobs in SQLite.
"""

from __future__ import annotations

import html
import json
import logging
import re
import sqlite3
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from typing import Any

from applypilot.database import get_connection, record_scan_history
from applypilot.discovery.liveness import check_posting_liveness
from applypilot.enrichment.cleaner import clean_job_description

log = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": "DuckDuckWork-JobEngine/1.0 (Direct ATS Integration; https://github.com/duckduckwork)",
    "Accept": "application/json",
}


def detect_ats_provider(url_or_slug: str) -> tuple[str, str] | None:
    """Identify ATS provider and extract the board token/slug from a URL or slug.

    Returns:
        (provider, board_token) where provider is 'greenhouse', 'ashby', or 'lever'.
        Returns None if not recognized.
    """
    if not url_or_slug:
        return None
    val = url_or_slug.strip()

    # Greenhouse URLs
    # e.g. https://boards.greenhouse.io/stripe
    # e.g. https://job-boards.greenhouse.io/stripe
    # e.g. https://boards.greenhouse.io/embed/job_board?for=stripe
    # e.g. greenhouse:stripe
    gh_embed = re.search(r"boards\.greenhouse\.io/embed/job_board\?.*?\bfor=([a-zA-Z0-9_-]+)", val, re.IGNORECASE)
    if gh_embed:
        return "greenhouse", gh_embed.group(1).lower()

    gh_match = re.search(r"(?:boards|job-boards)\.greenhouse\.io/([a-zA-Z0-9_-]+)", val, re.IGNORECASE)
    if gh_match:
        token = gh_match.group(1).lower()
        if token != "embed":
            return "greenhouse", token

    if val.lower().startswith("greenhouse:"):
        return "greenhouse", val.split(":", 1)[1].strip().lower()

    # Ashby URLs
    # e.g. https://jobs.ashbyhq.com/linear
    # e.g. ashby:linear
    ashby_match = re.search(r"jobs\.ashbyhq\.com/([a-zA-Z0-9_-]+)", val, re.IGNORECASE)
    if ashby_match:
        return "ashby", ashby_match.group(1).lower()

    if val.lower().startswith("ashby:"):
        return "ashby", val.split(":", 1)[1].strip().lower()

    # Lever URLs
    # e.g. https://jobs.lever.co/netflix
    # e.g. lever:netflix
    lever_match = re.search(r"jobs\.lever\.co/([a-zA-Z0-9_-]+)", val, re.IGNORECASE)
    if lever_match:
        return "lever", lever_match.group(1).lower()

    if val.lower().startswith("lever:"):
        return "lever", val.split(":", 1)[1].strip().lower()

    return None


def fetch_greenhouse_jobs(
    board_token: str,
    target_location: str | None = None,
    max_jobs: int = 100,
    conn: sqlite3.Connection | None = None,
    search_id: str | None = None,
    search_name: str | None = None,
) -> dict[str, Any]:
    """Fetch structured jobs from Greenhouse Public Board API.

    API Endpoint: https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true
    """
    token_clean = board_token.strip().lower()
    url = f"https://boards-api.greenhouse.io/v1/boards/{token_clean}/jobs?content=true"
    log.info("[Greenhouse] Fetching public board '%s'...", token_clean)

    try:
        req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log.error("[Greenhouse] Failed to fetch board '%s': %s", token_clean, e)
        return {"provider": "greenhouse", "board": token_clean, "new": 0, "existing": 0, "errors": 1, "total": 0, "jobs": []}

    job_items = data.get("jobs", [])
    if not job_items:
        log.info("[Greenhouse] 0 postings for board '%s'", token_clean)
        return {"provider": "greenhouse", "board": token_clean, "new": 0, "existing": 0, "errors": 0, "total": 0, "jobs": []}

    close_conn_on_exit = False
    if conn is None:
        conn = get_connection()
        close_conn_on_exit = True

    now = datetime.now(UTC).isoformat()
    new_count = 0
    existing_count = 0
    parsed_jobs: list[dict[str, Any]] = []

    # Format human-friendly company name from token
    company_name = token_clean.replace("-", " ").replace("_", " ").title()

    for item in job_items[:max_jobs]:
        title = (item.get("title") or "").strip()
        job_url = item.get("absolute_url")
        if not title or not job_url:
            continue

        raw_location = item.get("location", {}).get("name") or "Remote"
        loc_str = str(raw_location).strip()
        if target_location and target_location.lower() not in loc_str.lower():
            # If target location filter specified, skip non-matching
            pass

        # Raw HTML content
        raw_html = item.get("content") or ""
        decoded_html = html.unescape(raw_html)
        full_desc = clean_job_description(decoded_html) if decoded_html else "No description provided."

        liveness = check_posting_liveness(full_desc)
        is_expired = 1 if not liveness["is_live"] else 0
        expired_reason = liveness["reason"]

        repost_count = record_scan_history(conn, job_url, company_name, title, search_id)

        job_dict = {
            "url": job_url,
            "title": title,
            "company": company_name,
            "location": loc_str,
            "site": "Greenhouse",
            "strategy": "ats_api",
            "description": full_desc[:500],
            "full_description": full_desc,
            "application_url": job_url,
            "is_expired": is_expired,
            "expired_reason": expired_reason,
            "repost_count": repost_count,
        }
        parsed_jobs.append(job_dict)

        try:
            conn.execute(
                "INSERT INTO jobs (url, title, company, salary, description, location, site, strategy, discovered_at, "
                "full_description, application_url, detail_scraped_at, company_logo, search_name, search_id, "
                "is_expired, expired_reason, repost_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (job_url, title, company_name, None, full_desc[:500], loc_str, "Greenhouse", "ats_api", now,
                 full_desc, job_url, now, None, search_name or f"Greenhouse ({token_clean})", search_id,
                 is_expired, expired_reason, repost_count),
            )
            new_count += 1
        except sqlite3.IntegrityError:
            conn.execute(
                "UPDATE jobs SET company = ?, full_description = ?, search_id = COALESCE(search_id, ?), "
                "search_name = COALESCE(search_name, ?), repost_count = ?, is_expired = ?, expired_reason = ? WHERE url = ?",
                (company_name, full_desc, search_id, search_name, repost_count, is_expired, expired_reason, job_url)
            )
            existing_count += 1

    conn.commit()
    if close_conn_on_exit:
        conn.close()

    log.info("[Greenhouse] Board '%s': %d total -> %d new, %d updated", token_clean, len(parsed_jobs), new_count, existing_count)
    return {
        "provider": "greenhouse",
        "board": token_clean,
        "new": new_count,
        "existing": existing_count,
        "errors": 0,
        "total": len(parsed_jobs),
        "jobs": parsed_jobs,
    }


def fetch_ashby_jobs(
    organization_id: str,
    target_location: str | None = None,
    max_jobs: int = 100,
    conn: sqlite3.Connection | None = None,
    search_id: str | None = None,
    search_name: str | None = None,
) -> dict[str, Any]:
    """Fetch structured jobs from Ashby Public Job Board API.

    API Endpoint: https://api.ashbyhq.com/posting-api/job-board/{organization_id}
    """
    org_clean = organization_id.strip().lower()
    url = f"https://api.ashbyhq.com/posting-api/job-board/{org_clean}"
    log.info("[Ashby] Fetching public board '%s'...", org_clean)

    try:
        req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log.error("[Ashby] Failed to fetch board '%s': %s", org_clean, e)
        return {"provider": "ashby", "board": org_clean, "new": 0, "existing": 0, "errors": 1, "total": 0, "jobs": []}

    job_items = data.get("jobs", [])
    if not job_items:
        log.info("[Ashby] 0 postings for organization '%s'", org_clean)
        return {"provider": "ashby", "board": org_clean, "new": 0, "existing": 0, "errors": 0, "total": 0, "jobs": []}

    close_conn_on_exit = False
    if conn is None:
        conn = get_connection()
        close_conn_on_exit = True

    now = datetime.now(UTC).isoformat()
    new_count = 0
    existing_count = 0
    parsed_jobs: list[dict[str, Any]] = []

    company_name = org_clean.replace("-", " ").replace("_", " ").title()

    for item in job_items[:max_jobs]:
        title = (item.get("title") or "").strip()
        job_url = item.get("jobUrl") or item.get("applyUrl")
        if not title or not job_url:
            continue

        loc_parts = []
        if item.get("location"):
            loc_parts.append(str(item["location"]))
        if item.get("isRemote"):
            loc_parts.append("Remote")
        loc_str = ", ".join(loc_parts) if loc_parts else "Remote"

        raw_desc = item.get("descriptionPlain") or item.get("descriptionHtml") or ""
        full_desc = clean_job_description(raw_desc) if raw_desc else "No description provided."

        liveness = check_posting_liveness(full_desc)
        is_expired = 1 if not liveness["is_live"] else 0
        expired_reason = liveness["reason"]

        repost_count = record_scan_history(conn, job_url, company_name, title, search_id)
        apply_url = item.get("applyUrl") or job_url

        job_dict = {
            "url": job_url,
            "title": title,
            "company": company_name,
            "location": loc_str,
            "site": "Ashby",
            "strategy": "ats_api",
            "description": full_desc[:500],
            "full_description": full_desc,
            "application_url": apply_url,
            "is_expired": is_expired,
            "expired_reason": expired_reason,
            "repost_count": repost_count,
        }
        parsed_jobs.append(job_dict)

        try:
            conn.execute(
                "INSERT INTO jobs (url, title, company, salary, description, location, site, strategy, discovered_at, "
                "full_description, application_url, detail_scraped_at, company_logo, search_name, search_id, "
                "is_expired, expired_reason, repost_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (job_url, title, company_name, None, full_desc[:500], loc_str, "Ashby", "ats_api", now,
                 full_desc, apply_url, now, None, search_name or f"Ashby ({org_clean})", search_id,
                 is_expired, expired_reason, repost_count),
            )
            new_count += 1
        except sqlite3.IntegrityError:
            conn.execute(
                "UPDATE jobs SET company = ?, full_description = ?, search_id = COALESCE(search_id, ?), "
                "search_name = COALESCE(search_name, ?), repost_count = ?, is_expired = ?, expired_reason = ? WHERE url = ?",
                (company_name, full_desc, search_id, search_name, repost_count, is_expired, expired_reason, job_url)
            )
            existing_count += 1

    conn.commit()
    if close_conn_on_exit:
        conn.close()

    log.info("[Ashby] Board '%s': %d total -> %d new, %d updated", org_clean, len(parsed_jobs), new_count, existing_count)
    return {
        "provider": "ashby",
        "board": org_clean,
        "new": new_count,
        "existing": existing_count,
        "errors": 0,
        "total": len(parsed_jobs),
        "jobs": parsed_jobs,
    }


def fetch_lever_jobs(
    company: str,
    target_location: str | None = None,
    max_jobs: int = 100,
    conn: sqlite3.Connection | None = None,
    search_id: str | None = None,
    search_name: str | None = None,
) -> dict[str, Any]:
    """Fetch structured jobs from Lever Public Postings API.

    API Endpoint: https://api.lever.co/v0/postings/{company}?mode=json
    """
    comp_clean = company.strip().lower()
    url = f"https://api.lever.co/v0/postings/{comp_clean}?mode=json"
    log.info("[Lever] Fetching public board '%s'...", comp_clean)

    try:
        req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log.error("[Lever] Failed to fetch board '%s': %s", comp_clean, e)
        return {"provider": "lever", "board": comp_clean, "new": 0, "existing": 0, "errors": 1, "total": 0, "jobs": []}

    if not isinstance(data, list):
        log.warning("[Lever] Unexpected response format for '%s'", comp_clean)
        return {"provider": "lever", "board": comp_clean, "new": 0, "existing": 0, "errors": 1, "total": 0, "jobs": []}

    close_conn_on_exit = False
    if conn is None:
        conn = get_connection()
        close_conn_on_exit = True

    now = datetime.now(UTC).isoformat()
    new_count = 0
    existing_count = 0
    parsed_jobs: list[dict[str, Any]] = []

    company_name = comp_clean.replace("-", " ").replace("_", " ").title()

    for item in data[:max_jobs]:
        title = (item.get("text") or "").strip()
        job_url = item.get("hostedUrl") or item.get("applyUrl")
        if not title or not job_url:
            continue

        cats = item.get("categories", {})
        loc = cats.get("location") or "Remote"
        commitment = cats.get("commitment") or ""
        loc_str = f"{loc} ({commitment})" if commitment else loc

        desc_plain = item.get("descriptionPlain") or ""
        desc_html = item.get("description") or ""
        lists_content = ""
        for li_grp in item.get("lists", []):
            if isinstance(li_grp, dict):
                header = li_grp.get("text", "")
                bullets = li_grp.get("content", "")
                lists_content += f"\n\n{header}\n{bullets}"

        base_desc = desc_plain or desc_html
        raw_desc = f"{base_desc}\n\n{lists_content}".strip() if lists_content else base_desc
        full_desc = clean_job_description(raw_desc) if raw_desc else "No description provided."

        liveness = check_posting_liveness(full_desc)
        is_expired = 1 if not liveness["is_live"] else 0
        expired_reason = liveness["reason"]

        repost_count = record_scan_history(conn, job_url, company_name, title, search_id)
        apply_url = item.get("applyUrl") or job_url

        job_dict = {
            "url": job_url,
            "title": title,
            "company": company_name,
            "location": loc_str,
            "site": "Lever",
            "strategy": "ats_api",
            "description": full_desc[:500],
            "full_description": full_desc,
            "application_url": apply_url,
            "is_expired": is_expired,
            "expired_reason": expired_reason,
            "repost_count": repost_count,
        }
        parsed_jobs.append(job_dict)

        try:
            conn.execute(
                "INSERT INTO jobs (url, title, company, salary, description, location, site, strategy, discovered_at, "
                "full_description, application_url, detail_scraped_at, company_logo, search_name, search_id, "
                "is_expired, expired_reason, repost_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (job_url, title, company_name, None, full_desc[:500], loc_str, "Lever", "ats_api", now,
                 full_desc, apply_url, now, None, search_name or f"Lever ({comp_clean})", search_id,
                 is_expired, expired_reason, repost_count),
            )
            new_count += 1
        except sqlite3.IntegrityError:
            conn.execute(
                "UPDATE jobs SET company = ?, full_description = ?, search_id = COALESCE(search_id, ?), "
                "search_name = COALESCE(search_name, ?), repost_count = ?, is_expired = ?, expired_reason = ? WHERE url = ?",
                (company_name, full_desc, search_id, search_name, repost_count, is_expired, expired_reason, job_url)
            )
            existing_count += 1

    conn.commit()
    if close_conn_on_exit:
        conn.close()

    log.info("[Lever] Board '%s': %d total -> %d new, %d updated", comp_clean, len(parsed_jobs), new_count, existing_count)
    return {
        "provider": "lever",
        "board": comp_clean,
        "new": new_count,
        "existing": existing_count,
        "errors": 0,
        "total": len(parsed_jobs),
        "jobs": parsed_jobs,
    }


def fetch_ats_jobs_from_url(
    url_or_slug: str,
    target_location: str | None = None,
    max_jobs: int = 100,
    conn: sqlite3.Connection | None = None,
    search_id: str | None = None,
    search_name: str | None = None,
) -> dict[str, Any]:
    """Detect ATS provider from a URL or slug and fetch all postings via its public REST API."""
    detected = detect_ats_provider(url_or_slug)
    if not detected:
        return {
            "error": f"Could not detect ATS provider from '{url_or_slug}'. Supported: Greenhouse, Ashby, Lever.",
            "success": False,
            "total": 0,
        }

    provider, token = detected
    if provider == "greenhouse":
        res = fetch_greenhouse_jobs(token, target_location=target_location, max_jobs=max_jobs, conn=conn, search_id=search_id, search_name=search_name)
    elif provider == "ashby":
        res = fetch_ashby_jobs(token, target_location=target_location, max_jobs=max_jobs, conn=conn, search_id=search_id, search_name=search_name)
    elif provider == "lever":
        res = fetch_lever_jobs(token, target_location=target_location, max_jobs=max_jobs, conn=conn, search_id=search_id, search_name=search_name)
    else:
        return {"error": f"Unsupported provider: {provider}", "success": False, "total": 0}

    res["success"] = res.get("errors", 0) == 0
    return res
