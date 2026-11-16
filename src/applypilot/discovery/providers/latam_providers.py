"""Providers for LatAm & International Remote Job Boards: Get on Board and Remotive.

Fetches structured jobs directly from official APIs (Get on Board, Remotive),
normalizes fields, and stores results into DuckDuckWork's SQLite database.
"""

import json
import logging
import sqlite3
import urllib.parse
import urllib.request
from datetime import UTC, datetime

log = logging.getLogger(__name__)

from applypilot.database import get_connection

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


_GOB_COMPANY_CACHE: dict[str, dict[str, str | None]] = {}

def _get_getonboard_company_info(company_id: str | int | None) -> dict[str, str | None]:
    """Fetch company name and logo from Get on Board's company endpoint with caching and retries."""
    if not company_id:
        return {"name": "Get on Board Partner", "logo": None}

    cid_str = str(company_id).strip()
    if cid_str in _GOB_COMPANY_CACHE and _GOB_COMPANY_CACHE[cid_str]["name"] != "Get on Board Partner":
        return _GOB_COMPANY_CACHE[cid_str]

    url = f"https://www.getonbrd.com/api/v0/companies/{cid_str}"
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
            with urllib.request.urlopen(req, timeout=8) as resp:
                c_data = json.loads(resp.read().decode("utf-8")).get("data", {}).get("attributes", {})
                name = c_data.get("name")
                logo = c_data.get("logo")
                if name:
                    info = {"name": name, "logo": logo}
                    _GOB_COMPANY_CACHE[cid_str] = info
                    return info
        except Exception as e:
            log.debug("[GetOnBoard] Attempt %d failed for company %s: %s", attempt + 1, cid_str, e)

    return {"name": "Get on Board Partner", "logo": None}


# -- Get on Board API Provider -----------------------------------------------

def fetch_getonboard_jobs(query: str, target_location: str | None = None, limit: int = 30,
                          search_id: str | None = None, search_name: str | None = None,
                          allowed_locations: list[str] | None = None,
                          reject_locations: list[str] | None = None) -> dict:
    """Fetch jobs from Get on Board's official public API with location and closure filtering.
    
    API docs: https://api-doc.getonbrd.com/
    Endpoint: https://www.getonbrd.com/api/v0/search/jobs?query=...
    """
    clean_query = str(query).strip()
    encoded_query = urllib.parse.quote_plus(clean_query)
    url = f"https://www.getonbrd.com/api/v0/search/jobs?query={encoded_query}&per_page={limit}"

    log.info("[GetOnBoard] Fetching query='%s'...", clean_query)

    try:
        req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
        with urllib.request.urlopen(req, timeout=12) as resp:
            raw_data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log.error("[GetOnBoard] API request failed: %s", e)
        return {"new": 0, "existing": 0, "errors": 1, "total": 0}

    job_items = raw_data.get("data", [])
    if not job_items:
        log.info("[GetOnBoard] 0 results for '%s'", clean_query)
        return {"new": 0, "existing": 0, "errors": 0, "total": 0}

    conn = get_connection()
    now = datetime.now(UTC).isoformat()
    new = 0
    existing = 0

    allowed_lower = [str(loc).strip().lower() for loc in (allowed_locations or []) if str(loc).strip()]
    reject_lower = [str(loc).strip().lower() for loc in (reject_locations or []) if str(loc).strip()]

    for item in job_items:
        attrs = item.get("attributes", {})
        links = item.get("links", {})
        job_url = links.get("public_url") or f"https://www.getonbrd.com/jobs/{item.get('id')}"
        if not job_url:
            continue

        title = attrs.get("title")
        if not title:
            continue

        # Location and modality analysis
        remote_flag = attrs.get("remote", False)
        remote_modality = str(attrs.get("remote_modality", "")).strip().lower()
        countries = [str(c).strip() for c in attrs.get("countries", []) if str(c).strip() and str(c).strip() != "Remote"]
        primary_country = countries[0] if countries else None

        # Check explicit reject patterns against country and modality
        is_rejected = False
        for rej in reject_lower:
            if primary_country and rej in primary_country.lower():
                is_rejected = True
                break
        if is_rejected:
            log.info("[GetOnBoard] Skipping '%s' - country '%s' matches reject patterns", title, primary_country)
            continue

        # Check allowed locations:
        # If candidate specified target locations (e.g. Guatemala, Nicaragua):
        # A job is acceptable IF:
        # 1. It is fully_remote (open worldwide)
        # 2. OR one of its countries matches the candidate's allowed locations
        if allowed_lower:
            is_fully_remote = (remote_modality == "fully_remote")
            matches_allowed = False
            for al in allowed_lower:
                if al in ("remote", "worldwide", "anywhere"):
                    if is_fully_remote:
                        matches_allowed = True
                        break
                elif primary_country and (al in primary_country.lower() or primary_country.lower() in al):
                    matches_allowed = True
                    break

            if not matches_allowed and not is_fully_remote:
                log.info("[GetOnBoard] Skipping '%s' - restricted to %s (not in allowed: %s)", title, countries or [remote_modality], allowed_locations)
                continue

        # Build accurate country and location string
        if remote_modality == "fully_remote":
            job_country = "Worldwide"
            loc_str = "Worldwide (Remote)"
        elif primary_country:
            job_country = primary_country
            if remote_flag or "remote" in remote_modality:
                loc_str = f"{job_country} (Remote)"
            elif "hybrid" in remote_modality:
                loc_str = f"{job_country} (Hybrid)"
            else:
                loc_str = f"{job_country}"
        else:
            job_country = target_location or "Worldwide"
            loc_str = f"{job_country} (Remote)" if remote_flag else job_country

        # Fetch real company name & logo using company ID
        company_data = attrs.get("company", {}).get("data", {})
        company_id = company_data.get("id") if isinstance(company_data, dict) else None
        company_info = _get_getonboard_company_info(company_id)
        company_name = company_info["name"] or "Get on Board Partner"
        company_logo = company_info["logo"]

        # Build salary string
        min_sal = attrs.get("min_salary")
        max_sal = attrs.get("max_salary")
        salary_str = None
        if min_sal:
            if max_sal and max_sal != min_sal:
                salary_str = f"${int(min_sal):,}-${int(max_sal):,}/mo (USD)"
            else:
                salary_str = f"${int(min_sal):,}/mo (USD)"

        # Assemble full description HTML & sanitize
        desc_parts = []
        if attrs.get("projects"):
            desc_parts.append(f"<h4>About the Role</h4>{attrs['projects']}")
        if attrs.get("functions"):
            desc_parts.append(f"<h4>Responsibilities</h4>{attrs['functions']}")
        if attrs.get("benefits"):
            desc_parts.append(f"<h4>Benefits</h4>{attrs['benefits']}")
        if attrs.get("description"):
            desc_parts.append(attrs["description"])

        raw_desc = "\n".join(desc_parts) if desc_parts else "No description provided."
        from applypilot.enrichment.cleaner import clean_job_description
        from applypilot.discovery.liveness import check_posting_liveness
        from applypilot.database import record_scan_history

        full_desc = clean_job_description(raw_desc)
        repost_count = record_scan_history(conn, job_url, company_name, title, search_id)

        # Liveness & closure evaluation
        liveness = check_posting_liveness(full_desc)
        is_expired = 1 if not liveness["is_live"] else 0
        expired_reason = liveness["reason"]

        if attrs.get("applications_closed") is True:
            is_expired = 1
            expired_reason = "Get on Board applications closed"
        elif str(attrs.get("status", "")).lower() in ("closed", "expired", "archived", "filled"):
            is_expired = 1
            expired_reason = f"Get on Board status: {attrs.get('status')}"

        try:
            conn.execute(
                "INSERT INTO jobs (url, title, company, salary, description, location, site, strategy, discovered_at, "
                "full_description, application_url, detail_scraped_at, company_logo, search_name, search_id, "
                "country, is_expired, expired_reason, repost_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (job_url, title, company_name, salary_str, full_desc[:500], loc_str, "Get on Board", "api", now,
                 full_desc, job_url, now, company_logo, search_name or "GetOnBoard Search", search_id,
                 job_country, is_expired, expired_reason, repost_count),
            )
            new += 1
        except sqlite3.IntegrityError:
            conn.execute(
                "UPDATE jobs SET company = ?, company_logo = COALESCE(company_logo, ?), full_description = ?, "
                "location = ?, country = ?, search_id = COALESCE(search_id, ?), search_name = COALESCE(search_name, ?), "
                "repost_count = ?, is_expired = ?, expired_reason = ? WHERE url = ?",
                (company_name, company_logo, full_desc, loc_str, job_country, search_id, search_name, repost_count, is_expired, expired_reason, job_url)
            )
            existing += 1

    conn.commit()
    log.info("[GetOnBoard] '%s': %d total -> %d new, %d dupes", clean_query, len(job_items), new, existing)
    return {"new": new, "existing": existing, "errors": 0, "total": len(job_items)}


# -- Remotive API Provider ---------------------------------------------------

def fetch_remotive_jobs(query: str, target_location: str | None = None, limit: int = 30,
                        search_id: str | None = None, search_name: str | None = None,
                        allowed_locations: list[str] | None = None,
                        reject_locations: list[str] | None = None) -> dict:
    """Fetch remote jobs from Remotive's official public API.
    
    Endpoint: https://remotive.com/api/remote-jobs?search=...
    """
    clean_query = str(query).strip()
    encoded_query = urllib.parse.quote_plus(clean_query)
    url = f"https://remotive.com/api/remote-jobs?search={encoded_query}&limit={limit}"

    log.info("[Remotive] Fetching query='%s'...", clean_query)

    try:
        req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
        with urllib.request.urlopen(req, timeout=12) as resp:
            raw_data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log.error("[Remotive] API request failed: %s", e)
        return {"new": 0, "existing": 0, "errors": 1, "total": 0}

    job_items = raw_data.get("jobs", [])
    if not job_items:
        log.info("[Remotive] 0 results for '%s'", clean_query)
        return {"new": 0, "existing": 0, "errors": 0, "total": 0}

    conn = get_connection()
    now = datetime.now(UTC).isoformat()
    new = 0
    existing = 0

    from applypilot.enrichment.cleaner import clean_job_description

    reject_lower = [str(loc).strip().lower() for loc in (reject_locations or []) if str(loc).strip()]

    for item in job_items[:limit]:
        job_url = item.get("url")
        title = item.get("title")
        if not job_url or not title:
            continue

        company_name = item.get("company_name", "Remote Company")
        logo_url = item.get("company_logo")
        salary = item.get("salary") or None
        req_loc = str(item.get("candidate_required_location") or "Worldwide").strip()

        # Reject checks
        if any(rej in req_loc.lower() for rej in reject_lower):
            continue

        job_country = req_loc if req_loc else "Worldwide"
        location_str = f"{req_loc} (Remote)" if not req_loc.endswith("(Remote)") else req_loc
        raw_desc = item.get("description") or ""
        full_desc = clean_job_description(raw_desc)
        from applypilot.discovery.liveness import check_posting_liveness
        from applypilot.database import record_scan_history

        repost_count = record_scan_history(conn, job_url, company_name, title, search_id)
        liveness = check_posting_liveness(full_desc)
        is_expired = 1 if not liveness["is_live"] else 0
        expired_reason = liveness["reason"]

        try:
            conn.execute(
                "INSERT INTO jobs (url, title, company, salary, description, location, site, strategy, discovered_at, "
                "full_description, application_url, detail_scraped_at, company_logo, search_name, search_id, "
                "country, is_expired, expired_reason, repost_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (job_url, title, company_name, salary, full_desc[:500], location_str, "Remotive", "api", now,
                 full_desc, job_url, now, logo_url, search_name or "Remotive Remote Search", search_id,
                 job_country, is_expired, expired_reason, repost_count),
            )
            new += 1
        except sqlite3.IntegrityError:
            conn.execute(
                "UPDATE jobs SET full_description = ?, location = ?, country = ?, search_id = COALESCE(search_id, ?), "
                "search_name = COALESCE(search_name, ?), repost_count = ?, is_expired = ?, expired_reason = ? WHERE url = ?",
                (full_desc, location_str, job_country, search_id, search_name, repost_count, is_expired, expired_reason, job_url)
            )
            existing += 1

    conn.commit()
    log.info("[Remotive] '%s': %d total -> %d new, %d dupes", clean_query, len(job_items), new, existing)
    return {"new": new, "existing": existing, "errors": 0, "total": len(job_items)}


# -- Hire With Near API Provider ---------------------------------------------

def fetch_hirewithnear_jobs(
    query: str,
    target_location: str | None = None,
    limit: int = 50,
    search_id: str | None = None,
    search_name: str | None = None,
    allowed_locations: list[str] | None = None,
    reject_locations: list[str] | None = None,
    hours_old: int = 48,
    conn: sqlite3.Connection | None = None,
) -> dict:
    """Fetch remote LatAm jobs from Hire With Near's official public REST API.

    Endpoint: https://jobs.hirewithnear.com/api/jobs/list?q=...&start=0&count=...
    """
    clean_query = str(query).strip()
    encoded_query = urllib.parse.quote_plus(clean_query)
    count = max(10, min(limit, 50))
    url = f"https://jobs.hirewithnear.com/api/jobs/list?q={encoded_query}&start=0&count={count}"

    log.info("[HireWithNear] Fetching query='%s'...", clean_query)

    try:
        req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
        with urllib.request.urlopen(req, timeout=12) as resp:
            raw_data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log.error("[HireWithNear] API request failed: %s", e)
        return {"new": 0, "existing": 0, "errors": 1, "total": 0}

    job_items = raw_data.get("jobs", [])
    if not job_items:
        log.info("[HireWithNear] 0 results for '%s'", clean_query)
        return {"new": 0, "existing": 0, "errors": 0, "total": 0}

    if conn is None:
        conn = get_connection()
    now = datetime.now(UTC).isoformat()
    new = 0
    existing = 0

    import re
    from applypilot.database import record_scan_history
    from applypilot.discovery.liveness import check_posting_liveness
    from applypilot.enrichment.cleaner import clean_job_description

    reject_lower = [str(loc).strip().lower() for loc in (reject_locations or []) if str(loc).strip()]

    for item in job_items:
        job_id = item.get("id")
        title = item.get("title")
        if not job_id or not title:
            continue

        job_url = f"https://jobs.hirewithnear.com/jobs/{job_id}"
        company_name = item.get("clientCorporation") or "Hire With Near"
        company_logo = "https://jobs.hirewithnear.com/icon.png"

        # Check date posted recency
        date_posted_ms = item.get("datePosted")
        posted_iso = None
        if date_posted_ms:
            try:
                dt_posted = datetime.fromtimestamp(date_posted_ms / 1000, tz=UTC)
                posted_iso = dt_posted.isoformat()
                if hours_old and hours_old > 0:
                    age_hours = (datetime.now(UTC) - dt_posted).total_seconds() / 3600
                    if age_hours > hours_old:
                        continue
            except Exception:
                pass

        # Extract salary if available
        salary_str = None
        sal_low = item.get("salaryLow")
        sal_high = item.get("salaryHigh")
        sal_unit = item.get("salaryUnit") or "month"
        if sal_low and sal_high:
            salary_str = f"${sal_low:,.0f} - ${sal_high:,.0f} USD/{sal_unit}"
        elif sal_low:
            salary_str = f"${sal_low:,.0f} USD/{sal_unit}"
        else:
            m_sal = re.search(r'(?:Compensation|Salary|Pay):\s*([^\n<]+)', item.get("publicDescription") or "", re.I)
            if m_sal:
                salary_str = re.sub(r'<[^>]+>', '', m_sal.group(1)).strip()[:60]

        addr = item.get("address") or {}
        city = (addr.get("city") or "").strip()
        state = (addr.get("state") or "").strip()
        country_name = (addr.get("country") or "").strip()
        loc_parts = [p for p in [city, state, country_name] if p and p.lower() not in ("remote", "latam", "latin america")]
        if loc_parts:
            loc_str = f"{', '.join(loc_parts)} (Remote)"
            job_country = country_name or state or "Latin America"
        else:
            loc_str = "Latin America (Remote)"
            job_country = "Latin America"

        # Check location exclusions
        loc_check_text = f"{loc_str} {city} {state} {country_name} {item.get('publicDescription') or ''}".lower()
        if any(rej in loc_check_text for rej in reject_lower):
            continue

        raw_desc = item.get("publicDescription") or ""
        full_desc = clean_job_description(raw_desc)

        # Liveness & closure evaluation
        liveness = check_posting_liveness(full_desc)
        is_expired = 1 if not liveness["is_live"] else 0
        expired_reason = liveness["reason"]

        if item.get("isOpen") is False or item.get("isPublic") is False:
            is_expired = 1
            expired_reason = "Hire With Near listing closed"

        repost_count = record_scan_history(conn, job_url, company_name, title, search_id)

        try:
            conn.execute(
                "INSERT INTO jobs (url, title, company, salary, description, location, site, strategy, discovered_at, "
                "full_description, application_url, detail_scraped_at, company_logo, search_name, search_id, "
                "country, is_expired, expired_reason, repost_count, posted_date) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (job_url, title, company_name, salary_str, full_desc[:500], loc_str, "Hire With Near", "api", now,
                 full_desc, job_url, now, company_logo, search_name or "Hire With Near Search", search_id,
                 job_country, is_expired, expired_reason, repost_count, posted_iso),
            )
            new += 1
        except sqlite3.IntegrityError:
            conn.execute(
                "UPDATE jobs SET company = ?, company_logo = COALESCE(company_logo, ?), full_description = ?, "
                "location = ?, country = ?, search_id = COALESCE(search_id, ?), search_name = COALESCE(search_name, ?), "
                "repost_count = ?, is_expired = ?, expired_reason = ?, posted_date = COALESCE(posted_date, ?) WHERE url = ?",
                (company_name, company_logo, full_desc, loc_str, job_country, search_id, search_name, repost_count, is_expired, expired_reason, posted_iso, job_url)
            )
            existing += 1

    conn.commit()
    log.info("[HireWithNear] '%s': %d total -> %d new, %d dupes", clean_query, len(job_items), new, existing)
    return {"new": new, "existing": existing, "errors": 0, "total": len(job_items)}
