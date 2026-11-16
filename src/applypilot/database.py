"""DuckDuckWork database layer: schema, migrations, stats, and connection helpers.

Single source of truth for the jobs table schema. All columns from every
pipeline stage are created up front so any stage can run independently
without migration ordering issues.
"""

import base64
import hashlib
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
try:
    from datetime import UTC
except ImportError:
    UTC = timezone.utc
from pathlib import Path

from applypilot.config import DB_PATH

log = logging.getLogger(__name__)

# Thread-local connection storage — each thread gets its own connection
# (required for SQLite thread safety with parallel workers)
_local = threading.local()


def encrypt_sensitive(plain_text: str | None, key: str | None = None) -> str | None:
    """Obfuscate/Encrypt sensitive field values before storing in DB (Item E)."""
    if plain_text is None or str(plain_text) == "":
        return plain_text
    val_str = str(plain_text)
    if val_str.startswith("enc:v1:"):
        return val_str
    if not key:
        key = os.environ.get("ADMIN_AUTH_TOKEN", "ddw_secret_key_2026")
    key_bytes = hashlib.sha256(key.encode("utf-8")).digest()
    data_bytes = val_str.encode("utf-8")
    cipher_bytes = bytes(b ^ key_bytes[i % len(key_bytes)] for i, b in enumerate(data_bytes))
    return "enc:v1:" + base64.b64encode(cipher_bytes).decode("utf-8")


def decrypt_sensitive(cipher_text: str | None, key: str | None = None) -> str | None:
    """Decrypt obfuscated sensitive field values when retrieving from DB (Item E)."""
    if cipher_text is None or not str(cipher_text).startswith("enc:v1:"):
        return cipher_text
    if not key:
        key = os.environ.get("ADMIN_AUTH_TOKEN", "ddw_secret_key_2026")
    key_bytes = hashlib.sha256(key.encode("utf-8")).digest()
    raw_b64 = str(cipher_text).removeprefix("enc:v1:")
    try:
        cipher_bytes = base64.b64decode(raw_b64)
        plain_bytes = bytes(b ^ key_bytes[i % len(key_bytes)] for i, b in enumerate(cipher_bytes))
        return plain_bytes.decode("utf-8")
    except Exception:
        return cipher_text


def get_db_path() -> Path:
    """Get DB path dynamically respecting APPLYPILOT_DIR env var."""
    app_dir = os.environ.get("APPLYPILOT_DIR")
    if app_dir:
        return Path(app_dir) / "applypilot.db"
    return DB_PATH


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Get a thread-local cached SQLite connection with WAL mode enabled."""
    path = str(db_path or get_db_path())

    if not hasattr(_local, 'connections'):
        _local.connections = {}

    conn = _local.connections.get(path)
    if conn is not None:
        try:
            conn.execute("SELECT 1")
            return conn
        except sqlite3.ProgrammingError:
            pass

    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.row_factory = sqlite3.Row
    _local.connections[path] = conn
    return conn


def close_connection(db_path: Path | str | None = None) -> None:
    """Close the cached connection for the current thread."""
    path = str(db_path or get_db_path())
    if hasattr(_local, 'connections'):
        conn = _local.connections.pop(path, None)
        if conn is not None:
            conn.close()


def init_db(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = db_path or get_db_path()

    # Ensure parent directory exists
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    conn = get_connection(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            -- Discovery stage (smart_extract / job_search)
            url                   TEXT PRIMARY KEY,
            title                 TEXT,
            salary                TEXT,
            description           TEXT,
            location              TEXT,
            site                  TEXT,
            strategy              TEXT,
            discovered_at         TEXT,

            -- Enrichment stage (detail_scraper)
            full_description      TEXT,
            application_url       TEXT,
            detail_scraped_at     TEXT,
            detail_error          TEXT,

            -- Scoring stage (job_scorer)
            fit_score             INTEGER,
            score_reasoning       TEXT,
            scored_at             TEXT,

            -- Tailoring stage (resume tailor)
            tailored_resume_path  TEXT,
            tailored_at           TEXT,
            tailor_attempts       INTEGER DEFAULT 0,

            -- Cover letter stage
            cover_letter_path     TEXT,
            cover_letter_at       TEXT,
            cover_attempts        INTEGER DEFAULT 0,

            -- Application stage
            applied_at            TEXT,
            apply_status          TEXT,
            apply_error           TEXT,
            apply_attempts        INTEGER DEFAULT 0,
            agent_id              TEXT,
            last_attempted_at     TEXT,
            apply_duration_ms     INTEGER,
            apply_task_id         TEXT,
            verification_confidence TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS scan_history (
            url TEXT PRIMARY KEY,
            company TEXT,
            title TEXT,
            title_normalized TEXT,
            first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            search_id TEXT
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_scan_history_comp_norm ON scan_history(company, title_normalized)
    """)
    conn.commit()

    # Run migrations for any columns added after initial schema
    ensure_columns(conn)

    return conn


# Complete column registry: column_name -> SQL type with optional default.
# This is the single source of truth. Adding a column here is all that's needed
# for it to appear in both new databases and migrated ones.
_ALL_COLUMNS: dict[str, str] = {
    # Discovery
    "url": "TEXT PRIMARY KEY",
    "title": "TEXT",
    "company": "TEXT",
    "salary": "TEXT",
    "description": "TEXT",
    "location": "TEXT",
    "site": "TEXT",
    "strategy": "TEXT",
    "discovered_at": "TEXT",
    "posted_date": "TEXT",
    "first_seen_at": "TEXT",
    # Liveness & Repost Tracking
    "is_expired": "INTEGER DEFAULT 0",
    "expired_reason": "TEXT",
    "repost_count": "INTEGER DEFAULT 1",
    # Enrichment
    "full_description": "TEXT",
    "application_url": "TEXT",
    "detail_scraped_at": "TEXT",
    "detail_error": "TEXT",
    # Scoring
    "fit_score": "INTEGER",
    "score_reasoning": "TEXT",
    "ai_summary": "TEXT",
    "scored_at": "TEXT",
    # Tailoring
    "tailored_resume_path": "TEXT",
    "tailored_at": "TEXT",
    "tailor_attempts": "INTEGER DEFAULT 0",
    # Cover letter
    "cover_letter_path": "TEXT",
    "cover_letter_at": "TEXT",
    "cover_attempts": "INTEGER DEFAULT 0",
    # Application
    "applied_at": "TEXT",
    "apply_status": "TEXT",
    "is_saved": "INTEGER DEFAULT 0",
    "is_archived": "INTEGER DEFAULT 0",
    "archived_at": "TEXT",
    "country": "TEXT",
    "search_id": "TEXT",
    "search_name": "TEXT",
    "apply_error": "TEXT",
    "apply_attempts": "INTEGER DEFAULT 0",
    "agent_id": "TEXT",
    "last_attempted_at": "TEXT",
    "apply_duration_ms": "INTEGER",
    "apply_task_id": "TEXT",
    "verification_confidence": "TEXT",
    "verification_status": "TEXT",
    "verification_report": "TEXT",
    "company_logo": "TEXT",
    "notes": "TEXT",
    "sub_status": "TEXT",
}


def ensure_columns(conn: sqlite3.Connection | None = None) -> list[str]:
    """Add any missing columns to the jobs table (forward migration).

    Reads the current table schema via PRAGMA table_info and compares against
    the full column registry. Any missing columns are added with ALTER TABLE.

    This makes it safe to upgrade the database from any previous version --
    columns are only added, never removed or renamed.

    Args:
        conn: Database connection. Uses get_connection() if None.

    Returns:
        List of column names that were added (empty if schema was already current).
    """
    if conn is None:
        conn = get_connection()

    existing = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    added = []

    for col, dtype in _ALL_COLUMNS.items():
        if col not in existing:
            # PRIMARY KEY columns can't be added via ALTER TABLE, but url
            # is always created with the table itself so this is safe
            if "PRIMARY KEY" in dtype:
                continue
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {dtype}")
            added.append(col)

    if added:
        conn.commit()

    return added


def get_stats(conn: sqlite3.Connection | None = None) -> dict:
    """Return job counts by pipeline stage.

    Provides a snapshot of how many jobs are at each stage, useful for
    dashboard display and pipeline progress tracking.

    Args:
        conn: Database connection. Uses get_connection() if None.

    Returns:
        Dictionary with keys:
            total, by_site, pending_detail, with_description,
            scored, unscored, tailored, untailored_eligible,
            with_cover_letter, applied, score_distribution
    """
    if conn is None:
        conn = get_connection()

    stats: dict = {}

    # Total jobs
    stats["total"] = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    # By site breakdown
    rows = conn.execute(
        "SELECT site, COUNT(*) as cnt FROM jobs GROUP BY site ORDER BY cnt DESC"
    ).fetchall()
    stats["by_site"] = [(row[0], row[1]) for row in rows]

    # Enrichment stage
    stats["pending_detail"] = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE detail_scraped_at IS NULL"
    ).fetchone()[0]

    stats["with_description"] = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE full_description IS NOT NULL"
    ).fetchone()[0]

    stats["detail_errors"] = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE detail_error IS NOT NULL"
    ).fetchone()[0]

    # Scoring stage
    stats["scored"] = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE fit_score IS NOT NULL"
    ).fetchone()[0]

    stats["unscored"] = conn.execute(
        "SELECT COUNT(*) FROM jobs "
        "WHERE full_description IS NOT NULL AND fit_score IS NULL"
    ).fetchone()[0]

    # Score distribution
    dist_rows = conn.execute(
        "SELECT fit_score, COUNT(*) as cnt FROM jobs "
        "WHERE fit_score IS NOT NULL "
        "GROUP BY fit_score ORDER BY fit_score DESC"
    ).fetchall()
    stats["score_distribution"] = [(row[0], row[1]) for row in dist_rows]

    # Tailoring stage
    stats["tailored"] = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE tailored_resume_path IS NOT NULL"
    ).fetchone()[0]

    stats["untailored_eligible"] = conn.execute(
        "SELECT COUNT(*) FROM jobs "
        "WHERE fit_score >= 7 AND full_description IS NOT NULL "
        "AND tailored_resume_path IS NULL"
    ).fetchone()[0]

    stats["tailor_exhausted"] = conn.execute(
        "SELECT COUNT(*) FROM jobs "
        "WHERE COALESCE(tailor_attempts, 0) >= 5 "
        "AND tailored_resume_path IS NULL"
    ).fetchone()[0]

    # Cover letter stage
    stats["with_cover_letter"] = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE cover_letter_path IS NOT NULL"
    ).fetchone()[0]

    stats["cover_exhausted"] = conn.execute(
        "SELECT COUNT(*) FROM jobs "
        "WHERE COALESCE(cover_attempts, 0) >= 5 "
        "AND (cover_letter_path IS NULL OR cover_letter_path = '')"
    ).fetchone()[0]

    # Application stage
    stats["applied"] = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE applied_at IS NOT NULL"
    ).fetchone()[0]

    stats["apply_errors"] = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE apply_error IS NOT NULL"
    ).fetchone()[0]

    stats["ready_to_apply"] = conn.execute(
        "SELECT COUNT(*) FROM jobs "
        "WHERE tailored_resume_path IS NOT NULL "
        "AND applied_at IS NULL "
        "AND application_url IS NOT NULL"
    ).fetchone()[0]

    return stats

def archive_existing_jobs(conn: sqlite3.Connection | None = None, search_id: str | None = None) -> int:
    """Archive all current non-archived jobs before running a new search.
    Preserves is_saved and apply_status/applied_at intact.
    If search_id is provided and not 'all', only archive jobs matching that search_id.
    """
    if conn is None:
        conn = get_connection()
    now_date = datetime.now().strftime("%b %d")
    if search_id and search_id != "all":
        cursor = conn.execute(
            "UPDATE jobs SET is_archived = 1, archived_at = COALESCE(archived_at, ?) WHERE COALESCE(is_archived, 0) = 0 AND (search_id = ? OR search_id IS NULL OR search_id = '')",
            (now_date, search_id)
        )
    else:
        cursor = conn.execute(
            "UPDATE jobs SET is_archived = 1, archived_at = COALESCE(archived_at, ?) WHERE COALESCE(is_archived, 0) = 0",
            (now_date,)
        )
    count = cursor.rowcount
    conn.commit()
    return count


def store_jobs(conn: sqlite3.Connection, jobs: list[dict],
               site: str, strategy: str) -> tuple[int, int]:
    """Store discovered jobs, skipping duplicates by URL.

    Args:
        conn: Database connection.
        jobs: List of job dicts with keys: url, title, salary, description, location.
        site: Source site name (e.g. "RemoteOK", "Dice").
        strategy: Extraction strategy used (e.g. "json_ld", "api_response", "css_selectors").

    Returns:
        Tuple of (new_count, duplicate_count).
    """
    now = datetime.now(UTC).isoformat()
    new = 0
    existing = 0

    for job in jobs:
        url = job.get("url")
        if not url:
            continue
        try:
            conn.execute(
                "INSERT INTO jobs (url, title, salary, description, location, site, strategy, discovered_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (url, job.get("title"), job.get("salary"), job.get("description"),
                 job.get("location"), site, strategy, now),
            )
            new += 1
        except sqlite3.IntegrityError:
            existing += 1

    conn.commit()
    return new, existing


def get_jobs_by_stage(conn: sqlite3.Connection | None = None,
                      stage: str = "discovered",
                      min_score: int | None = None,
                      limit: int = 100) -> list[dict]:
    """Fetch jobs filtered by pipeline stage.

    Args:
        conn: Database connection. Uses get_connection() if None.
        stage: One of "discovered", "enriched", "scored", "tailored", "applied".
        min_score: Minimum fit_score filter (only relevant for scored+ stages).
        limit: Maximum number of rows to return.

    Returns:
        List of job dicts.
    """
    if conn is None:
        conn = get_connection()

    conditions = {
        "discovered": "1=1",
        "pending_detail": "detail_scraped_at IS NULL",
        "enriched": "full_description IS NOT NULL",
        "pending_score": "(full_description IS NOT NULL OR (description IS NOT NULL AND LENGTH(description) > 50)) AND fit_score IS NULL",
        "scored": "fit_score IS NOT NULL",
        "pending_tailor": (
            "fit_score >= ? AND (full_description IS NOT NULL OR (description IS NOT NULL AND LENGTH(description) > 50)) "
            "AND tailored_resume_path IS NULL AND COALESCE(tailor_attempts, 0) < 5"
        ),
        "tailored": "tailored_resume_path IS NOT NULL",
        "pending_apply": (
            "tailored_resume_path IS NOT NULL AND applied_at IS NULL "
            "AND application_url IS NOT NULL"
        ),
        "applied": "applied_at IS NOT NULL",
    }

    where = conditions.get(stage, "1=1")
    params: list = []

    if "?" in where and min_score is not None:
        params.append(min_score)
    elif "?" in where:
        params.append(7)  # default min_score

    if min_score is not None and "fit_score" not in where and stage in ("scored", "tailored", "applied"):
        where += " AND fit_score >= ?"
        params.append(min_score)

    query = f"SELECT * FROM jobs WHERE {where} ORDER BY fit_score DESC NULLS LAST, discovered_at DESC"
    if limit > 0:
        query += " LIMIT ?"
        params.append(limit)

    rows = conn.execute(query, params).fetchall()

    # Convert sqlite3.Row objects to dicts
    if rows:
        columns = rows[0].keys()
        return [dict(zip(columns, row)) for row in rows]
    return []


def normalize_job_title(title: str | None) -> str:
    """Normalize a title for title identity (inspired by Career-Ops detect-reposts).

    Tolerates word order and punctuation churn, but preserves semantic title tokens.
    """
    if not title:
        return ""
    import re
    # Remove emojis, special punctuation, extra whitespace
    cleaned = re.sub(r"[^\w\s]", " ", str(title).lower())
    tokens = sorted(set(cleaned.split()))
    # Remove common noise tokens (level or region markers if standalone)
    noise = {
        "remote", "hybrid", "onsite", "on-site", "fulltime", "full-time",
        "part-time", "temporary", "contract", "latam", "guatemala", "nicaragua"
    }
    meaningful = [t for t in tokens if t not in noise and len(t) > 1]
    return " ".join(meaningful) if meaningful else " ".join(tokens)


def record_scan_history(conn: sqlite3.Connection, url: str, company: str | None, title: str | None, search_id: str | None = None) -> int:
    """Record a posting in scan_history and calculate how many times it was reposted in 90 days.

    Returns:
        int: repost_count (number of distinct URLs for this company and title identity in the last 90 days).
    """
    if not url or not company or not title:
        return 1

    norm_title = normalize_job_title(title)
    comp_clean = str(company).strip().lower()

    try:
        # Check if this exact URL already exists in scan_history
        row = conn.execute("SELECT url FROM scan_history WHERE url = ?", (url,)).fetchone()
        if row:
            conn.execute("UPDATE scan_history SET last_seen_at = CURRENT_TIMESTAMP WHERE url = ?", (url,))
        else:
            conn.execute(
                "INSERT INTO scan_history (url, company, title, title_normalized, search_id) VALUES (?, ?, ?, ?, ?)",
                (url, comp_clean, str(title), norm_title, search_id)
            )
        conn.commit()

        # Calculate distinct URLs for same company and normalized title in 90 days
        c = conn.execute("""
            SELECT COUNT(DISTINCT url) 
            FROM scan_history 
            WHERE company = ? 
              AND title_normalized = ?
              AND first_seen_at >= datetime('now', '-90 days')
        """, (comp_clean, norm_title)).fetchone()

        count = max(1, c[0] if c else 1)
        # Update jobs table repost_count
        conn.execute("UPDATE jobs SET repost_count = ? WHERE url = ?", (count, url))
        conn.commit()
        return count
    except Exception as exc:
        log.warning(f"Error in record_scan_history: {exc}")
        return 1

