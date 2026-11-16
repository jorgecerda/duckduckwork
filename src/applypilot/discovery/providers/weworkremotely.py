"""WeWorkRemotely RSS Feed Provider.

Fetches structured remote jobs from WeWorkRemotely's official RSS feed,
sanitizes descriptions with clean_job_description, and stores results into DuckDuckWork DB.
"""

import logging
import re
import sqlite3
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

log = logging.getLogger(__name__)

from applypilot.database import get_connection
from applypilot.enrichment.cleaner import clean_job_description

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}
WWR_RSS_URL = "https://weworkremotely.com/remote-jobs.rss"


def _get_wwr_company_logo(job_url: str, full_desc: str) -> str | None:
    """Fetch official company logo URL for a WeWorkRemotely job listing."""
    headers = {"User-Agent": DEFAULT_HEADERS["User-Agent"]}
    try:
        req = urllib.request.Request(job_url, headers=headers)
        with urllib.request.urlopen(req, timeout=4) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
            m = re.search(r"(https://we-work-remotely\.imgix\.net/logos/[^\s\'\"\)]+)", html)
            if m:
                return m.group(1)
    except Exception as e:
        log.debug("[WeWorkRemotely] Logo page fetch failed for %s: %s", job_url, e)

    url_m = re.search(r"<strong>URL:</strong>\s*<a href=[\'\"]([^\'\"]+)[\'\"]", full_desc, re.IGNORECASE)
    if url_m:
        dom_m = re.search(r"https?://(?:www\.)?([^/]+)", url_m.group(1))
        if dom_m:
            return f"https://logo.clearbit.com/{dom_m.group(1)}"

    return None


def fetch_weworkremotely_jobs(query: str, target_location: str | None = None, limit: int = 30,
                              search_id: str | None = None, search_name: str | None = None) -> dict:
    """Fetch remote jobs from WeWorkRemotely official RSS feed."""
    clean_query = str(query).strip().lower()

    log.info("[WeWorkRemotely] Fetching RSS feed for query='%s'...", clean_query)

    try:
        req = urllib.request.Request(WWR_RSS_URL, headers=DEFAULT_HEADERS)
        with urllib.request.urlopen(req, timeout=12) as resp:
            raw_xml = resp.read()
        root = ET.fromstring(raw_xml)
    except Exception as e:
        log.error("[WeWorkRemotely] RSS request failed: %s", e)
        return {"new": 0, "existing": 0, "errors": 1, "total": 0}

    items = root.findall("./channel/item")
    if not items:
        log.info("[WeWorkRemotely] 0 items found in RSS feed")
        return {"new": 0, "existing": 0, "errors": 0, "total": 0}

    conn = get_connection()
    now = datetime.now(UTC).isoformat()
    new = 0
    existing = 0
    matched = 0

    # Words in query for filtering
    query_words = [w for w in clean_query.split() if len(w) > 2]

    for item in items:
        title_raw = item.findtext("title") or ""
        link = item.findtext("link") or ""
        if not link or not title_raw:
            continue

        # Parse Company & Title from "Company: Job Title" format
        if ":" in title_raw:
            parts = title_raw.split(":", 1)
            company_name = parts[0].strip()
            job_title = parts[1].strip()
        else:
            company_name = "WeWorkRemotely Partner"
            job_title = title_raw.strip()

        # Check if query matches job_title or company_name
        search_target = f"{job_title} {company_name}".lower()
        if query_words and not any(qw in search_target for qw in query_words):
            continue

        matched += 1
        raw_desc = item.findtext("description") or ""
        full_desc = clean_job_description(raw_desc)
        pub_date = item.findtext("pubDate") or now
        location_str = "Worldwide (Remote)"
        company_logo = _get_wwr_company_logo(link, full_desc)

        from applypilot.discovery.liveness import check_posting_liveness
        from applypilot.database import record_scan_history

        repost_count = record_scan_history(conn, link, company_name, job_title, search_id)
        liveness = check_posting_liveness(full_desc)
        is_expired = 1 if not liveness["is_live"] else 0
        expired_reason = liveness["reason"]

        try:
            conn.execute(
                "INSERT INTO jobs (url, title, company, salary, description, location, site, strategy, discovered_at, "
                "full_description, application_url, detail_scraped_at, search_name, search_id, company_logo, "
                "is_expired, expired_reason, repost_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (link, job_title, company_name, None, full_desc[:500], location_str, "WeWorkRemotely", "rss", pub_date,
                 full_desc, link, now, search_name or "WeWorkRemotely RSS Search", search_id, company_logo,
                 is_expired, expired_reason, repost_count),
            )
            new += 1
        except sqlite3.IntegrityError:
            conn.execute(
                "UPDATE jobs SET full_description = ?, company_logo = COALESCE(company_logo, ?), "
                "search_id = COALESCE(search_id, ?), search_name = COALESCE(search_name, ?), repost_count = ?, "
                "is_expired = ?, expired_reason = ? WHERE url = ?",
                (full_desc, company_logo, search_id, search_name, repost_count, is_expired, expired_reason, link)
            )
            existing += 1

        if matched >= limit:
            break

    conn.commit()
    log.info("[WeWorkRemotely] '%s': %d matched -> %d new, %d dupes", clean_query, matched, new, existing)
    return {"new": new, "existing": existing, "errors": 0, "total": matched}
