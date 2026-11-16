"""JobSpy-based job discovery: searches Indeed, LinkedIn, Glassdoor, ZipRecruiter.

Uses python-jobspy to scrape multiple job boards, deduplicates results,
parses salary ranges, and stores everything in the DuckDuckWork database.

Search queries, locations, and filtering rules are loaded from the user's
search configuration YAML (searches.yaml) rather than being hardcoded.
"""

import logging
import os
import sqlite3
import time
from datetime import UTC, datetime

log = logging.getLogger(__name__)

try:
    from jobspy import scrape_jobs
    from jobspy.model import Country

    # Monkey-patch JobSpy's Country.from_string to prevent crashes on unlisted countries (e.g. Guatemala, Nicaragua)
    _orig_country_from_string = Country.from_string

    def _safe_country_from_string(val: str):
        try:
            return _orig_country_from_string(val)
        except Exception:
            return Country.WORLDWIDE

    Country.from_string = _safe_country_from_string
except ImportError:
    scrape_jobs = None
    Country = None

from applypilot import config
from applypilot.database import get_connection, init_db

VALID_JOBSPY_COUNTRIES = {
    "argentina", "australia", "austria", "bahrain", "bangladesh", "belgium", "bulgaria", "brazil", "canada",
    "chile", "china", "colombia", "costa rica", "croatia", "cyprus", "czech republic", "czechia", "denmark",
    "ecuador", "egypt", "estonia", "finland", "france", "germany", "greece", "hong kong", "hungary", "india",
    "indonesia", "ireland", "israel", "italy", "japan", "kuwait", "latvia", "lithuania", "luxembourg", "malaysia",
    "malta", "mexico", "morocco", "netherlands", "new zealand", "nigeria", "norway", "oman", "pakistan", "panama",
    "peru", "philippines", "poland", "portugal", "qatar", "romania", "saudi arabia", "singapore", "slovakia",
    "slovenia", "south africa", "south korea", "spain", "sweden", "switzerland", "taiwan", "thailand", "türkiye",
    "turkey", "ukraine", "united arab emirates", "uk", "united kingdom", "usa", "us", "united states", "uruguay",
    "venezuela", "vietnam", "usa/ca", "worldwide"
}

def sanitize_jobspy_country(c_val: str | None) -> str:
    if not c_val:
        return "worldwide"
    clean = str(c_val).strip().lower()
    if clean in VALID_JOBSPY_COUNTRIES:
        return clean
    return "worldwide"


# -- Proxy parsing -----------------------------------------------------------

def parse_proxy(proxy_str: str | None) -> dict | None:
    """Parse host:port:user:pass, host:port, or http(s)://user:pass@host:port into components."""
    if not proxy_str:
        return None
    proxy_str = str(proxy_str).strip()
    if not proxy_str:
        return None

    import urllib.parse
    if proxy_str.startswith("http://") or proxy_str.startswith("https://"):
        try:
            parsed = urllib.parse.urlparse(proxy_str)
            host = parsed.hostname or ""
            port = str(parsed.port or (443 if parsed.scheme == "https" else 80))
            user = parsed.username
            passwd = parsed.password
            if user and passwd:
                return {
                    "host": host,
                    "port": port,
                    "user": user,
                    "pass": passwd,
                    "jobspy": f"{user}:{passwd}@{host}:{port}",
                    "playwright": {
                        "server": f"http://{host}:{port}",
                        "username": user,
                        "password": passwd,
                    },
                }
            else:
                return {
                    "host": host,
                    "port": port,
                    "user": None,
                    "pass": None,
                    "jobspy": f"{host}:{port}",
                    "playwright": {"server": f"http://{host}:{port}"},
                }
        except Exception:
            pass

    parts = proxy_str.split(":")
    if len(parts) == 4:
        host, port, user, passwd = parts
        return {
            "host": host,
            "port": port,
            "user": user,
            "pass": passwd,
            "jobspy": f"{user}:{passwd}@{host}:{port}",
            "playwright": {
                "server": f"http://{host}:{port}",
                "username": user,
                "password": passwd,
            },
        }
    elif len(parts) == 2:
        host, port = parts
        return {
            "host": host,
            "port": port,
            "user": None,
            "pass": None,
            "jobspy": f"{host}:{port}",
            "playwright": {"server": f"http://{host}:{port}"},
        }
    else:
        log.warning("Proxy format not recognized: %s", proxy_str)
        return None


def is_proxy_disabled() -> bool:
    """Return True if running locally or if proxy is explicitly disabled."""
    val = os.environ.get("APPLYPILOT_DISABLE_PROXY", "").lower()
    if val in ("true", "1"):
        return True
    if val in ("false", "0"):
        return False
    # If not running on Vercel, default to disabling proxy to guarantee 0 Webshare API consumption locally
    if not os.environ.get("VERCEL"):
        return True
    return False


def fetch_webshare_info(api_key: str | None = None) -> dict:
    """Fetch proxy list, subscription info, and usage stats from Webshare REST API v2."""
    info = {
        "valid": False,
        "proxies": [],
        "proxy_count": 0,
        "subscription": {},
        "stats": {},
        "countries": [],
    }
    if is_proxy_disabled():
        return info

    token = (api_key or os.environ.get("WEBSHARE_API_KEY") or "").strip()
    if not token:
        try:
            from applypilot.server import load_admin_config
            token = (load_admin_config().get("webshare_api_key") or "").strip()
        except Exception:
            pass

    if not token:
        return info

    headers = {"Authorization": f"Token {token}"}
    import json
    import urllib.request

    # 1. Fetch direct proxy list
    try:
        req = urllib.request.Request(
            "https://proxy.webshare.io/api/v2/proxy/list/?mode=direct&page=1&page_size=25",
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                results = data.get("results", [])
                info["proxy_count"] = data.get("count", len(results))
                country_set = set()
                for p in results:
                    c_code = p.get("country_code")
                    if c_code:
                        country_set.add(c_code)
                    info["proxies"].append({
                        "ip": p.get("proxy_address"),
                        "port": p.get("port"),
                        "user": p.get("username"),
                        "pass": p.get("password"),
                        "country": c_code,
                        "city": p.get("city_name"),
                        "valid": p.get("valid", True),
                    })
                info["countries"] = sorted(list(country_set))
                info["valid"] = True
    except Exception as e:
        info["proxy_error"] = str(e)

    # 2. Fetch subscription
    try:
        req = urllib.request.Request(
            "https://proxy.webshare.io/api/v2/subscription/",
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            if resp.status == 200:
                info["subscription"] = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        info["sub_error"] = str(e)

    # 3. Fetch stats
    try:
        req = urllib.request.Request(
            "https://proxy.webshare.io/api/v2/stats/",
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            if resp.status == 200:
                stats_list = json.loads(resp.read().decode("utf-8"))
                if stats_list and isinstance(stats_list, list):
                    total_bw = sum(s.get("bandwidth_total", 0) for s in stats_list)
                    total_reqs = sum(s.get("requests_total", 0) for s in stats_list)
                    succ_reqs = sum(s.get("requests_successful", 0) for s in stats_list)
                    failed_reqs = sum(s.get("requests_failed", 0) for s in stats_list)
                    info["stats"] = {
                        "bandwidth_bytes": total_bw,
                        "bandwidth_mb": round(total_bw / (1024 * 1024), 2),
                        "requests_total": total_reqs,
                        "requests_successful": succ_reqs,
                        "requests_failed": failed_reqs,
                    }
    except Exception as e:
        info["stats_error"] = str(e)

    return info


def resolve_webshare_proxy(api_key: str | None = None, proxy_override: str | None = None) -> dict | None:
    """Resolve direct proxy from Webshare API Key or manual proxy string."""
    if is_proxy_disabled():
        return None

    if proxy_override and str(proxy_override).strip():
        parsed = parse_proxy(proxy_override)
        if parsed:
            return parsed

    info = fetch_webshare_info(api_key=api_key)
    if info.get("valid") and info.get("proxies"):
        pool: list[str] = []
        first_parsed: dict | None = None
        for p in info["proxies"]:
            if p.get("valid", True) and p.get("ip") and p.get("port") and p.get("user") and p.get("pass"):
                p_str = f"http://{p['user']}:{p['pass']}@{p['ip']}:{p['port']}"
                parsed = parse_proxy(p_str)
                if parsed:
                    parsed["country_code"] = p.get("country")
                    pool.append(parsed["jobspy"])
                    if first_parsed is None:
                        first_parsed = parsed
        if first_parsed:
            first_parsed["pool"] = pool
            return first_parsed

    try:
        from applypilot.server import load_admin_config
        cfg_proxy = load_admin_config().get("proxy")
        if cfg_proxy:
            return parse_proxy(cfg_proxy)
    except Exception:
        pass

    return None


# -- Retry wrapper -----------------------------------------------------------

def _scrape_with_retry(kwargs: dict, max_retries: int = 2, backoff: float = 5.0):
    """Call scrape_jobs with Direct-First proxy conservation and retry on transient failures."""
    configured_proxies = kwargs.get("proxies")
    direct_first = os.environ.get("APPLYPILOT_DIRECT_FIRST", "1").lower() not in ("0", "false")

    for attempt in range(max_retries + 1):
        call_kwargs = dict(kwargs)
        # Attempt 0: try direct first if proxies are configured to conserve Webshare 1GB free bandwidth
        if attempt == 0 and configured_proxies and direct_first:
            call_kwargs.pop("proxies", None)
        elif configured_proxies:
            call_kwargs["proxies"] = configured_proxies

        try:
            return scrape_jobs(**call_kwargs)
        except Exception as e:
            err = str(e).lower()
            if "403" in err and not configured_proxies:
                # 403 Forbidden is non-transient when no fallback proxy exists
                raise
            transient = any(k in err for k in ("timeout", "429", "403", "proxy", "connection", "reset", "refused"))
            if transient and attempt < max_retries:
                wait = 1.0 if (attempt == 0 and configured_proxies and direct_first) else backoff * (attempt + 1)
                log.warning("Retry %d/%d in %.0fs (using proxy=%s): %s", attempt + 1, max_retries, wait, bool(configured_proxies), e)
                time.sleep(wait)
            else:
                raise


# -- Location filtering ------------------------------------------------------

def _load_location_config(search_cfg: dict) -> tuple[list[str], list[str]]:
    """Extract accept/reject location lists from search config.

    Falls back to sensible defaults if not defined in the YAML.
    """
    accept = search_cfg.get("location_accept", [])
    reject = search_cfg.get("location_reject_non_remote", [])
    return accept, reject


def _location_ok(location: str | None, accept: list[str], reject: list[str]) -> bool:
    """Check if a job location passes the user's location filter.

    Reject patterns are checked first so that locations like 'US Remote' or 'Canada'
    are rejected even if they contain the word 'remote'.
    """
    if not location:
        return True  # unknown location -- keep it, let scorer decide

    loc = location.lower()

    # Reject matches FIRST (e.g. "USA", "United States", "US Remote")
    for r in reject:
        if r and r.lower() in loc:
            return False

    if not accept:
        return True

    # Worldwide / Global searches accept all locations
    if any(a.lower() in ("worldwide", "anywhere", "global", "all") for a in accept):
        return True

    # Remote jobs OK if not rejected
    if any(r in loc for r in ("remote", "anywhere", "work from home", "wfh", "distributed")):
        return True

    # Accept matches
    for a in accept:
        a_clean = a.lower()
        if a_clean in loc or loc in a_clean:
            return True

    # If search was targeted to a specific location (in accept), and the job board
    # returned a result for that query (e.g. "Managua, Managua"), accept it as long as it
    # does not match an explicit reject rule (e.g. "USA").
    if accept:
        return True

    return False


# -- Title filtering ---------------------------------------------------------

def _title_ok(title: str | None, query: str | None, match_mode: str = "flexible") -> bool:
    """Check if a job title matches the search query.

    Filters out completely unrelated roles returned by job boards full-text search
    (e.g., 'Software Engineer' when searching for 'Scrum Master').
    """
    if not title or not query or match_mode == "off":
        return True

    clean_t = title.lower()
    clean_q = query.lower()

    if clean_q in clean_t:
        return True

    import re
    q_words = [w for w in re.findall(r'\b\w+\b', clean_q) if len(w) > 1]
    if not q_words:
        return True

    stop_modifiers = {"senior", "junior", "lead", "staff", "principal", "head", "director", "vp", "manager", "remote", "hybrid"}
    core_q_words = [w for w in q_words if w not in stop_modifiers]
    if not core_q_words:
        core_q_words = q_words

    t_words = set(re.findall(r'\b\w+\b', clean_t))

    synonyms = {
        "scrum": {"agile", "kanban", "scrum"},
        "agile": {"scrum", "agile", "kanban"},
        "fullstack": {"full", "stack", "frontend", "backend", "fullstack"},
        "frontend": {"front", "end", "ui", "react", "vue", "angular", "frontend"},
        "backend": {"back", "end", "api", "backend", "python", "node", "java", "golang"},
        "devops": {"sre", "cloud", "infrastructure", "devops", "platform"},
    }

    matches = 0
    for qw in core_q_words:
        if qw in t_words:
            matches += 1
            continue
        if qw in synonyms and any(syn in t_words for syn in synonyms[qw]):
            matches += 1
            continue
        if any(qw in tw or tw in qw for tw in t_words if len(tw) > 3):
            matches += 1
            continue

    if match_mode == "strict":
        return matches == len(core_q_words)
    else:
        min_matches = 1 if len(core_q_words) <= 2 else max(1, len(core_q_words) // 2)
        return matches >= min_matches


def _poster_ok(company: str | None, blocked_posters: list[str] | None) -> bool:
    """Check if a company is not in the blocked posters list."""
    if not company or not blocked_posters:
        return True
    import re
    company_clean = re.sub(r"[^a-zA-Z0-9]", "", company).lower()
    for bp in blocked_posters:
        bp_clean = re.sub(r"[^a-zA-Z0-9]", "", str(bp)).lower()
        if bp_clean and (bp_clean in company_clean or company_clean in bp_clean):
            return False
    return True


# -- DB storage (JobSpy DataFrame -> SQLite) ---------------------------------

def store_jobspy_results(
    conn: sqlite3.Connection,
    df,
    source_label: str,
    target_location: str | None = None,
    search_id: str | None = None,
    search_name: str | None = None,
) -> tuple[int, int]:
    """Store JobSpy DataFrame results into the DB. Returns (new, existing)."""
    now = datetime.now(UTC).isoformat()
    new = 0
    existing = 0

    for _, row in df.iterrows():
        url = str(row.get("job_url", ""))
        if not url or url == "nan":
            continue

        title = str(row.get("title", "")) if str(row.get("title", "")) != "nan" else None
        company = str(row.get("company", "")) if str(row.get("company", "")) != "nan" else None
        location_str = str(row.get("location", "")) if str(row.get("location", "")) != "nan" else None

        if target_location:
            if not location_str or location_str.strip().lower() in ("remote", "work from home", "wfh"):
                location_str = f"{target_location} (Remote)"
            elif target_location.lower() not in location_str.lower():
                location_str = f"{target_location}, {location_str}"

        # Build salary string from min/max
        salary = None
        min_amt = row.get("min_amount")
        max_amt = row.get("max_amount")
        interval = str(row.get("interval", "")) if str(row.get("interval", "")) != "nan" else ""
        currency = str(row.get("currency", "")) if str(row.get("currency", "")) != "nan" else ""
        if min_amt and str(min_amt) != "nan":
            if max_amt and str(max_amt) != "nan":
                salary = f"{currency}{int(float(min_amt)):,}-{currency}{int(float(max_amt)):,}"
            else:
                salary = f"{currency}{int(float(min_amt)):,}"
            if interval:
                salary += f"/{interval}"

        description = str(row.get("description", "")) if str(row.get("description", "")) != "nan" else None

        site_name = str(row.get("site", source_label))
        is_remote = row.get("is_remote", False)

        site_label = f"{site_name}"
        if is_remote and "remote" not in (location_str or "").lower():
            location_str = f"{location_str} (Remote)" if location_str else "Remote"

        is_easy = bool(row.get("is_easy_apply")) if ("is_easy_apply" in row and row.get("is_easy_apply") is not None) else False
        strategy = "easy_apply" if is_easy else "jobspy"

        # If JobSpy gave us a full description, promote it directly
        full_description = None
        detail_scraped_at = None
        if description and len(description) > 300:
            full_description = description
            detail_scraped_at = now

        # Extract apply URL if JobSpy provided it
        apply_url = str(row.get("job_url_direct", "")) if str(row.get("job_url_direct", "")) != "nan" else None

        # Extract company logo URL if JobSpy provided it
        raw_logo = str(row.get("company_logo", "")) or str(row.get("logo_photo_url", "")) or str(row.get("company_url_direct", "")) or str(row.get("logo", ""))
        company_logo = raw_logo if (raw_logo and raw_logo != "nan" and raw_logo.startswith("http")) else None

        from applypilot.discovery.liveness import check_posting_liveness
        from applypilot.database import record_scan_history

        # Record scan history & get repost count (90-day tracking)
        repost_count = record_scan_history(conn, url, company, title, search_id)

        # Liveness Gate check
        desc_to_check = full_description or description or ""
        liveness = check_posting_liveness(desc_to_check) if desc_to_check else {"is_live": True, "reason": None}
        is_expired = 1 if not liveness["is_live"] else 0
        expired_reason = liveness["reason"]

        try:
            conn.execute(
                "INSERT INTO jobs (url, title, company, salary, description, location, site, strategy, discovered_at, "
                "full_description, application_url, detail_scraped_at, search_id, search_name, company_logo, "
                "is_expired, expired_reason, repost_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (url, title, company, salary, description, location_str, site_label, strategy, now,
                 full_description, apply_url, detail_scraped_at, search_id, search_name, company_logo,
                 is_expired, expired_reason, repost_count),
            )
            new += 1
        except sqlite3.IntegrityError:
            conn.execute(
                "UPDATE jobs SET search_id = COALESCE(search_id, ?), search_name = COALESCE(search_name, ?), "
                "company_logo = COALESCE(company_logo, ?), repost_count = ?, is_expired = ?, expired_reason = ? WHERE url = ?",
                (search_id, search_name, company_logo, repost_count, is_expired, expired_reason, url)
            )
            existing += 1

    conn.commit()
    return new, existing


# -- Single search execution -------------------------------------------------

def _run_one_search(
    search: dict,
    sites: list[str],
    results_per_site: int,
    hours_old: int,
    proxy_config: dict | None,
    defaults: dict,
    max_retries: int,
    accept_locs: list[str],
    reject_locs: list[str],
    glassdoor_map: dict,
) -> dict:
    """Run a single search query and store results in DB."""
    s = search
    if not proxy_config:
        proxy_config = resolve_webshare_proxy()
    label = f"\"{s['query']}\" in {s['location']} {'(remote)' if s.get('remote') else ''}"
    if "tier" in s:
        label += f" [tier {s['tier']}]"

    cur_sites = s.get("sites") or sites
    cur_results_per_site = s.get("results_per_site") or results_per_site
    cur_hours_old = s.get("hours_old") or hours_old
    easy_apply = s.get("easy_apply_only", False)

    effective_accept = list(accept_locs)
    if s.get("location"):
        effective_accept.append(str(s["location"]))
    for loc_item in s.get("locations", []):
        effective_accept.append(str(loc_item))

    loc_clean = str(s.get("location", "")).strip().lower()
    if loc_clean in ("worldwide", "global", "anywhere", "all"):
        gd_location = "United States"
    else:
        gd_location = glassdoor_map.get(s["location"], s["location"].split(",")[0])
    valid_jobspy_sites = {"linkedin", "indeed", "glassdoor", "zip_recruiter", "google"}
    jobspy_sites = [si for si in cur_sites if si.lower() in valid_jobspy_sites]
    has_glassdoor = "glassdoor" in jobspy_sites
    other_sites = [si for si in jobspy_sites if si.lower() != "glassdoor"]

    all_dfs = []

    # Run non-Glassdoor sites with original location
    if other_sites:
        kwargs = {
            "site_name": other_sites,
            "search_term": s["query"],
            "location": s["location"],
            "results_wanted": cur_results_per_site,
            "hours_old": cur_hours_old,
            "description_format": "markdown",
            "country_indeed": defaults.get("country_indeed", "usa"),
            "country": sanitize_jobspy_country(s.get("country") or defaults.get("country")),
            "verbose": 0,
        }
        if easy_apply:
            kwargs["easy_apply"] = True
        if s.get("remote"):
            kwargs["is_remote"] = True
        if proxy_config:
            kwargs["proxies"] = proxy_config.get("pool") or [proxy_config["jobspy"]]
        if "linkedin" in other_sites:
            kwargs["linkedin_fetch_description"] = True
        try:
            df = _scrape_with_retry(kwargs, max_retries=max_retries)
            all_dfs.append(df)
        except Exception as e:
            log.error("[%s] (non-gd): %s", label, e)

    # Run Glassdoor separately with simplified location
    if has_glassdoor:
        gd_kwargs = {
            "site_name": ["glassdoor"],
            "search_term": s["query"],
            "location": gd_location,
            "results_wanted": cur_results_per_site,
            "hours_old": cur_hours_old,
            "description_format": "markdown",
            "verbose": 0,
        }
        if easy_apply:
            gd_kwargs["easy_apply"] = True
        if s.get("remote"):
            gd_kwargs["is_remote"] = True
        if proxy_config:
            gd_kwargs["proxies"] = proxy_config.get("pool") or [proxy_config["jobspy"]]
        try:
            gd_df = _scrape_with_retry(gd_kwargs, max_retries=max_retries)
            all_dfs.append(gd_df)
        except Exception as e:
            log.error("[%s] (glassdoor): %s", label, e)

    new = 0
    existing = 0
    filtered = 0
    before = 0

    if all_dfs:
        import warnings
        import pandas as pd
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            df = pd.concat(all_dfs, ignore_index=True) if len(all_dfs) > 1 else all_dfs[0]

        if len(df) > 0:
            before = len(df)
            title_mode = s.get("title_match_mode") or defaults.get("title_match_mode", "flexible")
            blocked_posters = [str(bp).strip() for bp in (s.get("blocked_posters") or []) if str(bp).strip()]
            df = df[df.apply(lambda row: (
                _location_ok(
                    str(row.get("location", "")) if str(row.get("location", "")) != "nan" else None,
                    effective_accept, reject_locs,
                ) and
                _title_ok(
                    str(row.get("title", "")) if str(row.get("title", "")) != "nan" else None,
                    s["query"],
                    title_mode,
                ) and
                _poster_ok(
                    str(row.get("company", "")) if str(row.get("company", "")) != "nan" else None,
                    blocked_posters,
                )
            ), axis=1)]
            filtered = before - len(df)

            if len(df) > 0:
                conn = get_connection()
                new_js, ext_js = store_jobspy_results(
                    conn, df, s["query"], target_location=s.get("location"),
                    search_id=s.get("search_id"), search_name=s.get("search_name")
                )
                new += new_js
                existing += ext_js

    # Fetch from LatAm & Remote API/RSS providers ONLY IF requested in search sites configuration
    try:
        from applypilot.discovery.providers import (
            detect_ats_provider,
            fetch_ats_jobs_from_url,
            fetch_getonboard_jobs,
            fetch_hirewithnear_jobs,
            fetch_remotive_jobs,
            fetch_weworkremotely_jobs,
        )
        target_sites = [st.lower() for st in (s.get("sites") or sites or [])]
        s_id = s.get("search_id")
        s_name = s.get("search_name")
        if "getonboard" in target_sites or "get_on_board" in target_sites:
            g_res = fetch_getonboard_jobs(
                s["query"],
                target_location=s.get("location"),
                search_id=s_id,
                search_name=s_name,
                allowed_locations=effective_accept,
                reject_locations=reject_locs,
            )
            new += g_res.get("new", 0)
            existing += g_res.get("existing", 0)
        if "hirewithnear" in target_sites or "hire_with_near" in target_sites or "near" in target_sites:
            h_res = fetch_hirewithnear_jobs(
                s["query"],
                target_location=s.get("location"),
                search_id=s_id,
                search_name=s_name,
                allowed_locations=effective_accept,
                reject_locations=reject_locs,
                hours_old=cur_hours_old,
            )
            new += h_res.get("new", 0)
            existing += h_res.get("existing", 0)
        if "remotive" in target_sites:
            r_res = fetch_remotive_jobs(
                s["query"],
                target_location=s.get("location"),
                search_id=s_id,
                search_name=s_name,
                allowed_locations=effective_accept,
                reject_locations=reject_locs,
            )
            new += r_res.get("new", 0)
            existing += r_res.get("existing", 0)
        if "weworkremotely" in target_sites or "wwr" in target_sites:
            w_res = fetch_weworkremotely_jobs(s["query"], target_location=s.get("location"), search_id=s_id, search_name=s_name)
            new += w_res.get("new", 0)
            existing += w_res.get("existing", 0)
        # Check direct ATS connectors (Greenhouse, Ashby, Lever)
        for ts in target_sites:
            ats_info = detect_ats_provider(ts)
            if ats_info:
                p_res = fetch_ats_jobs_from_url(ts, target_location=s.get("location"), search_id=s_id, search_name=s_name)
                new += p_res.get("new", 0)
                existing += p_res.get("existing", 0)
        # Also check if query itself is an ATS URL or token pattern (e.g. greenhouse:stripe)
        q_ats = detect_ats_provider(s["query"])
        if q_ats:
            p_res = fetch_ats_jobs_from_url(s["query"], target_location=s.get("location"), search_id=s_id, search_name=s_name)
            new += p_res.get("new", 0)
            existing += p_res.get("existing", 0)
    except Exception as api_err:
        log.warning("[%s] LatAm/Remote/ATS API provider warning: %s", label, api_err)

    # Automatic recency fallback: if 0 new/existing jobs found and hours_old < 48, retry with capped recency of 48 hours max
    if (new == 0 and existing == 0) and cur_hours_old < 48:
        next_hours = 48
        log.info("[%s] 0 jobs found with hours_old=%d. Retrying with automatic recency fallback (hours_old=%d max)...", label, cur_hours_old, next_hours)
        s_fallback = dict(s)
        s_fallback["hours_old"] = next_hours
        return _run_one_search(
            s_fallback, sites, results_per_site, next_hours,
            proxy_config, defaults, max_retries,
            accept_locs, reject_locs, glassdoor_map,
        )

    msg = f"[{label}] {before} results -> {new} new, {existing} dupes"
    if filtered:
        msg += f", {filtered} filtered (location)"
    log.info(msg)

    return {"new": new, "existing": existing, "errors": 0, "filtered": filtered, "total": before, "label": label}


# -- Single query search -----------------------------------------------------

def search_jobs(
    query: str,
    location: str,
    sites: list[str] | None = None,
    remote_only: bool = False,
    results_per_site: int = 50,
    hours_old: int = 72,
    proxy: str | None = None,
    country_indeed: str = "usa",
) -> dict:
    """Run a single job search via JobSpy and store results in DB."""
    if sites is None:
        sites = ["indeed", "linkedin", "zip_recruiter"]

    proxy_config = resolve_webshare_proxy(proxy_override=proxy)

    log.info("Search: \"%s\" in %s | sites=%s | remote=%s", query, location, sites, remote_only)

    kwargs = {
        "site_name": sites,
        "search_term": query,
        "location": location,
        "results_wanted": results_per_site,
        "hours_old": hours_old,
        "description_format": "markdown",
        "country_indeed": country_indeed,
        "verbose": 2,
    }

    if remote_only:
        kwargs["is_remote"] = True

    if proxy_config:
        kwargs["proxies"] = [proxy_config["jobspy"]]

    if "linkedin" in sites:
        kwargs["linkedin_fetch_description"] = True

    try:
        df = scrape_jobs(**kwargs)
    except Exception as e:
        log.error("JobSpy search failed: %s", e)
        return {"error": str(e), "total": 0, "new": 0, "existing": 0}

    total = len(df)
    log.info("JobSpy returned %d results", total)

    if total == 0:
        return {"total": 0, "new": 0, "existing": 0}

    df = df[df.apply(lambda row: _title_ok(
        str(row.get("title", "")) if str(row.get("title", "")) != "nan" else None,
        query,
        "flexible",
    ), axis=1)]

    if "site" in df.columns:
        site_counts = df["site"].value_counts()
        for site, count in site_counts.items():
            log.info("  %s: %d", site, count)

    conn = init_db()
    new, existing = store_jobspy_results(conn, df, query)
    log.info("Stored: %d new, %d already in DB", new, existing)

    db_total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM jobs WHERE detail_scraped_at IS NULL").fetchone()[0]
    log.info("DB total: %d jobs, %d pending detail scrape", db_total, pending)

    return {"total": total, "new": new, "existing": existing}


# -- Full crawl (all queries x all locations) --------------------------------

def _full_crawl(
    search_cfg: dict,
    tiers: list[int] | None = None,
    locations: list[str] | None = None,
    sites: list[str] | None = None,
    results_per_site: int = 100,
    hours_old: int = 72,
    proxy: str | None = None,
    max_retries: int = 2,
) -> dict:
    """Run all search queries from search config across all locations."""
    if sites is None:
        sites = ["indeed", "linkedin", "zip_recruiter"]

    # Build search combinations from config
    queries = search_cfg.get("queries", [])
    locs = search_cfg.get("locations", [])
    defaults = search_cfg.get("defaults", {})
    defaults["country"] = search_cfg.get("country", "worldwide")
    glassdoor_map = search_cfg.get("glassdoor_location_map", {})
    accept_locs, reject_locs = _load_location_config(search_cfg)

    search_profiles = search_cfg.get("searches", [])
    searches = []

    if search_profiles and isinstance(search_profiles, list):
        for sp in search_profiles:
            if not sp.get("enabled", True):
                continue
            sp_queries = sp.get("queries", [])
            sp_locs = list(sp.get("locations", []))

            sp_id = sp.get("id", "search_1")
            sp_name = sp.get("name", "Search")
            for q_item in sp_queries:
                q_text = q_item["query"] if isinstance(q_item, dict) else str(q_item)
                for loc_item in sp_locs:
                    l_text = loc_item["location"] if isinstance(loc_item, dict) else str(loc_item)
                    sp_is_remote = sp.get("is_remote")
                    l_remote = loc_item.get("remote", False) if isinstance(loc_item, dict) else (sp_is_remote if sp_is_remote is not None else False)
                    sp_sites = sp.get("sites") or sites
                    sp_results_per_site = sp.get("results_per_site") or sp.get("results_wanted") or results_per_site
                    sp_hours_old = sp.get("hours_old") or hours_old
                    sp_easy_apply = sp.get("easy_apply_only", False)
                    sp_blocked_posters = sp.get("blocked_posters") or []
                    searches.append({
                        "query": q_text,
                        "location": l_text,
                        "locations": [loc["location"] if isinstance(loc, dict) else str(loc) for loc in sp_locs],
                        "remote": l_remote,
                        "search_id": sp_id,
                        "search_name": sp_name,
                        "sites": sp_sites,
                        "results_per_site": sp_results_per_site,
                        "hours_old": sp_hours_old,
                        "easy_apply_only": sp_easy_apply,
                        "blocked_posters": sp_blocked_posters,
                    })
    else:
        queries = search_cfg.get("queries", [])
        locs = search_cfg.get("locations", [])

        if tiers:
            queries = [q for q in queries if q.get("tier") in tiers]
        if locations:
            locs = [loc for loc in locs if loc.get("label") in locations]

        for q in queries:
            for loc in locs:
                searches.append({
                    "query": q["query"],
                    "location": loc["location"],
                    "remote": loc.get("remote", False),
                    "tier": q.get("tier", 0),
                    "search_id": "search_1",
                    "search_name": "Default Search Profile",
                })

    proxy_config = parse_proxy(proxy) if proxy else None

    log.info("Full crawl: %d search combinations", len(searches))
    log.info("Sites: %s | Results/site: %d | Hours old: %d",
             ", ".join(sites), results_per_site, hours_old)

    # Ensure DB schema is ready
    init_db()

    total_new = 0
    total_existing = 0
    total_errors = 0
    completed = 0

    for s in searches:
        result = _run_one_search(
            s, sites, results_per_site, hours_old,
            proxy_config, defaults, max_retries,
            accept_locs, reject_locs, glassdoor_map,
        )
        completed += 1
        total_new += result["new"]
        total_existing += result["existing"]
        total_errors += result["errors"]

        if completed % 5 == 0 or completed == len(searches):
            log.info("Progress: %d/%d queries done (%d new, %d dupes, %d errors)",
                     completed, len(searches), total_new, total_existing, total_errors)

    # Final stats
    conn = get_connection()
    db_total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    log.info("Full crawl complete: %d new | %d dupes | %d errors | %d total in DB",
             total_new, total_existing, total_errors, db_total)

    return {
        "new": total_new,
        "existing": total_existing,
        "errors": total_errors,
        "db_total": db_total,
        "queries": len(searches),
    }


# -- Public entry point ------------------------------------------------------

def run_discovery(cfg: dict | None = None) -> dict:
    """Main entry point for JobSpy-based job discovery.

    Loads search queries and locations from the user's search config YAML,
    then runs a full crawl across all configured job boards.

    Args:
        cfg: Override the search configuration dict. If None, loads from
             the user's searches.yaml file.

    Returns:
        Dict with stats: new, existing, errors, db_total, queries.
    """
    if cfg is None:
        cfg = config.load_search_config()

    if not cfg:
        log.warning("No search configuration found. Run `applypilot init` to create one.")
        return {"new": 0, "existing": 0, "errors": 0, "db_total": 0, "queries": 0}

    import os
    from applypilot.server import load_admin_config
    proxy = cfg.get("proxy") or os.environ.get("APPLYPILOT_PROXY") or os.environ.get("JOBSPY_PROXY") or os.environ.get("PROXY_URL") or load_admin_config().get("proxy")
    sites = cfg.get("sites")
    results_per_site = cfg.get("defaults", {}).get("results_per_site", 100)
    hours_old = cfg.get("defaults", {}).get("hours_old", 72)
    tiers = cfg.get("tiers")
    locations = cfg.get("location_labels")

    return _full_crawl(
        search_cfg=cfg,
        tiers=tiers,
        locations=locations,
        sites=sites,
        results_per_site=results_per_site,
        hours_old=hours_old,
        proxy=proxy,
    )
