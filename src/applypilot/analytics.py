"""DuckDuckWork Analytics Engine.

High-performance, read-only analytics aggregation functions querying the
DuckDuckWork SQLite database (`jobs` table).
"""

from __future__ import annotations

import logging
import sqlite3
import unicodedata
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Geographic coordinate references: [latitude, longitude]
# Covers Guatemala, Nicaragua, Latin America, North America, and Global.
LOCATION_COORDINATES_MAP: list[tuple[list[str], list[float]]] = [
    # Guatemalan Municipalities, Zones, and Departments
    (["san miguel petapa"], [14.5019, -90.5594]),
    (["villa nueva"], [14.5269, -90.5875]),
    (["villa canales"], [14.4842, -90.5333]),
    (["mixco"], [14.6310, -90.6064]),
    (["amatitlan"], [14.4842, -90.6156]),
    (["antigua", "sacatepequez"], [14.5586, -90.7295]),
    (["san juan sacatepequez"], [14.7189, -90.6442]),
    (["santa catarina pinula"], [14.5683, -90.4950]),
    (["san jose pinula"], [14.5458, -90.4147]),
    (["quetzaltenango", "xela"], [14.8347, -91.5181]),
    (["puerto san jose", "escuintla"], [14.3050, -90.7850]),
    (["coban", "alta verapaz"], [15.4708, -90.3708]),
    (["chimaltenango"], [14.6611, -90.8208]),
    (["zacapa"], [14.9722, -89.5306]),
    (["chiquimula"], [14.7981, -89.5458]),
    (["nueva esperanza"], [14.6400, -90.5100]),
    (
        [
            "guatemala city",
            "ciudad de guatemala",
            "zona diez",
            "zona 10",
            "zona quince",
            "zona 15",
            "zona cuatro",
            "zona 4",
            "zona trece",
            "zona 13",
            "zona doce",
            "zona 12",
            "zona siete",
            "zona 7",
        ],
        [14.6349, -90.5069],
    ),
    (["guatemala"], [14.6349, -90.5069]),
    # Nicaragua Municipalities and Departments
    (["managua"], [12.1149, -86.2362]),
    (["masaya", "nindiri"], [11.9744, -86.0942]),
    (["granada"], [11.9299, -85.9560]),
    (["leon"], [12.4379, -86.8780]),
    (["carazo", "jinotepe", "diriamba"], [11.8394, -86.2081]),
    (["matagalpa"], [12.9256, -85.9175]),
    (["esteli"], [13.0919, -86.3538]),
    (["chinandega"], [12.6294, -87.1311]),
    (["rivas", "san juan del sur"], [11.4372, -85.8263]),
    (["nicaragua"], [12.8654, -85.2072]),
    # Central America
    (["costa rica", "san jose"], [9.9281, -84.0907]),
    (["panama"], [8.9824, -79.5199]),
    (["honduras", "tegucigalpa", "san pedro sula"], [14.0723, -87.1921]),
    (["el salvador", "san salvador"], [13.6929, -89.2182]),
    # South America
    (["medellin"], [6.2442, -75.5812]),
    (["bogota", "colombia"], [4.7110, -74.0721]),
    (["mexico", "monterrey", "guadalajara"], [19.4326, -99.1332]),
    (["sao paulo", "rio de janeiro", "brasil", "brazil"], [-23.5505, -46.6333]),
    (["chile", "santiago"], [-33.4489, -70.6693]),
    (["peru", "lima"], [-12.0464, -77.0428]),
    (["buenos aires", "argentina"], [-34.6037, -58.3816]),
    (["montevideo", "uruguay"], [-34.9011, -56.1645]),
    (["quito", "ecuador"], [-0.1807, -78.4678]),
    (["bolivia", "la paz"], [-16.5000, -68.1193]),
    # Regional LATAM
    (["latam", "latin america", "americas"], [9.7489, -83.7534]),
    # North America & Europe
    (["san francisco", "bay area", "silicon valley"], [37.7749, -122.4194]),
    (["new york", "nyc", "manhattan", "brooklyn"], [40.7128, -74.0060]),
    (["austin"], [30.2672, -97.7431]),
    (["seattle"], [47.6062, -122.3321]),
    (["los angeles"], [34.0522, -118.2437]),
    (["chicago"], [41.8781, -87.6298]),
    (["boston"], [42.3601, -71.0589]),
    (["denver"], [39.7392, -104.9903]),
    (["atlanta"], [33.7490, -84.3880]),
    (["toronto"], [43.6532, -79.3832]),
    (["vancouver"], [49.2827, -123.1207]),
    (["usa", "united states", "us remote", "northern america"], [37.0902, -95.7129]),
    (["canada"], [56.1304, -106.3468]),
    (["europe", "uk", "england", "switzerland", "france", "germany", "spain"], [50.1109, 8.6821]),
    # Worldwide & Global Remote default
    (["worldwide", "remote", "global", "anywhere"], [20.0, -10.0]),
]


def normalize_location_text(text: str | None) -> str:
    """Normalize location text by stripping accents, symbols, and lowercase."""
    if not text:
        return ""
    norm = unicodedata.normalize("NFD", str(text).lower())
    return "".join(c for c in norm if unicodedata.category(c) != "Mn")


def map_location_to_coordinates(location: str | None) -> list[float]:
    """Map a location string to geographic latitude and longitude coordinates."""
    if not location or not str(location).strip():
        return [20.0, -10.0]

    norm = normalize_location_text(location)
    for patterns, coords in LOCATION_COORDINATES_MAP:
        for p in patterns:
            if p in norm:
                return [coords[0], coords[1]]

    return [20.0, -10.0]


def compute_tab_recommendation(
    total_jobs: int,
    avg_score: float,
    saved_rate: float,
    applied_rate: float,
) -> str:
    """Calculate an algorithmic recommendation for a search profile/tab.

    Returns one of:
    - 'Keep Searching': Healthy query with positive candidate interaction or good fit.
    - 'Refine Query': Low scores or uncalibrated results requiring query adjustment.
    - 'Discard / Pause': Substantial lead volume with zero interest/action.
    """
    if total_jobs == 0:
        return "Keep Searching"

    # Substantial volume scraped with zero saved or applied actions
    if total_jobs >= 30 and (saved_rate + applied_rate) == 0.0:
        return "Discard / Pause"

    # Significant jobs scraped but very weak scores and low interaction
    if total_jobs >= 15 and avg_score < 30.0 and (saved_rate + applied_rate) < 5.0:
        return "Refine Query"

    # Strong candidate fit or solid conversion
    if avg_score >= 50.0 or (saved_rate + applied_rate) >= 8.0:
        return "Keep Searching"

    # Moderate score with at least some candidate action
    if avg_score >= 40.0 and (saved_rate > 0 or applied_rate > 0):
        return "Keep Searching"

    if total_jobs < 10:
        return "Keep Searching"

    return "Refine Query"


def get_analytics_summary(db_path: Path | str, search_id: str | None = None) -> dict[str, Any]:
    """Query DuckDuckWork database and return aggregated analytics summary.

    Args:
        db_path: Path to the SQLite applypilot.db file.
        search_id: Optional search_id filter (e.g. 'search_1'). If None or 'all',
                   aggregates across all jobs in the database.

    Returns:
        Dictionary containing:
        - summary: total_found, saved, applied, archived, pending
        - top_sites: list of top 10 portals with count and percentage
        - top_companies: list of top 10 companies with count and percentage
        - top_titles: list of top 10 job titles with count and percentage
        - score_distribution: avg_score, score_buckets (90+, 80-89, 70-79, <70, unscored)
        - geolocations: aggregated locations with coordinates, counts, and status breakdown
        - tabs_health: per-search metrics for all searches with recommendation
    """
    path_obj = Path(db_path)
    empty_result: dict[str, Any] = {
        "summary": {
            "total_found": 0,
            "saved": 0,
            "applied": 0,
            "archived": 0,
            "pending": 0,
        },
        "total_found": 0,
        "saved": 0,
        "applied": 0,
        "archived": 0,
        "pending": 0,
        "top_sites": [],
        "sites": [],
        "top_companies": [],
        "companies": [],
        "top_titles": [],
        "titles": [],
        "score_distribution": {
            "avg_score": 0.0,
            "score_buckets": {
                "90+": 0,
                "80-89": 0,
                "70-79": 0,
                "<70": 0,
                "unscored": 0,
            },
        },
        "geolocations": [],
        "tabs_health": [],
    }

    if not path_obj.exists():
        return empty_result

    # Standardize search_id filter
    active_search_id: str | None = None
    if search_id and str(search_id).strip() and str(search_id).strip().lower() != "all":
        active_search_id = str(search_id).strip()

    try:
        # Open in strictly read-only mode to prevent any database modification
        conn = sqlite3.connect(f"file:{path_obj.resolve()}?mode=ro", uri=True, timeout=10)
    except Exception:
        try:
            conn = sqlite3.connect(str(path_obj), timeout=10)
        except Exception as conn_err:
            logger.warning("Failed to open SQLite database in analytics: %s", conn_err)
            return empty_result

    conn.row_factory = sqlite3.Row

    try:
        # Verify table existence
        tbl_check = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'"
        ).fetchone()
        if not tbl_check:
            conn.close()
            return empty_result

        where_clause = ""
        params: list[Any] = []
        if active_search_id is not None:
            where_clause = "WHERE search_id = ?"
            params.append(active_search_id)

        # 1. Summary Counts
        sum_query = f"""
            SELECT
                COUNT(*) as total_found,
                SUM(CASE WHEN is_saved = 1 THEN 1 ELSE 0 END) as saved,
                SUM(CASE WHEN (apply_status = 'applied' OR applied_at IS NOT NULL) THEN 1 ELSE 0 END) as applied,
                SUM(CASE WHEN (is_archived = 1 OR apply_status = 'archived') THEN 1 ELSE 0 END) as archived,
                SUM(CASE WHEN (COALESCE(is_saved, 0) = 0)
                          AND (COALESCE(is_archived, 0) = 0)
                          AND (apply_status IS NULL OR apply_status NOT IN ('applied', 'archived'))
                          AND applied_at IS NULL THEN 1 ELSE 0 END) as pending
            FROM jobs
            {where_clause}
        """
        sum_row = conn.execute(sum_query, params).fetchone()
        total_found = int(sum_row["total_found"] or 0) if sum_row else 0
        saved_count = int(sum_row["saved"] or 0) if sum_row else 0
        applied_count = int(sum_row["applied"] or 0) if sum_row else 0
        archived_count = int(sum_row["archived"] or 0) if sum_row else 0
        pending_count = int(sum_row["pending"] or 0) if sum_row else 0

        summary_dict = {
            "total_found": total_found,
            "saved": saved_count,
            "applied": applied_count,
            "archived": archived_count,
            "pending": pending_count,
        }

        # 2. Top 10 Sites
        site_query = f"""
            SELECT
                COALESCE(NULLIF(TRIM(site), ''), 'Other') as name,
                COUNT(*) as count
            FROM jobs
            {where_clause}
            GROUP BY name
            ORDER BY count DESC
            LIMIT 10
        """
        site_rows = conn.execute(site_query, params).fetchall()
        top_sites = [
            {
                "name": str(r["name"]),
                "count": int(r["count"]),
                "percentage": round((int(r["count"]) / total_found) * 100.0, 1) if total_found > 0 else 0.0,
            }
            for r in site_rows
        ]

        # 3. Top 10 Companies
        comp_query = f"""
            SELECT
                COALESCE(NULLIF(TRIM(company), ''), 'Direct Employer') as name,
                COUNT(*) as count
            FROM jobs
            {where_clause}
            GROUP BY name
            ORDER BY count DESC
            LIMIT 10
        """
        comp_rows = conn.execute(comp_query, params).fetchall()
        top_companies = [
            {
                "name": str(r["name"]),
                "count": int(r["count"]),
                "percentage": round((int(r["count"]) / total_found) * 100.0, 1) if total_found > 0 else 0.0,
            }
            for r in comp_rows
        ]

        # 4. Top 10 Job Titles
        title_query = f"""
            SELECT
                COALESCE(NULLIF(TRIM(title), ''), 'Untitled') as name,
                COUNT(*) as count
            FROM jobs
            {where_clause}
            GROUP BY name
            ORDER BY count DESC
            LIMIT 10
        """
        title_rows = conn.execute(title_query, params).fetchall()
        top_titles = [
            {
                "name": str(r["name"]),
                "count": int(r["count"]),
                "percentage": round((int(r["count"]) / total_found) * 100.0, 1) if total_found > 0 else 0.0,
            }
            for r in title_rows
        ]

        # 5. Score Distribution
        score_query = f"SELECT fit_score FROM jobs {where_clause}"
        score_rows = conn.execute(score_query, params).fetchall()

        scores_list: list[float] = []
        score_buckets = {
            "90+": 0,
            "80-89": 0,
            "70-79": 0,
            "<70": 0,
            "unscored": 0,
        }

        for r in score_rows:
            sc = r["fit_score"]
            if sc is None or sc < 0:
                score_buckets["unscored"] += 1
            else:
                # Normalize 0-10 integer score scale to 0-100 percentage scale
                norm_sc = float(sc * 10.0 if sc <= 10 else sc)
                scores_list.append(norm_sc)
                if norm_sc >= 90.0:
                    score_buckets["90+"] += 1
                elif norm_sc >= 80.0:
                    score_buckets["80-89"] += 1
                elif norm_sc >= 70.0:
                    score_buckets["70-79"] += 1
                else:
                    score_buckets["<70"] += 1

        avg_score = round(sum(scores_list) / len(scores_list), 1) if scores_list else 0.0
        score_distribution = {
            "avg_score": avg_score,
            "score_buckets": score_buckets,
        }

        # 6. Geolocations
        geo_query = f"""
            SELECT
                COALESCE(NULLIF(TRIM(location), ''), 'Worldwide (Remote)') as location,
                COUNT(*) as count,
                SUM(CASE WHEN is_saved = 1 THEN 1 ELSE 0 END) as saved,
                SUM(CASE WHEN (apply_status = 'applied' OR applied_at IS NOT NULL) THEN 1 ELSE 0 END) as applied,
                SUM(CASE WHEN (is_archived = 1 OR apply_status = 'archived') THEN 1 ELSE 0 END) as archived,
                SUM(CASE WHEN (COALESCE(is_saved, 0) = 0)
                          AND (COALESCE(is_archived, 0) = 0)
                          AND (apply_status IS NULL OR apply_status NOT IN ('applied', 'archived'))
                          AND applied_at IS NULL THEN 1 ELSE 0 END) as pending
            FROM jobs
            {where_clause}
            GROUP BY location
            ORDER BY count DESC
        """
        geo_rows = conn.execute(geo_query, params).fetchall()
        geolocations: list[dict[str, Any]] = []

        for gr in geo_rows:
            loc_str = str(gr["location"])
            loc_count = int(gr["count"] or 0)
            loc_saved = int(gr["saved"] or 0)
            loc_applied = int(gr["applied"] or 0)
            loc_archived = int(gr["archived"] or 0)
            loc_pending = int(gr["pending"] or 0)
            coords = map_location_to_coordinates(loc_str)

            geolocations.append({
                "location": loc_str,
                "coordinates": coords,
                "count": loc_count,
                "total": loc_count,
                "saved": loc_saved,
                "applied": loc_applied,
                "archived": loc_archived,
                "pending": loc_pending,
                "status_breakdown": {
                    "saved": loc_saved,
                    "applied": loc_applied,
                    "archived": loc_archived,
                    "pending": loc_pending,
                },
            })

        # 7. Tabs Health (Cross-search comparison)
        tabs_health = _calculate_tabs_health(conn, path_obj)

        conn.close()

        return {
            "summary": summary_dict,
            "total_found": total_found,
            "saved": saved_count,
            "applied": applied_count,
            "archived": archived_count,
            "pending": pending_count,
            "top_sites": top_sites,
            "sites": top_sites,
            "top_companies": top_companies,
            "companies": top_companies,
            "top_titles": top_titles,
            "titles": top_titles,
            "score_distribution": score_distribution,
            "geolocations": geolocations,
            "tabs_health": tabs_health,
        }

    except Exception as query_err:
        logger.warning("Error computing analytics summary: %s", query_err)
        try:
            conn.close()
        except Exception:
            pass
        return empty_result


def _calculate_tabs_health(conn: sqlite3.Connection, db_path: Path) -> list[dict[str, Any]]:
    """Compute performance and recommendation health metrics for all search tabs."""
    # Attempt to load search profiles from searches.yaml
    searches_file = db_path.parent / "searches.yaml"
    profiles_from_yaml: list[dict[str, Any]] = []

    if searches_file.exists():
        try:
            import yaml

            with open(searches_file, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            searches_raw = cfg.get("searches", [])
            if isinstance(searches_raw, list):
                profiles_from_yaml = searches_raw
        except Exception as yaml_err:
            logger.debug("Could not parse searches.yaml for tabs health: %s", yaml_err)

    if not profiles_from_yaml:
        try:
            from applypilot.config import get_search_profiles

            profiles_from_yaml = get_search_profiles()
        except Exception:
            profiles_from_yaml = []

    # Aggregate metrics from SQLite jobs table grouped by search_id
    metrics_query = """
        SELECT
            search_id,
            COALESCE(search_name, '') as db_search_name,
            COUNT(*) as total_jobs,
            SUM(CASE WHEN is_saved = 1 THEN 1 ELSE 0 END) as saved,
            SUM(CASE WHEN (apply_status = 'applied' OR applied_at IS NOT NULL) THEN 1 ELSE 0 END) as applied,
            AVG(CASE WHEN fit_score IS NOT NULL AND fit_score >= 0 THEN
                CASE WHEN fit_score <= 10 THEN fit_score * 10.0 ELSE fit_score * 1.0 END
            ELSE NULL END) as avg_score
        FROM jobs
        GROUP BY search_id
    """
    db_metrics_rows = conn.execute(metrics_query).fetchall()
    db_metrics_map = {str(r["search_id"]): dict(r) for r in db_metrics_rows if r["search_id"]}

    tabs_result: list[dict[str, Any]] = []
    processed_ids: set[str] = set()

    # First add all profiles defined in configuration
    for profile in profiles_from_yaml:
        sid = str(profile.get("id") or "").strip()
        if not sid:
            continue
        processed_ids.add(sid)
        m = db_metrics_map.get(sid, {})
        total = int(m.get("total_jobs", 0))
        saved = int(m.get("saved", 0))
        applied = int(m.get("applied", 0))
        avg_sc = round(float(m.get("avg_score") or 0.0), 1)
        saved_rate = round((saved / total) * 100.0, 1) if total > 0 else 0.0
        applied_rate = round((applied / total) * 100.0, 1) if total > 0 else 0.0
        rec = compute_tab_recommendation(total, avg_sc, saved_rate, applied_rate)

        name = str(profile.get("name") or m.get("db_search_name") or sid)
        tabs_result.append({
            "search_id": sid,
            "search_name": name,
            "total_jobs": total,
            "avg_score": avg_sc,
            "saved_rate": saved_rate,
            "applied_rate": applied_rate,
            "recommendation": rec,
        })

    # Add any search_ids found in database that were not in searches.yaml
    for sid, m in db_metrics_map.items():
        if sid in processed_ids:
            continue
        processed_ids.add(sid)
        total = int(m.get("total_jobs", 0))
        saved = int(m.get("saved", 0))
        applied = int(m.get("applied", 0))
        avg_sc = round(float(m.get("avg_score") or 0.0), 1)
        saved_rate = round((saved / total) * 100.0, 1) if total > 0 else 0.0
        applied_rate = round((applied / total) * 100.0, 1) if total > 0 else 0.0
        rec = compute_tab_recommendation(total, avg_sc, saved_rate, applied_rate)
        name = str(m.get("db_search_name") or sid)

        tabs_result.append({
            "search_id": sid,
            "search_name": name,
            "total_jobs": total,
            "avg_score": avg_sc,
            "saved_rate": saved_rate,
            "applied_rate": applied_rate,
            "recommendation": rec,
        })

    return tabs_result
