"""DuckDuckWork Local & Serverless Dashboard Server.

Serves the DuckDuckWork Job CRM Dashboard, search management APIs, PDF resumes,
and background search runner execution on 0.0.0.0:5050.
"""

import base64
import hashlib
import hmac
import json
import os
import socket
import sqlite3
import re
import threading
import time
from datetime import UTC, datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from applypilot.config import (
    APP_DIR,
    extract_pdf_text,
    get_search_profiles,
    load_env,
    load_search_config,
    save_search_config,
    update_profile_last_run,
)

# Load environment variables (.env / GEMINI_API_KEY) immediately
load_env()

# Global thread-safe search runner state
_RUNNER_LOCK = threading.Lock()
_SEARCH_RUNNER_STATE = {
    "status": "idle",
    "message": "Idle",
    "new_jobs": 0,
    "last_run": None,
    "error": None,
    "search_id": None,
    "scoring_completed": 0,
    "scoring_total": 0,
    "scoring_percent": 0,
    "tab_new_counts": {},
    "completed_profiles": [],
}
_SESSION_ACTIVITY: dict[str, float] = {}

AUTO_RUN_INTERVAL_MAP: dict[str, int] = {
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "2h": 120,
}
_AUTO_RUN_LOCK = threading.Lock()
_AUTO_RUN_STATE: dict[str, object] = {
    "enabled": False,
    "interval": "1h",
    "interval_minutes": 60,
    "last_run": 0.0,
    "next_run": 0.0,
}
_AUTO_RUN_SCHEDULER_STARTED = False


def get_local_ip() -> str:
    """Find local LAN IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def log_event(message: str, level: str = "INFO"):
    base_data_dir = Path(os.environ.get("APPLYPILOT_BASE_DIR", os.environ.get("APPLYPILOT_DIR", str(Path.cwd() / ".data"))))
    if base_data_dir.name == "sessions" or "sessions" in base_data_dir.parts:
        idx = base_data_dir.parts.index("sessions") if "sessions" in base_data_dir.parts else 0
        base_data_dir = Path(*base_data_dir.parts[:idx])
    
    logs_dir = base_data_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / "applypilot.log"
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] [{level}] {message}\n"
    print(entry.strip())
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception:
        pass


def _run_search_background_task(target_search_id: str | None = None, skip_scoring: bool = False, target_app_dir: Path | str | None = None):
    """Background worker thread executing JobSpy discovery + optional Gemini fit scoring."""
    global _SEARCH_RUNNER_STATE
    if target_app_dir:
        os.environ["APPLYPILOT_DIR"] = str(target_app_dir)
    app_dir = Path(os.environ.get("APPLYPILOT_DIR", str(APP_DIR)))

    log_event(f"Starting search runner task (target={target_search_id or 'all'}, session={app_dir.name})")

    with _RUNNER_LOCK:
        _SEARCH_RUNNER_STATE["status"] = "scraping"
        _SEARCH_RUNNER_STATE["message"] = "Searching job listings across enabled sources..."
        _SEARCH_RUNNER_STATE["search_id"] = target_search_id
        _SEARCH_RUNNER_STATE["error"] = None
        _SEARCH_RUNNER_STATE["new_jobs"] = 0
        _SEARCH_RUNNER_STATE["scoring_completed"] = 0
        _SEARCH_RUNNER_STATE["scoring_total"] = 0
        _SEARCH_RUNNER_STATE["scoring_percent"] = 0
        _SEARCH_RUNNER_STATE["tab_new_counts"] = {}
        _SEARCH_RUNNER_STATE["completed_profiles"] = []

    try:
        from applypilot.database import init_db
        from applypilot.discovery.jobspy import run_discovery
        from applypilot.scoring.scorer import run_scoring
        from applypilot.view import generate_dashboard

        # Ensure database tables are initialized for target session
        init_db()

        # Step 1: Sequential Execution for "all" or Single Profile
        full_cfg = load_search_config()
        all_profiles = get_search_profiles()
        tab_new_counts: dict[str, int] = {}

        if target_search_id == "all":
            active_profiles = [p for p in all_profiles if p.get("enabled", True)]
            if not active_profiles:
                active_profiles = all_profiles

            tot_p = len(active_profiles)
            total_new = 0
            completed_profiles: list[str] = []

            for idx_p, p in enumerate(active_profiles):
                p_id = p.get("id", f"search_{idx_p+1}")
                p_name = p.get("name", f"Profile {idx_p+1}")
                cur_num = idx_p + 1

                with _RUNNER_LOCK:
                    _SEARCH_RUNNER_STATE["status"] = "scraping"
                    _SEARCH_RUNNER_STATE["search_id"] = p_id
                    _SEARCH_RUNNER_STATE["message"] = f"[{cur_num}/{tot_p}] Searching listings for {p_name}..."
                    _SEARCH_RUNNER_STATE["scoring_completed"] = 0
                    _SEARCH_RUNNER_STATE["scoring_total"] = 0
                    _SEARCH_RUNNER_STATE["scoring_percent"] = 0

                log_event(f"Sequential run [{cur_num}/{tot_p}]: Discovery for {p_name} ({p_id})")
                profile_cfg = dict(full_cfg)
                profile_cfg["searches"] = [p]

                disc_res = run_discovery(profile_cfg)
                p_new = disc_res.get("new", 0)
                tab_new_counts[p_id] = p_new
                total_new += p_new
                log_event(f"Discovery for {p_name} completed. Found {p_new} new listings.")
                with _RUNNER_LOCK:
                    _SEARCH_RUNNER_STATE["tab_new_counts"] = dict(tab_new_counts)

                if not skip_scoring:
                    log_event(f"Starting Gemini fit scoring for {p_name}...")
                    with _RUNNER_LOCK:
                        _SEARCH_RUNNER_STATE["status"] = "scoring"
                        _SEARCH_RUNNER_STATE["search_id"] = p_id
                        _SEARCH_RUNNER_STATE["message"] = f"[{cur_num}/{tot_p}] Preparing Gemini scoring for {p_name}..."
                        _SEARCH_RUNNER_STATE["new_jobs"] = total_new
                        _SEARCH_RUNNER_STATE["scoring_completed"] = 0
                        _SEARCH_RUNNER_STATE["scoring_total"] = 0
                        _SEARCH_RUNNER_STATE["scoring_percent"] = 0

                    def _on_scoring_progress(completed: int, total: int, cur_p=p_name, cur_n=cur_num, tot_n=tot_p) -> None:
                        if total <= 0:
                            with _RUNNER_LOCK:
                                _SEARCH_RUNNER_STATE["status"] = "scoring"
                                _SEARCH_RUNNER_STATE["message"] = f"[{cur_n}/{tot_n}] Scoring {cur_p} jobs with Gemini..."
                                _SEARCH_RUNNER_STATE["scoring_completed"] = 0
                                _SEARCH_RUNNER_STATE["scoring_total"] = 0
                                _SEARCH_RUNNER_STATE["scoring_percent"] = 0
                            return

                        pct = int((completed / total) * 100) if total > 0 else 0
                        curr_job = min(completed + 1, total) if completed < total else total
                        msg = f"[{cur_n}/{tot_n}] Scoring {cur_p} ({curr_job}/{total}) with Gemini... [{pct}%]"
                        with _RUNNER_LOCK:
                            _SEARCH_RUNNER_STATE["status"] = "scoring"
                            _SEARCH_RUNNER_STATE["message"] = msg
                            _SEARCH_RUNNER_STATE["scoring_completed"] = completed
                            _SEARCH_RUNNER_STATE["scoring_total"] = total
                            _SEARCH_RUNNER_STATE["scoring_percent"] = pct

                    try:
                        run_scoring(limit=0, rescore=False, search_id=p_id, progress_callback=_on_scoring_progress)
                        log_event(f"Gemini fit scoring completed for {p_name}.")
                    except Exception as score_err:
                        log_event(f"Scoring notice/warning for {p_name}: {score_err}", level="WARN")

                completed_profiles.append(p_id)
                try:
                    update_profile_last_run(p_id)
                except Exception as lr_err:
                    log_event(f"Notice: unable to update last_run for {p_id}: {lr_err}", level="WARN")

                with _RUNNER_LOCK:
                    _SEARCH_RUNNER_STATE["completed_profiles"] = list(completed_profiles)
                    _SEARCH_RUNNER_STATE["tab_new_counts"] = dict(tab_new_counts)

                try:
                    generate_dashboard()
                    log_event(f"Dashboard updated and tab released for completed profile {p_name} ({p_id}).")
                except Exception as gen_err:
                    log_event(f"Dashboard generation warning for {p_id}: {gen_err}", level="WARN")

            new_found = total_new
            now_str = datetime.now().strftime("%I:%M %p")
            finish_msg = f"Finished all searches! Found {new_found} new roles across {tot_p} profiles at {now_str}."
        else:
            # Single search profile execution
            single_profile = None
            if target_search_id:
                matched = [p for p in all_profiles if p.get("id") == target_search_id]
                if matched:
                    single_profile = matched[0]
                    full_cfg["searches"] = matched

            p_id = target_search_id or (single_profile.get("id") if single_profile else "search_1")
            p_name = single_profile.get("name", "Search") if single_profile else "Search"

            disc_res = run_discovery(full_cfg)
            new_found = disc_res.get("new", 0)
            tab_new_counts[p_id] = new_found
            log_event(f"JobSpy discovery completed. Found {new_found} new job listings.")
            with _RUNNER_LOCK:
                _SEARCH_RUNNER_STATE["tab_new_counts"] = dict(tab_new_counts)

            if not skip_scoring:
                log_event(f"Starting Gemini fit scoring for {p_name}...")
                with _RUNNER_LOCK:
                    _SEARCH_RUNNER_STATE["status"] = "scoring"
                    _SEARCH_RUNNER_STATE["message"] = f"Preparing Gemini fit scoring for {p_name}..."
                    _SEARCH_RUNNER_STATE["new_jobs"] = new_found
                    _SEARCH_RUNNER_STATE["scoring_completed"] = 0
                    _SEARCH_RUNNER_STATE["scoring_total"] = 0
                    _SEARCH_RUNNER_STATE["scoring_percent"] = 0

                def _on_single_scoring_progress(completed: int, total: int, cur_p=p_name) -> None:
                    if total <= 0:
                        with _RUNNER_LOCK:
                            _SEARCH_RUNNER_STATE["status"] = "scoring"
                            _SEARCH_RUNNER_STATE["message"] = f"Scoring {cur_p} jobs with Gemini..."
                            _SEARCH_RUNNER_STATE["scoring_completed"] = 0
                            _SEARCH_RUNNER_STATE["scoring_total"] = 0
                            _SEARCH_RUNNER_STATE["scoring_percent"] = 0
                        return

                    pct = int((completed / total) * 100) if total > 0 else 0
                    curr_job = min(completed + 1, total) if completed < total else total
                    msg = f"Scoring job {curr_job} of {total} with Gemini... [{pct}%]"
                    with _RUNNER_LOCK:
                        _SEARCH_RUNNER_STATE["status"] = "scoring"
                        _SEARCH_RUNNER_STATE["message"] = msg
                        _SEARCH_RUNNER_STATE["scoring_completed"] = completed
                        _SEARCH_RUNNER_STATE["scoring_total"] = total
                        _SEARCH_RUNNER_STATE["scoring_percent"] = pct

                try:
                    scoring_search_id = target_search_id if (target_search_id and target_search_id != "all") else None
                    run_scoring(limit=0, rescore=False, search_id=scoring_search_id, progress_callback=_on_single_scoring_progress)
                    log_event("Gemini fit scoring completed.")
                except Exception as score_err:
                    log_event(f"Scoring notice/warning: {score_err}", level="WARN")

            completed_profiles = [p_id]
            try:
                update_profile_last_run(p_id)
            except Exception as lr_err:
                log_event(f"Notice: unable to update last_run for {p_id}: {lr_err}", level="WARN")

            with _RUNNER_LOCK:
                _SEARCH_RUNNER_STATE["completed_profiles"] = list(completed_profiles)

            now_str = datetime.now().strftime("%I:%M %p")
            finish_msg = f"Finished! Found {new_found} new roles at {now_str}."

        # Step 3: Regenerate Dashboard HTML immediately
        generate_dashboard()

        # Step 4: Sync discovered jobs and session data to Neon PostgreSQL cloud database
        try:
            sid_name = app_dir.name.removeprefix("ap_")
            save_session_to_cloud(sid_name, app_dir)
        except Exception as cloud_err:
            log_event(f"Cloud sync notice/warning: {cloud_err}", level="WARN")

        with _RUNNER_LOCK:
            _SEARCH_RUNNER_STATE["status"] = "complete"
            _SEARCH_RUNNER_STATE["message"] = finish_msg
            _SEARCH_RUNNER_STATE["new_jobs"] = new_found
            _SEARCH_RUNNER_STATE["tab_new_counts"] = tab_new_counts
            _SEARCH_RUNNER_STATE["completed_profiles"] = list(completed_profiles)
            _SEARCH_RUNNER_STATE["last_run"] = now_str
            _SEARCH_RUNNER_STATE["scoring_completed"] = 0
            _SEARCH_RUNNER_STATE["scoring_total"] = 0
            _SEARCH_RUNNER_STATE["scoring_percent"] = 0

        log_event(f"Search task finished successfully. Found {new_found} new roles at {now_str}.")

        # Step 5: Dispatch Telegram Push Notification if enabled
        if new_found > 0:
            try:
                from applypilot.notifications import dispatch_telegram_job_alert
                from applypilot.database import get_connection
                db_target = app_dir / "applypilot.db"
                conn = get_connection(db_target if db_target.exists() else None)
                c = conn.cursor()
                c.execute("""
                    SELECT title, company, location, salary, fit_score, url, site, search_name
                    FROM jobs
                    WHERE is_archived = 0
                    ORDER BY discovered_at DESC, COALESCE(fit_score, 0) DESC
                    LIMIT ?
                """, (min(new_found, 10),))
                rows = c.fetchall()
                col_names = [d[0] for d in c.description]
                recent_jobs = [dict(zip(col_names, r)) for r in rows]
                search_label = "All Searches" if target_search_id == "all" else (single_profile.get("name", "Search") if single_profile else "Search")

                # Count active unreviewed / pending jobs in the feed
                if target_search_id == "all" or not single_profile:
                    c.execute("""
                        SELECT COUNT(*) FROM jobs
                        WHERE is_archived = 0 AND is_saved = 0 AND (applied_at IS NULL OR applied_at = '')
                    """)
                else:
                    c.execute("""
                        SELECT COUNT(*) FROM jobs
                        WHERE is_archived = 0 AND is_saved = 0 AND (applied_at IS NULL OR applied_at = '')
                          AND (search_id = ? OR search_name = ?)
                    """, (target_search_id, single_profile.get("name", "")))
                row = c.fetchone()
                total_pending = row[0] if row else None

                dispatch_telegram_job_alert(
                    jobs=recent_jobs,
                    search_name=search_label,
                    session_id=app_dir.name.removeprefix("ap_") if app_dir.name != ".data" else "",
                    total_pending_count=total_pending,
                    new_found_count=new_found,
                )
            except Exception as notify_err:
                log_event(f"Telegram notification notice: {notify_err}", level="WARN")

    except Exception as e:
        now_str = datetime.now().strftime("%I:%M %p")
        log_event(f"Search runner task failed: {e}", level="ERROR")
        with _RUNNER_LOCK:
            _SEARCH_RUNNER_STATE["status"] = "error"
            _SEARCH_RUNNER_STATE["message"] = f"Search error: {e}"
            _SEARCH_RUNNER_STATE["error"] = str(e)
            _SEARCH_RUNNER_STATE["last_run"] = now_str
            _SEARCH_RUNNER_STATE["scoring_completed"] = 0
            _SEARCH_RUNNER_STATE["scoring_total"] = 0
            _SEARCH_RUNNER_STATE["scoring_percent"] = 0


ADMIN_AUTH_TOKEN = "admin_token_ddw_2026_auth"

def load_admin_config() -> dict:
    base_data_dir = Path(os.environ.get("APPLYPILOT_BASE_DIR", os.environ.get("APPLYPILOT_DIR", str(Path.cwd() / ".data"))))
    if base_data_dir.name == "sessions" or "sessions" in base_data_dir.parts:
        idx = base_data_dir.parts.index("sessions") if "sessions" in base_data_dir.parts else 0
        base_data_dir = Path(*base_data_dir.parts[:idx])
    cfg_file = base_data_dir / "admin_config.json"
    cfg = {}
    if cfg_file.exists():
        try:
            cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    if "maintenance_mode" not in cfg:
        cfg["maintenance_mode"] = False
    if "admin_password" not in cfg:
        cfg["admin_password"] = os.environ.get("ADMIN_PASSWORD", "ddw2026")
    if "permanent_sessions" not in cfg or not isinstance(cfg.get("permanent_sessions"), list):
        cfg["permanent_sessions"] = []
    if "deleted_sessions" not in cfg or not isinstance(cfg.get("deleted_sessions"), list):
        cfg["deleted_sessions"] = []
    # Environment variables always take precedence
    env_webshare = os.environ.get("WEBSHARE_API_KEY", "").strip()
    if env_webshare:
        cfg["webshare_api_key"] = env_webshare
    env_gemini = os.environ.get("GEMINI_API_KEY", "").strip()
    if env_gemini:
        cfg["gemini_api_key"] = env_gemini
    env_openai = os.environ.get("OPENAI_API_KEY", "").strip()
    if env_openai:
        cfg["openai_api_key"] = env_openai
    env_llm_url = os.environ.get("LLM_URL", "").strip()
    if env_llm_url:
        cfg["llm_url"] = env_llm_url
    env_llm_model = os.environ.get("LLM_MODEL", "").strip()
    if env_llm_model:
        cfg["llm_model"] = env_llm_model
    env_llm_api_key = os.environ.get("LLM_API_KEY", "").strip()
    if env_llm_api_key:
        cfg["llm_api_key"] = env_llm_api_key
    env_tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if env_tg_token:
        cfg["telegram_bot_token"] = env_tg_token
    env_tg_chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if env_tg_chat:
        cfg["telegram_chat_id"] = env_tg_chat
    if "TELEGRAM_ENABLED" in os.environ:
        cfg["telegram_enabled"] = os.environ.get("TELEGRAM_ENABLED", "").lower() in ("1", "true")
    if "TELEGRAM_MIN_SCORE" in os.environ:
        try:
            cfg["telegram_min_score"] = int(os.environ.get("TELEGRAM_MIN_SCORE", 0))
        except Exception:
            pass
    if "telegram_enabled" not in cfg:
        cfg["telegram_enabled"] = False
    if "telegram_bot_token" not in cfg:
        cfg["telegram_bot_token"] = ""
    if "telegram_chat_id" not in cfg:
        cfg["telegram_chat_id"] = ""
    if "telegram_min_score" not in cfg:
        cfg["telegram_min_score"] = 0
    env_wa_phone = os.environ.get("WHATSAPP_PHONE", "").strip()
    if env_wa_phone:
        cfg["whatsapp_phone"] = env_wa_phone
    env_wa_key = os.environ.get("WHATSAPP_API_KEY", "").strip()
    if env_wa_key:
        cfg["whatsapp_api_key"] = env_wa_key
    if "WHATSAPP_ENABLED" in os.environ:
        cfg["whatsapp_enabled"] = os.environ.get("WHATSAPP_ENABLED", "").lower() in ("1", "true")
    if "whatsapp_enabled" not in cfg:
        cfg["whatsapp_enabled"] = False
    if "whatsapp_phone" not in cfg:
        cfg["whatsapp_phone"] = ""
    if "whatsapp_api_key" not in cfg:
        cfg["whatsapp_api_key"] = ""
    return cfg

def save_admin_config(cfg: dict) -> None:
    base_data_dir = Path(os.environ.get("APPLYPILOT_BASE_DIR", os.environ.get("APPLYPILOT_DIR", str(Path.cwd() / ".data"))))
    if base_data_dir.name == "sessions" or "sessions" in base_data_dir.parts:
        idx = base_data_dir.parts.index("sessions") if "sessions" in base_data_dir.parts else 0
        base_data_dir = Path(*base_data_dir.parts[:idx])
    cfg_file = base_data_dir / "admin_config.json"
    cfg_file.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def calculate_auto_run_next_run(interval_minutes: int) -> float:
    """Calculate the next execution timestamp based on profile search recency.
    If any profile has never run or is older than interval_minutes, run immediately (return time.time()).
    Otherwise, return oldest_run + interval_seconds.
    """
    now = time.time()
    interval_seconds = interval_minutes * 60
    profiles = get_search_profiles()
    if not profiles:
        return now

    oldest_run = None
    for p in profiles:
        lr = p.get("last_run")
        if not lr:
            return now
        try:
            val = float(lr)
            ts = val if val < 1e11 else val / 1000.0
            if oldest_run is None or ts < oldest_run:
                oldest_run = ts
        except Exception:
            return now

    if oldest_run is None:
        return now

    elapsed_since_oldest = now - oldest_run
    if elapsed_since_oldest >= interval_seconds:
        return now

    next_due = oldest_run + interval_seconds
    return max(now, min(next_due, now + interval_seconds))


def start_auto_run_scheduler() -> None:
    """Start background scheduler daemon for automated searches."""
    global _AUTO_RUN_SCHEDULER_STARTED
    if _AUTO_RUN_SCHEDULER_STARTED:
        return
    _AUTO_RUN_SCHEDULER_STARTED = True

    try:
        cfg = load_admin_config()
        enabled = bool(cfg.get("auto_run_enabled", False))
        interval = str(cfg.get("auto_run_interval", "1h")).strip()
        if interval not in AUTO_RUN_INTERVAL_MAP:
            interval = "1h"
        interval_mins = AUTO_RUN_INTERVAL_MAP[interval]
        with _AUTO_RUN_LOCK:
            _AUTO_RUN_STATE["enabled"] = enabled
            _AUTO_RUN_STATE["interval"] = interval
            _AUTO_RUN_STATE["interval_minutes"] = interval_mins
            if enabled:
                _AUTO_RUN_STATE["next_run"] = calculate_auto_run_next_run(interval_mins)
            else:
                _AUTO_RUN_STATE["next_run"] = 0.0
    except Exception as e:
        log_event(f"Error initializing auto-run scheduler: {e}")

    t = threading.Thread(target=_auto_run_scheduler_daemon, daemon=True, name="AutoRunScheduler")
    t.start()


def _auto_run_scheduler_daemon() -> None:
    """Periodic daemon loop to evaluate and trigger automated background searches."""
    log_event("Auto-run scheduler daemon started")
    while True:
        try:
            time.sleep(5)
            with _AUTO_RUN_LOCK:
                enabled = bool(_AUTO_RUN_STATE["enabled"])
                interval_mins = int(_AUTO_RUN_STATE["interval_minutes"])
                next_run = float(_AUTO_RUN_STATE["next_run"])

            if not enabled:
                continue

            now = time.time()
            if next_run > 0 and now >= next_run:
                with _RUNNER_LOCK:
                    is_busy = _SEARCH_RUNNER_STATE.get("status") in ("scraping", "scoring")

                if not is_busy:
                    with _AUTO_RUN_LOCK:
                        _AUTO_RUN_STATE["last_run"] = now
                        _AUTO_RUN_STATE["next_run"] = now + (interval_mins * 60)

                    log_event(f"[Auto-Run] Starting automated execution for all searches (interval={interval_mins}m)")
                    base_dir = Path(os.environ.get("APPLYPILOT_BASE_DIR", os.environ.get("APPLYPILOT_DIR", str(Path.cwd() / ".data"))))
                    if base_dir.name == "sessions" or "sessions" in base_dir.parts:
                        idx = base_dir.parts.index("sessions") if "sessions" in base_dir.parts else 0
                        base_dir = Path(*base_dir.parts[:idx])

                    t = threading.Thread(
                        target=_run_search_background_task,
                        args=("all", False, base_dir),
                        daemon=True,
                    )
                    t.start()
        except Exception as e:
            log_event(f"Auto-run scheduler error: {e}")


def is_new_user(app_dir: Path) -> bool:
    """Check if the current installation needs onboarding."""
    admin_cfg = load_admin_config()
    if admin_cfg.get("onboarded"):
        return False

    # Check if database exists and has any jobs
    db_path = app_dir / "applypilot.db"
    if db_path.exists():
        try:
            import sqlite3
            conn = sqlite3.connect(str(db_path), timeout=5)
            cnt = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            conn.close()
            if cnt > 0:
                admin_cfg["onboarded"] = True
                save_admin_config(admin_cfg)
                return False
        except Exception:
            pass

    # Check if searches.yaml exists with real searches and has API key
    cfg_path = app_dir / "searches.yaml"
    if cfg_path.exists():
        try:
            import yaml
            raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            searches = raw.get("searches", [])
            has_key = bool(admin_cfg.get("gemini_api_key") or admin_cfg.get("llm_api_key") or admin_cfg.get("openai_api_key") or os.environ.get("GEMINI_API_KEY"))
            if searches and len(searches) > 0 and has_key:
                admin_cfg["onboarded"] = True
                save_admin_config(admin_cfg)
                return False
        except Exception:
            pass

    return True

_FAILED_ADMIN_LOGINS: dict[str, list[float]] = {}


def check_rate_limit(ip: str, max_attempts: int = 5, window_sec: int = 900) -> bool:
    """Check if IP has exceeded login attempt threshold (5 attempts / 15 minutes)."""
    now = time.time()
    attempts = [t for t in _FAILED_ADMIN_LOGINS.get(ip, []) if now - t < window_sec]
    _FAILED_ADMIN_LOGINS[ip] = attempts
    return len(attempts) < max_attempts


def record_failed_login(ip: str):
    """Record a failed login attempt for rate limiting."""
    now = time.time()
    if ip not in _FAILED_ADMIN_LOGINS:
        _FAILED_ADMIN_LOGINS[ip] = []
    _FAILED_ADMIN_LOGINS[ip].append(now)


def generate_hmac_token(secret: str | None = None) -> str:
    """Generate a 24-hour time-bound HMAC authentication token."""
    if not secret:
        secret = os.environ.get("ADMIN_AUTH_TOKEN", "admin123")
    ts = int(time.time())
    msg = f"ddw_admin:{ts}".encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return f"{ts}:{sig}"


def verify_hmac_token(token: str | None, secret: str | None = None, max_age: int = 86400) -> bool:
    """Verify HMAC token validity and expiration (Item C)."""
    if not token:
        return False
    if token == ADMIN_AUTH_TOKEN:
        return True  # Static fallback
    if ":" not in token:
        return False
    if not secret:
        secret = os.environ.get("ADMIN_AUTH_TOKEN", "admin123")
    try:
        ts_str, sig = token.split(":", 1)
        ts = int(ts_str)
        if time.time() - ts > max_age:
            return False
        msg = f"ddw_admin:{ts}".encode("utf-8")
        expected_sig = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected_sig)
    except Exception:
        return False


def generate_csrf_token(secret: str | None = None) -> str:
    """Generate anti-CSRF token for admin POST operations (Item F)."""
    if not secret:
        secret = os.environ.get("ADMIN_AUTH_TOKEN", "admin123")
    ts = int(time.time())
    msg = f"ddw_csrf:{ts}".encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()[:16]
    return f"{ts}:{sig}"


def verify_csrf_token(token: str | None, secret: str | None = None, max_age: int = 86400) -> bool:
    """Verify anti-CSRF token for administrative state changes (Item F)."""
    if not token:
        return False
    if token in (ADMIN_AUTH_TOKEN, "legacy_bypass"):
        return True
    if ":" not in token:
        return False
    if not secret:
        secret = os.environ.get("ADMIN_AUTH_TOKEN", "admin123")
    try:
        ts_str, sig = token.split(":", 1)
        ts = int(ts_str)
        if time.time() - ts > max_age:
            return False
        msg = f"ddw_csrf:{ts}".encode("utf-8")
        expected_sig = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()[:16]
        return hmac.compare_digest(sig, expected_sig)
    except Exception:
        return False


def check_and_clean_inactive_session(session_dir: Path, inactive_days: int = 5, delete_days: int = 10, ttl_days: int | None = None) -> dict:
    """Enforce 5-day inactive soft-purge and 10-day hard deletion lifecycle rules.

    Rule 1 (5 days): Soft-purge unclassified `new` jobs (is_saved=0 AND apply_status in ('new','unapplied',NULL) AND is_archived=0).
    Preserves all search profiles, resumes, saved jobs, applied jobs, and archived jobs.
    Rule 2 (10 days): Permanently delete session directory and tombstone sid in deleted_sessions.
    Permanent sessions (marked in permanent_sessions array) are exempt from both rules.
    """
    if ttl_days is not None:
        inactive_days = ttl_days
    res = {"status": "active", "deleted": False}
    if not session_dir.exists():
        return res

    sid = session_dir.name.removeprefix("ap_")
    admin_cfg = load_admin_config()
    permanent_sessions = admin_cfg.get("permanent_sessions", [])
    deleted_sessions = admin_cfg.get("deleted_sessions", [])

    if sid in permanent_sessions:
        return res

    mtime = session_dir.stat().st_mtime
    days_inactive = (time.time() - mtime) / 86400.0

    if days_inactive >= delete_days:
        import shutil
        shutil.rmtree(session_dir, ignore_errors=True)
        if sid not in deleted_sessions:
            deleted_sessions.append(sid)
            admin_cfg["deleted_sessions"] = deleted_sessions
            save_admin_config(admin_cfg)
        res["status"] = "deleted"
        res["deleted"] = True
        return res

    if days_inactive >= inactive_days:
        db_file = session_dir / "applypilot.db"
        if db_file.exists():
            try:
                from applypilot.database import ensure_columns
                conn = sqlite3.connect(str(db_file), timeout=10)
                ensure_columns(conn)
                c = conn.cursor()
                # Delete ONLY unclassified new jobs (un-saved, un-applied, un-archived)
                c.execute("""
                    DELETE FROM jobs
                    WHERE COALESCE(is_saved, 0) = 0
                    AND COALESCE(is_archived, 0) = 0
                    AND (COALESCE(apply_status, '') = '' OR apply_status = 'new' OR apply_status = 'unapplied')
                """)
                conn.commit()
                conn.execute("VACUUM")
                conn.close()
            except Exception:
                pass
        marker = session_dir / ".inactive"
        if not marker.exists():
            try:
                marker.touch()
            except Exception:
                pass
        res["status"] = "inactive"

    return res


def get_admin_data() -> dict:
    import yaml
    base_data_dir = Path(os.environ.get("APPLYPILOT_BASE_DIR", os.environ.get("APPLYPILOT_DIR", str(Path.cwd() / ".data"))))
    if base_data_dir.name == "sessions":
        base_data_dir = base_data_dir.parent
    elif "sessions" in base_data_dir.parts:
        idx = base_data_dir.parts.index("sessions")
        base_data_dir = Path(*base_data_dir.parts[:idx])

    admin_cfg = load_admin_config()
    permanent_sessions = set(admin_cfg.get("permanent_sessions", ["1ag8q26h"]))

    sessions_dir = base_data_dir / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)

    # Hydrate cloud sessions from Neon PostgreSQL if running in serverless / cloud env
    pg_conn = get_postgres_connection()
    if pg_conn:
        try:
            init_postgres_db(pg_conn)
            pg_s_rows = pg_conn.run("SELECT session_id, COALESCE(is_permanent, 0) FROM duckduckwork_sessions")
            if pg_s_rows:
                for pgrow in pg_s_rows:
                    pg_sid = str(pgrow[0]).removeprefix("ap_")
                    pg_perm = bool(pgrow[1]) if len(pgrow) > 1 else False
                    if pg_perm:
                        permanent_sessions.add(pg_sid)
                    s_target = sessions_dir / pg_sid
                    if not (s_target / "searches.yaml").exists() and not (s_target / "applypilot.db").exists():
                        try:
                            load_session_from_cloud(pg_sid, s_target)
                        except Exception:
                            pass
        except Exception:
            pass

    sessions_list = []
    total_jobs = 0

    if sessions_dir.exists():
        for s_folder in sorted(sessions_dir.glob("*")):
            if s_folder.is_dir() and not s_folder.name.startswith("."):
                sid = s_folder.name.removeprefix("ap_")
                if (s_folder / ".permanent").exists():
                    permanent_sessions.add(sid)
                is_permanent = (sid in permanent_sessions)
                
                db_file = s_folder / "applypilot.db"
                jobs_count = 0
                jobs_new = 0
                jobs_saved = 0
                jobs_applied = 0
                jobs_archived = 0

                if db_file.exists():
                    try:
                        conn = sqlite3.connect(str(db_file))
                        c = conn.cursor()
                        c.execute("SELECT COUNT(*) FROM jobs")
                        jobs_count = c.fetchone()[0]
                        c.execute("SELECT COUNT(*) FROM jobs WHERE COALESCE(is_archived, 0) = 0 AND COALESCE(is_saved, 0) = 0 AND (apply_status IS NULL OR apply_status = 'new' OR apply_status = 'unapplied')")
                        jobs_new = c.fetchone()[0]
                        c.execute("SELECT COUNT(*) FROM jobs WHERE is_saved = 1")
                        jobs_saved = c.fetchone()[0]
                        c.execute("SELECT COUNT(*) FROM jobs WHERE apply_status = 'applied' OR LOWER(COALESCE(sub_status, '')) = 'applied'")
                        jobs_applied = c.fetchone()[0]
                        c.execute("SELECT COUNT(*) FROM jobs WHERE is_archived = 1")
                        jobs_archived = c.fetchone()[0]
                        conn.close()
                    except Exception:
                        pass
                
                total_jobs += jobs_count
                
                cfg_file = s_folder / "searches.yaml"
                searches_count = 0
                active_tabs = []
                if cfg_file.exists():
                    try:
                        cfg_data = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}
                        searches_list_cfg = cfg_data.get("searches", [])
                        searches_count = len(searches_list_cfg)
                        for item in searches_list_cfg:
                            name = (item.get("name") or "").strip()
                            if not name and item.get("queries"):
                                name = ", ".join(item["queries"])
                            if name:
                                active_tabs.append(name)
                        if not active_tabs and cfg_data.get("queries"):
                            for q in cfg_data["queries"]:
                                if isinstance(q, dict) and q.get("query"):
                                    active_tabs.append(q["query"])
                                elif isinstance(q, str):
                                    active_tabs.append(q)
                    except Exception:
                        pass
                
                folder_size = 0
                file_mtimes = []
                file_ctimes = []
                for f in s_folder.glob("**/*"):
                    if f.is_file():
                        st = f.stat()
                        folder_size += st.st_size
                        file_mtimes.append(st.st_mtime)
                        btime = getattr(st, 'st_birthtime', st.st_ctime)
                        file_ctimes.append(btime)

                size_mb = round(folder_size / (1024 * 1024), 2)

                if file_mtimes:
                    ctime = min(file_ctimes)
                    mtime = max(file_mtimes)
                else:
                    ctime = s_folder.stat().st_ctime
                    mtime = s_folder.stat().st_mtime

                last_accessed = _SESSION_ACTIVITY.get(sid, 0)
                if last_accessed > mtime:
                    mtime = last_accessed

                created_ts = int(ctime * 1000)
                last_active_ts = int(mtime * 1000)
                created_str = datetime.fromtimestamp(ctime).strftime("%Y-%m-%d %H:%M")
                last_active_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")

                is_online = (time.time() - last_accessed < 600) or (time.time() - mtime < 600)
                
                is_running = (_SEARCH_RUNNER_STATE.get("status") in ("scraping", "scoring", "running"))
                runner_st = _SEARCH_RUNNER_STATE.get("status") if is_running else None

                if runner_st in ("scraping", "running"):
                    status_label = "Scraping..."
                    status_cls = "status-running"
                elif runner_st == "scoring":
                    status_label = "Scoring..."
                    status_cls = "status-running"
                elif is_online:
                    status_label = "Online"
                    status_cls = "status-running"
                elif (s_folder / ".inactive").exists():
                    status_label = "Inactive"
                    status_cls = "status-idle"
                else:
                    status_label = "Idle"
                    status_cls = "status-idle"
                
                sessions_list.append({
                    "id": sid,
                    "created_at": created_str,
                    "last_active": last_active_str,
                    "created_ts": created_ts,
                    "last_active_ts": last_active_ts,
                    "searches_count": searches_count,
                    "tabs": active_tabs,
                    "jobs_count": jobs_count,
                    "jobs_new": jobs_new,
                    "jobs_saved": jobs_saved,
                    "jobs_applied": jobs_applied,
                    "jobs_archived": jobs_archived,
                    "size_mb": size_mb,
                    "is_running": is_running,
                    "is_online": is_online,
                    "is_permanent": is_permanent,
                    "status_label": status_label,
                    "status_cls": status_cls,
                })

    def _get_session_sort_key(s: dict):
        st_cls = s.get("status_cls", "")
        is_online = s.get("is_online", False)
        is_running = s.get("is_running", False)
        if is_running or st_cls == "status-running":
            rank = 0
        elif is_online or s.get("status_label") == "Online":
            rank = 1
        else:
            rank = 2
        return (rank, -s.get("last_active_ts", 0))

    sessions_list.sort(key=_get_session_sort_key)

    g_key = (os.environ.get("GEMINI_API_KEY") or admin_cfg.get("gemini_api_key", "")).strip()
    
    total_storage = 0
    if base_data_dir.exists():
        for f in base_data_dir.glob("**/*"):
            if f.is_file():
                total_storage += f.stat().st_size
    storage_size_mb = round(total_storage / (1024 * 1024), 2)
    
    log_lines = []
    log_files = sorted(base_data_dir.glob("logs/*.log")) + sorted(base_data_dir.glob("*.log")) + sorted(base_data_dir.glob("sessions/**/*.log"))
    for lf in log_files:
        if lf.is_file() and lf.stat().st_size > 0:
            try:
                lines = lf.read_text(encoding="utf-8", errors="ignore").splitlines()
                log_lines.extend(lines)
            except Exception:
                pass

    if log_lines:
        logs_content = "\n".join(log_lines[-100:])
    else:
        logs_content = f"No execution log entries captured yet in {base_data_dir.resolve()}"
        
    online_sessions_count = sum(1 for s in sessions_list if s.get("is_online"))

    curr_proxy = os.environ.get("APPLYPILOT_PROXY") or os.environ.get("JOBSPY_PROXY") or os.environ.get("PROXY_URL") or admin_cfg.get("proxy", "")
    proxy_masked = f"{curr_proxy[:10]}..." if len(curr_proxy) > 10 else (curr_proxy if curr_proxy else "Not Configured")

    curr_webshare_key = (os.environ.get("WEBSHARE_API_KEY") or admin_cfg.get("webshare_api_key", "")).strip()
    webshare_key_masked = f"{curr_webshare_key[:6]}••••••••{curr_webshare_key[-4:]}" if len(curr_webshare_key) >= 10 else ("Configured" if curr_webshare_key else "Not Set")

    user_jobs_count = 0
    user_scored_jobs = 0
    root_db = base_data_dir / "applypilot.db"
    if root_db.exists():
        try:
            conn = sqlite3.connect(str(root_db))
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM jobs")
            user_jobs_count = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM jobs WHERE fit_score IS NOT NULL AND fit_score > 0")
            user_scored_jobs = c.fetchone()[0]
            conn.close()
        except Exception:
            pass

    user_searches_count = 0
    root_searches = base_data_dir / "searches.yaml"
    if root_searches.exists():
        try:
            cfg_s = yaml.safe_load(root_searches.read_text(encoding="utf-8")) or {}
            user_searches_count = len(cfg_s.get("searches", []))
        except Exception:
            pass

    active_prov = (admin_cfg.get("ai_provider") or ("ollama" if os.environ.get("LLM_URL") else "gemini")).lower()
    if active_prov == "openai":
        active_key = os.environ.get("OPENAI_API_KEY", "").strip() or admin_cfg.get("openai_api_key", "").strip()
    elif active_prov in ("groq", "openrouter", "deepseek"):
        active_key = os.environ.get("LLM_API_KEY", "").strip() or admin_cfg.get("llm_api_key", "").strip()
    else:
        active_key = g_key

    ai_daily_limit = 1500 if active_prov == "gemini" else (0 if active_prov == "ollama" else 1000)
    ai_rpm_limit = 15 if active_prov == "gemini" else (30 if active_prov == "groq" else 60)
    ai_tier_label = "Free Tier (Google AI Studio)" if active_prov == "gemini" else ("100% Free & Local" if active_prov == "ollama" else ("Free Tier Cloud" if active_prov == "groq" else "Pay-as-you-go"))
    ai_cost_label = "$0.00 (Free Tier)" if active_prov in ("gemini", "ollama", "groq") else "BYOK"

    ai_usage = {
        "provider": active_prov,
        "provider_title": "Google Gemini" if active_prov == "gemini" else active_prov.title(),
        "tier": ai_tier_label,
        "today_requests": user_scored_jobs,
        "daily_limit": ai_daily_limit,
        "rpm_limit": ai_rpm_limit,
        "cost_est": ai_cost_label,
        "status": "Ready & Connected" if active_key or active_prov == "ollama" else "Pending Configuration"
    }

    wt_data = get_waitlist_and_telemetry(base_data_dir, pg_conn)
    if pg_conn:
        try:
            pg_conn.close()
        except Exception:
            pass

    return {
        "sessions": sessions_list,
        "online_sessions_count": online_sessions_count,
        "total_jobs": total_jobs,
        "user_jobs_count": user_jobs_count,
        "user_scored_jobs": user_scored_jobs,
        "user_searches_count": user_searches_count,
        "waitlist": wt_data["waitlist"],
        "telemetry": wt_data["telemetry"],
        "system": {
            "ai_provider": active_prov,
            "ai_key": active_key,
            "gemini_api_key": active_key if active_prov == "gemini" else g_key,
            "gemini_key_configured": bool(active_key or active_prov == "ollama"),
            "gemini_key_masked": f"{active_key[:6]}••••••••{active_key[-4:]}" if len(active_key) >= 10 else ("Configured" if active_key else "Not Set"),
            "gemini_model": os.environ.get("LLM_MODEL") or admin_cfg.get("llm_model", "gemini-2.0-flash"),
            "ai_usage": ai_usage,
            "proxy_configured": bool(curr_proxy or curr_webshare_key),
            "proxy_masked": proxy_masked,
            "proxy": curr_proxy,
            "webshare_api_key": curr_webshare_key,
            "webshare_api_key_masked": webshare_key_masked,
            "webshare_api_key_set": bool(curr_webshare_key),
            "storage_size_mb": storage_size_mb,
            "runner_status": _SEARCH_RUNNER_STATE.get("status", "idle"),
            "maintenance_mode": admin_cfg.get("maintenance_mode", False),
            "cloud_mode": is_cloud_mode(),
            "server_external_url": str(admin_cfg.get("server_external_url", "")).strip(),
            "auto_run": {
                "enabled": bool(_AUTO_RUN_STATE.get("enabled", False)),
                "interval": str(_AUTO_RUN_STATE.get("interval", "1h")),
                "interval_minutes": int(_AUTO_RUN_STATE.get("interval_minutes", 60)),
                "last_run": float(_AUTO_RUN_STATE.get("last_run", 0.0)),
                "next_run": float(_AUTO_RUN_STATE.get("next_run", 0.0)),
            },
            "notifications": {
                "telegram_enabled": bool(admin_cfg.get("telegram_enabled", False)),
                "telegram_bot_token": str(admin_cfg.get("telegram_bot_token", "")),
                "telegram_bot_token_masked": (
                    f"{admin_cfg.get('telegram_bot_token')[:6]}••••••••{admin_cfg.get('telegram_bot_token')[-4:]}"
                    if len(str(admin_cfg.get("telegram_bot_token", ""))) >= 12
                    else ("Configured" if admin_cfg.get("telegram_bot_token") else "")
                ),
                "telegram_chat_id": str(admin_cfg.get("telegram_chat_id", "")),
                "telegram_min_score": int(admin_cfg.get("telegram_min_score", 0)),
                "telegram_configured": bool(admin_cfg.get("telegram_bot_token") and admin_cfg.get("telegram_chat_id")),
                "whatsapp_enabled": bool(admin_cfg.get("whatsapp_enabled", False)),
                "whatsapp_phone": str(admin_cfg.get("whatsapp_phone", "")),
                "whatsapp_api_key": str(admin_cfg.get("whatsapp_api_key", "")),
                "whatsapp_api_key_masked": (
                    f"{admin_cfg.get('whatsapp_api_key')[:2]}••••••••{admin_cfg.get('whatsapp_api_key')[-2:]}"
                    if len(str(admin_cfg.get("whatsapp_api_key", ""))) >= 5
                    else ("Configured" if admin_cfg.get("whatsapp_api_key") else "")
                ),
                "whatsapp_configured": bool(admin_cfg.get("whatsapp_phone") and admin_cfg.get("whatsapp_api_key")),
            },
        },
        "notifications": {
            "telegram_enabled": bool(admin_cfg.get("telegram_enabled", False)),
            "telegram_bot_token": str(admin_cfg.get("telegram_bot_token", "")),
            "telegram_bot_token_masked": (
                f"{admin_cfg.get('telegram_bot_token')[:6]}••••••••{admin_cfg.get('telegram_bot_token')[-4:]}"
                if len(str(admin_cfg.get("telegram_bot_token", ""))) >= 12
                else ("Configured" if admin_cfg.get("telegram_bot_token") else "")
            ),
            "telegram_chat_id": str(admin_cfg.get("telegram_chat_id", "")),
            "telegram_min_score": int(admin_cfg.get("telegram_min_score", 0)),
            "telegram_configured": bool(admin_cfg.get("telegram_bot_token") and admin_cfg.get("telegram_chat_id")),
            "whatsapp_enabled": bool(admin_cfg.get("whatsapp_enabled", False)),
            "whatsapp_phone": str(admin_cfg.get("whatsapp_phone", "")),
            "whatsapp_api_key": str(admin_cfg.get("whatsapp_api_key", "")),
            "whatsapp_api_key_masked": (
                f"{admin_cfg.get('whatsapp_api_key')[:2]}••••••••{admin_cfg.get('whatsapp_api_key')[-2:]}"
                if len(str(admin_cfg.get("whatsapp_api_key", ""))) >= 5
                else ("Configured" if admin_cfg.get("whatsapp_api_key") else "")
            ),
            "whatsapp_configured": bool(admin_cfg.get("whatsapp_phone") and admin_cfg.get("whatsapp_api_key")),
            "server_external_url": str(admin_cfg.get("server_external_url", "")).strip(),
        },
        "logs": logs_content
    }


def is_cloud_mode() -> bool:
    """Return True when running in public cloud mode (duckduckwork.com / Vercel)."""
    return (
        os.environ.get("VERCEL", "").lower() in ("1", "true")
        or os.environ.get("DUCKDUCKWORK_CLOUD_MODE", "").lower() in ("1", "true")
    )


def get_session_quota(app_dir: Path | None = None) -> dict:
    """Read or initialize free-tier quota state for a session directory."""
    if app_dir is None:
        app_dir = Path(os.environ.get("APPLYPILOT_DIR", str(APP_DIR)))
    q_file = app_dir / "quota.json"
    data = {
        "searches_used": 0,
        "max_free_searches": 2,
        "bonus_granted": False,
        "byok_active": False,
        "tailors_used": 0,
        "max_free_tailors": 3,
        "max_free_tabs": 2,
    }
    if q_file.exists():
        try:
            loaded = json.loads(q_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data.update(loaded)
        except Exception:
            pass

    cloud = is_cloud_mode()
    unlimited = (not cloud) or bool(data.get("byok_active"))
    limit = int(data.get("max_free_searches", 2)) + (1 if data.get("bonus_granted") else 0)
    used = int(data.get("searches_used", 0))
    remaining = 999 if unlimited else max(0, limit - used)

    return {
        "cloud_mode": cloud,
        "unlimited": unlimited,
        "searches_used": used,
        "searches_limit": limit,
        "searches_remaining": remaining,
        "bonus_granted": bool(data.get("bonus_granted")),
        "byok_active": bool(data.get("byok_active")),
        "max_free_tabs": int(data.get("max_free_tabs", 2)),
    }


def update_session_quota(app_dir: Path, **updates) -> dict:
    """Update quota fields in app_dir/quota.json and return computed quota status."""
    app_dir.mkdir(parents=True, exist_ok=True)
    q_file = app_dir / "quota.json"
    raw = {
        "searches_used": 0,
        "max_free_searches": 2,
        "bonus_granted": False,
        "byok_active": False,
        "tailors_used": 0,
        "max_free_tailors": 3,
        "max_free_tabs": 2,
    }
    if q_file.exists():
        try:
            loaded = json.loads(q_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                raw.update(loaded)
        except Exception:
            pass
    raw.update(updates)
    try:
        q_file.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    except Exception:
        pass
    return get_session_quota(app_dir)


def _get_waitlist_file(base_data_dir: Path | None = None) -> Path:
    if base_data_dir is None:
        base_data_dir = Path(os.environ.get("APPLYPILOT_BASE_DIR", os.environ.get("APPLYPILOT_DIR", str(Path.cwd() / ".data"))))
    if base_data_dir.name == "sessions":
        base_data_dir = base_data_dir.parent
    elif "sessions" in base_data_dir.parts:
        idx = base_data_dir.parts.index("sessions")
        base_data_dir = Path(*base_data_dir.parts[:idx])
    base_data_dir.mkdir(parents=True, exist_ok=True)
    return base_data_dir / "waitlist_telemetry.json"


def record_telemetry_event(event_key: str) -> None:
    """Increment a product demand telemetry counter in local storage and Neon Postgres."""
    clean_key = re.sub(r"[^a-zA-Z0-9_]", "", str(event_key or "")).lower()[:64]
    if not clean_key:
        return
    wt_file = _get_waitlist_file()
    store = {"waitlist": [], "telemetry": {}}
    if wt_file.exists():
        try:
            store = json.loads(wt_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    tel = store.setdefault("telemetry", {})
    tel[clean_key] = int(tel.get(clean_key, 0)) + 1
    try:
        wt_file.write_text(json.dumps(store, indent=2), encoding="utf-8")
    except Exception:
        pass

    pg_conn = get_postgres_connection()
    if pg_conn:
        try:
            init_postgres_db(pg_conn)
            pg_conn.run("""
                INSERT INTO duckduckwork_telemetry (event_key, event_count, updated_at)
                VALUES (:ek, 1, CURRENT_TIMESTAMP)
                ON CONFLICT (event_key) DO UPDATE SET
                    event_count = duckduckwork_telemetry.event_count + 1,
                    updated_at = CURRENT_TIMESTAMP
            """, ek=clean_key)
        except Exception:
            pass
        finally:
            try:
                pg_conn.close()
            except Exception:
                pass


def add_waitlist_lead(email: str, target_role: str = "", session_id: str = "", source: str = "pro_modal") -> dict:
    """Record a Pro Early Access waitlist lead in local JSON and Neon PostgreSQL."""
    clean_email = str(email or "").strip().lower()
    if not clean_email or "@" not in clean_email or "." not in clean_email.split("@")[-1]:
        raise ValueError("Please enter a valid email address.")
    clean_role = str(target_role or "").strip()[:120]
    clean_sid = str(session_id or "local").strip()[:64]
    clean_src = str(source or "pro_modal").strip()[:64]
    now_iso = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    wt_file = _get_waitlist_file()
    store = {"waitlist": [], "telemetry": {}}
    if wt_file.exists():
        try:
            store = json.loads(wt_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    leads = store.setdefault("waitlist", [])
    existing = [x for x in leads if str(x.get("email", "")).lower() == clean_email]
    entry = {
        "email": clean_email,
        "target_role": clean_role,
        "session_id": clean_sid,
        "source": clean_src,
        "created_at": now_iso,
    }
    if not existing:
        leads.insert(0, entry)
        tel = store.setdefault("telemetry", {})
        tel["waitlist_signups"] = int(tel.get("waitlist_signups", 0)) + 1
    try:
        wt_file.write_text(json.dumps(store, indent=2), encoding="utf-8")
    except Exception:
        pass

    pg_conn = get_postgres_connection()
    if pg_conn:
        try:
            init_postgres_db(pg_conn)
            pg_conn.run("""
                INSERT INTO duckduckwork_waitlist (email, target_role, session_id, source, created_at)
                VALUES (:em, :tr, :sid, :src, CURRENT_TIMESTAMP)
                ON CONFLICT (email) DO UPDATE SET
                    target_role = COALESCE(NULLIF(:tr, ''), duckduckwork_waitlist.target_role),
                    session_id = :sid
            """, em=clean_email, tr=clean_role, sid=clean_sid, src=clean_src)
            if not existing:
                pg_conn.run("""
                    INSERT INTO duckduckwork_telemetry (event_key, event_count, updated_at)
                    VALUES ('waitlist_signups', 1, CURRENT_TIMESTAMP)
                    ON CONFLICT (event_key) DO UPDATE SET
                        event_count = duckduckwork_telemetry.event_count + 1,
                        updated_at = CURRENT_TIMESTAMP
                """)
        except Exception as pg_err:
            print(f"[Postgres Waitlist Save Notice]: {pg_err}")
        finally:
            try:
                pg_conn.close()
            except Exception:
                pass

    return entry


def get_waitlist_and_telemetry(base_data_dir: Path | None = None, pg_conn=None) -> dict:
    """Read-only retrieval of Pro Waitlist leads and demand telemetry."""
    wt_file = _get_waitlist_file(base_data_dir)
    waitlist: list[dict] = []
    telemetry: dict[str, int] = {
        "paywall_views": 0,
        "waitlist_signups": 0,
        "byok_connected": 0,
        "github_clicks": 0,
        "cloud_searches_run": 0,
    }
    if wt_file.exists():
        try:
            data = json.loads(wt_file.read_text(encoding="utf-8"))
            if isinstance(data.get("waitlist"), list):
                waitlist = data["waitlist"]
            if isinstance(data.get("telemetry"), dict):
                for k, v in data["telemetry"].items():
                    telemetry[k] = int(v or 0)
        except Exception:
            pass

    if pg_conn:
        try:
            rows = pg_conn.run("SELECT email, target_role, session_id, source, TO_CHAR(created_at, 'YYYY-MM-DD HH24:MI UTC') FROM duckduckwork_waitlist ORDER BY created_at DESC LIMIT 200")
            if rows:
                seen_emails = {str(w.get("email", "")).lower() for w in waitlist}
                for r in rows:
                    em = str(r[0] or "").lower()
                    if em and em not in seen_emails:
                        waitlist.append({
                            "email": em,
                            "target_role": r[1] or "",
                            "session_id": r[2] or "",
                            "source": r[3] or "pro_modal",
                            "created_at": r[4] or "",
                        })
                        seen_emails.add(em)
            t_rows = pg_conn.run("SELECT event_key, event_count FROM duckduckwork_telemetry")
            if t_rows:
                for tr in t_rows:
                    ek = str(tr[0] or "")
                    ec = int(tr[1] or 0)
                    if ek:
                        telemetry[ek] = max(telemetry.get(ek, 0), ec)
        except Exception:
            pass

    telemetry["waitlist_signups"] = max(telemetry.get("waitlist_signups", 0), len(waitlist))
    return {"waitlist": waitlist, "telemetry": telemetry}


def get_postgres_connection():
    """Get a pg8000 connection if DATABASE_URL / POSTGRES_URL is set in environment."""
    db_url = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or os.environ.get("POSTGRES_URL_NON_POOLING")
    if not db_url:
        return None

    try:
        import ssl
        import urllib.parse
        import pg8000.native

        parsed = urllib.parse.urlparse(db_url)
        user = urllib.parse.unquote(parsed.username or "")
        password = urllib.parse.unquote(parsed.password or "")
        host = parsed.hostname or "localhost"
        port = parsed.port or 5432
        database = parsed.path.lstrip("/").split("?")[0] if parsed.path else "neondb"
        if not database:
            database = "neondb"

        ssl_ctx = None
        if "sslmode=require" in db_url or "ssl=true" in db_url or (parsed.hostname and "neon.tech" in parsed.hostname):
            ssl_ctx = ssl.create_default_context()

        conn = pg8000.native.Connection(
            user=user,
            password=password,
            host=host,
            port=port,
            database=database,
            ssl_context=ssl_ctx
        )
        return conn
    except Exception as exc:
        print(f"[Postgres Connection Error]: {exc}")
        return None


def init_postgres_db(conn):
    """Create persistent sessions and jobs tables in Neon PostgreSQL if not present."""
    if not conn:
        return
    try:
        conn.run("""
            CREATE TABLE IF NOT EXISTS duckduckwork_sessions (
                session_id VARCHAR(128) PRIMARY KEY,
                searches_yaml TEXT,
                profile_json TEXT,
                resume_txt TEXT,
                is_permanent INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        try:
            conn.run("ALTER TABLE duckduckwork_sessions ADD COLUMN IF NOT EXISTS is_permanent INTEGER DEFAULT 0;")
        except Exception:
            pass
        conn.run("""
            CREATE TABLE IF NOT EXISTS duckduckwork_jobs (
                session_id VARCHAR(128),
                url TEXT,
                title TEXT,
                company TEXT,
                salary TEXT,
                description TEXT,
                location TEXT,
                site TEXT,
                strategy TEXT,
                discovered_at TEXT,
                posted_date TEXT,
                full_description TEXT,
                application_url TEXT,
                detail_scraped_at TEXT,
                detail_error TEXT,
                fit_score INTEGER,
                score_reasoning TEXT,
                scored_at TEXT,
                tailored_resume_path TEXT,
                tailored_at TEXT,
                cover_letter_path TEXT,
                cover_letter_at TEXT,
                applied_at TEXT,
                apply_status TEXT,
                sub_status TEXT,
                apply_error TEXT,
                is_saved INTEGER DEFAULT 0,
                is_archived INTEGER DEFAULT 0,
                notes TEXT,
                search_id TEXT,
                country TEXT,
                work_mode TEXT,
                ai_summary TEXT,
                company_logo TEXT,
                PRIMARY KEY (session_id, url)
            );
        """)
        for col_sql in (
            "ALTER TABLE duckduckwork_jobs ADD COLUMN IF NOT EXISTS ai_summary TEXT;",
            "ALTER TABLE duckduckwork_jobs ADD COLUMN IF NOT EXISTS company_logo TEXT;",
        ):
            try:
                conn.run(col_sql)
            except Exception:
                pass
        conn.run("""
            CREATE TABLE IF NOT EXISTS duckduckwork_waitlist (
                id SERIAL PRIMARY KEY,
                email VARCHAR(255) UNIQUE NOT NULL,
                target_role VARCHAR(255),
                session_id VARCHAR(128),
                source VARCHAR(64) DEFAULT 'pro_modal',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.run("""
            CREATE TABLE IF NOT EXISTS duckduckwork_telemetry (
                event_key VARCHAR(128) PRIMARY KEY,
                event_count INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
    except Exception as exc:
        print(f"[Postgres Init DB Error]: {exc}")


def session_exists_in_cloud(sid: str) -> bool:
    """Check if session_id already exists in Postgres cloud database or local disk."""
    if not sid:
        return False
    base_dir = Path(os.environ.get("APPLYPILOT_BASE_DIR", os.environ.get("APPLYPILOT_DIR", str(APP_DIR))))
    session_dir = base_dir / "sessions" / sid
    alt_dir = base_dir / "sessions" / f"ap_{sid}"
    if (session_dir / "searches.yaml").exists() or (alt_dir / "searches.yaml").exists():
        return True
    pg_conn = get_postgres_connection()
    if pg_conn:
        try:
            s_rows = pg_conn.run("SELECT 1 FROM duckduckwork_sessions WHERE session_id = :sid LIMIT 1", sid=sid)
            if s_rows and len(s_rows) > 0:
                return True
        except Exception:
            pass
    return False


def load_session_from_cloud(sid: str, session_dir: Path) -> bool:
    """Fetch session data from cloud storage (Neon Postgres / Vercel KV / Upstash / Blob) if available."""
    if not sid:
        return False

    pg_conn = get_postgres_connection()
    if pg_conn:
        try:
            init_postgres_db(pg_conn)
            session_dir.mkdir(parents=True, exist_ok=True)

            s_rows = pg_conn.run("SELECT searches_yaml, profile_json, resume_txt FROM duckduckwork_sessions WHERE session_id = :sid", sid=sid)
            if s_rows and len(s_rows) > 0:
                s_yaml, p_json, r_txt = s_rows[0]
                if s_yaml:
                    (session_dir / "searches.yaml").write_text(s_yaml, encoding="utf-8")
                if p_json:
                    (session_dir / "profile.json").write_text(p_json, encoding="utf-8")
                if r_txt:
                    (session_dir / "resume.txt").write_text(r_txt, encoding="utf-8")

            col_names = [
                "url", "title", "company", "salary", "description", "location", "site", "strategy",
                "discovered_at", "posted_date", "full_description", "application_url", "detail_scraped_at",
                "detail_error", "fit_score", "score_reasoning", "ai_summary", "company_logo", "scored_at", "tailored_resume_path",
                "tailored_at", "cover_letter_path", "cover_letter_at", "applied_at", "apply_status",
                "sub_status", "apply_error", "is_saved", "is_archived", "notes", "search_id", "country", "work_mode"
            ]
            sql_cols = ", ".join(col_names)
            j_rows = pg_conn.run(f"SELECT {sql_cols} FROM duckduckwork_jobs WHERE session_id = :sid", sid=sid)

            if j_rows and len(j_rows) > 0:
                db_file = session_dir / "applypilot.db"
                sqlite_conn = sqlite3.connect(str(db_file), timeout=10)
                try:
                    from applypilot.database import ensure_columns, init_db
                    init_db(db_file)
                    ensure_columns(sqlite_conn)

                    cursor = sqlite_conn.cursor()
                    cursor.execute("PRAGMA table_info(jobs)")
                    valid_cols = {row[1] for row in cursor.fetchall()}

                    for row in j_rows:
                        j_dict = dict(zip(col_names, row))
                        if not j_dict.get("url"):
                            continue
                        filtered_job = {k: v for k, v in j_dict.items() if k in valid_cols and k != "url" and v is not None}
                        cols = ["url"] + list(filtered_job.keys())
                        placeholders = ",".join(["?"] * len(cols))
                        updates = ",".join([f"{k}=EXCLUDED.{k}" for k in filtered_job.keys()])

                        vals = [j_dict["url"]] + list(filtered_job.values())
                        sql = f"INSERT INTO jobs ({','.join(cols)}) VALUES ({placeholders}) ON CONFLICT(url) DO UPDATE SET {updates}"
                        cursor.execute(sql, vals)

                    sqlite_conn.commit()
                finally:
                    sqlite_conn.close()

            from applypilot.view import generate_dashboard
            os.environ["APPLYPILOT_DIR"] = str(session_dir.resolve())
            generate_dashboard()
            pg_conn.close()
            return True
        except Exception as exc:
            print(f"[Postgres Session Load Exception]: {exc}")
            if pg_conn:
                try:
                    pg_conn.close()
                except Exception:
                    pass

    kv_url = os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL")
    kv_token = os.environ.get("KV_REST_API_TOKEN") or os.environ.get("UPSTASH_REDIS_REST_TOKEN")
    blob_token = os.environ.get("BLOB_READ_WRITE_TOKEN") or os.environ.get("VERCEL_BLOB_READ_WRITE_TOKEN")

    data_json = None
    if kv_url and kv_token:
        try:
            import urllib.request
            req_url = f"{kv_url.rstrip('/')}/get/session:{sid}"
            req = urllib.request.Request(req_url, headers={"Authorization": f"Bearer {kv_token}"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                result_str = res.get("result")
                if result_str:
                    data_json = json.loads(result_str) if isinstance(result_str, str) else result_str
        except Exception as err:
            print(f"[Cloud Session Restore Error - KV]: {err}")

    if not data_json and blob_token:
        try:
            import urllib.request
            req_url = f"https://blob.vercel-storage.com/sessions/{sid}.json"
            req = urllib.request.Request(req_url, headers={"Authorization": f"Bearer {blob_token}"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data_json = json.loads(resp.read().decode("utf-8"))
        except Exception as err:
            print(f"[Cloud Session Restore Error - Blob]: {err}")

    if not data_json:
        return False

    try:
        session_dir.mkdir(parents=True, exist_ok=True)
        searches_yaml = data_json.get("searches_yaml")
        if searches_yaml and isinstance(searches_yaml, str):
            (session_dir / "searches.yaml").write_text(searches_yaml, encoding="utf-8")

        profile_data = data_json.get("profile")
        if profile_data:
            prof_str = json.dumps(profile_data, indent=2) if isinstance(profile_data, dict) else str(profile_data)
            (session_dir / "profile.json").write_text(prof_str, encoding="utf-8")

        resume_txt = data_json.get("resume_txt")
        if resume_txt and isinstance(resume_txt, str):
            (session_dir / "resume.txt").write_text(resume_txt, encoding="utf-8")

        jobs = data_json.get("jobs", [])
        if jobs and isinstance(jobs, list):
            db_file = session_dir / "applypilot.db"
            conn = sqlite3.connect(str(db_file), timeout=10)
            try:
                from applypilot.database import ensure_columns, init_db
                init_db(db_file)
                ensure_columns(conn)

                cursor = conn.cursor()
                cursor.execute("PRAGMA table_info(jobs)")
                valid_cols = {row[1] for row in cursor.fetchall()}

                for j in jobs:
                    if not isinstance(j, dict) or not j.get("url"):
                        continue
                    filtered_job = {k: v for k, v in j.items() if k in valid_cols and k != "url"}
                    cols = ["url"] + list(filtered_job.keys())
                    placeholders = ",".join(["?"] * len(cols))
                    updates = ",".join([f"{k}=EXCLUDED.{k}" for k in filtered_job.keys()])

                    vals = [j["url"]] + list(filtered_job.values())
                    sql = f"INSERT INTO jobs ({','.join(cols)}) VALUES ({placeholders}) ON CONFLICT(url) DO UPDATE SET {updates}"
                    cursor.execute(sql, vals)

                conn.commit()
            finally:
                conn.close()

        from applypilot.view import generate_dashboard
        os.environ["APPLYPILOT_DIR"] = str(session_dir.resolve())
        generate_dashboard()
        return True
    except Exception as exc:
        print(f"[Cloud Session Unpack Exception]: {exc}")
        return False


def save_session_to_cloud(sid: str | None, session_dir: Path) -> bool:
    """Upload session state to cloud storage (Neon Postgres / Vercel KV / Blob) for cross-instance persistence."""
    if not sid:
        return False

    pg_conn = get_postgres_connection()
    if pg_conn:
        try:
            init_postgres_db(pg_conn)

            searches_yaml = (session_dir / "searches.yaml").read_text(encoding="utf-8") if (session_dir / "searches.yaml").exists() else ""
            profile_json = (session_dir / "profile.json").read_text(encoding="utf-8") if (session_dir / "profile.json").exists() else ""
            resume_txt = (session_dir / "resume.txt").read_text(encoding="utf-8") if (session_dir / "resume.txt").exists() else ""

            pg_conn.run("""
                INSERT INTO duckduckwork_sessions (session_id, searches_yaml, profile_json, resume_txt, updated_at)
                VALUES (:sid, :s_yaml, :p_json, :r_txt, CURRENT_TIMESTAMP)
                ON CONFLICT (session_id) DO UPDATE SET
                    searches_yaml = EXCLUDED.searches_yaml,
                    profile_json = EXCLUDED.profile_json,
                    resume_txt = EXCLUDED.resume_txt,
                    updated_at = CURRENT_TIMESTAMP
            """, sid=sid, s_yaml=searches_yaml, p_json=profile_json, r_txt=resume_txt)

            db_file = session_dir / "applypilot.db"
            if db_file.exists():
                sqlite_conn = sqlite3.connect(str(db_file))
                sqlite_conn.row_factory = sqlite3.Row
                cursor = sqlite_conn.cursor()
                cursor.execute("SELECT * FROM jobs")
                jobs = [dict(r) for r in cursor.fetchall()]
                sqlite_conn.close()

                for j in jobs:
                    if not j.get("url"):
                        continue
                    vals = {
                        "session_id": sid,
                        "url": str(j.get("url") or ""),
                        "title": j.get("title"),
                        "company": j.get("company"),
                        "salary": j.get("salary"),
                        "description": j.get("description"),
                        "location": j.get("location"),
                        "site": j.get("site"),
                        "strategy": j.get("strategy"),
                        "discovered_at": j.get("discovered_at"),
                        "posted_date": j.get("posted_date"),
                        "full_description": j.get("full_description"),
                        "application_url": j.get("application_url"),
                        "detail_scraped_at": j.get("detail_scraped_at"),
                        "detail_error": j.get("detail_error"),
                        "fit_score": j.get("fit_score"),
                        "score_reasoning": j.get("score_reasoning"),
                        "ai_summary": j.get("ai_summary"),
                        "company_logo": j.get("company_logo"),
                        "scored_at": j.get("scored_at"),
                        "tailored_resume_path": j.get("tailored_resume_path"),
                        "tailored_at": j.get("tailored_at"),
                        "cover_letter_path": j.get("cover_letter_path"),
                        "cover_letter_at": j.get("cover_letter_at"),
                        "applied_at": j.get("applied_at"),
                        "apply_status": j.get("apply_status"),
                        "sub_status": j.get("sub_status"),
                        "apply_error": j.get("apply_error"),
                        "is_saved": int(j.get("is_saved") or 0),
                        "is_archived": int(j.get("is_archived") or 0),
                        "notes": j.get("notes"),
                        "search_id": j.get("search_id"),
                        "country": j.get("country"),
                        "work_mode": j.get("work_mode")
                    }

                    pg_conn.run("""
                        INSERT INTO duckduckwork_jobs (
                            session_id, url, title, company, salary, description, location, site, strategy,
                            discovered_at, posted_date, full_description, application_url, detail_scraped_at,
                            detail_error, fit_score, score_reasoning, ai_summary, company_logo, scored_at, tailored_resume_path,
                            tailored_at, cover_letter_path, cover_letter_at, applied_at, apply_status,
                            sub_status, apply_error, is_saved, is_archived, notes, search_id, country, work_mode
                        ) VALUES (
                            :session_id, :url, :title, :company, :salary, :description, :location, :site, :strategy,
                            :discovered_at, :posted_date, :full_description, :application_url, :detail_scraped_at,
                            :detail_error, :fit_score, :score_reasoning, :ai_summary, :company_logo, :scored_at, :tailored_resume_path,
                            :tailored_at, :cover_letter_path, :cover_letter_at, :applied_at, :apply_status,
                            :sub_status, :apply_error, :is_saved, :is_archived, :notes, :search_id, :country, :work_mode
                        ) ON CONFLICT (session_id, url) DO UPDATE SET
                            title = EXCLUDED.title,
                            company = EXCLUDED.company,
                            salary = EXCLUDED.salary,
                            description = EXCLUDED.description,
                            location = EXCLUDED.location,
                            site = EXCLUDED.site,
                            strategy = EXCLUDED.strategy,
                            discovered_at = EXCLUDED.discovered_at,
                            posted_date = EXCLUDED.posted_date,
                            full_description = EXCLUDED.full_description,
                            application_url = EXCLUDED.application_url,
                            detail_scraped_at = EXCLUDED.detail_scraped_at,
                            detail_error = EXCLUDED.detail_error,
                            fit_score = EXCLUDED.fit_score,
                            score_reasoning = EXCLUDED.score_reasoning,
                            ai_summary = EXCLUDED.ai_summary,
                            company_logo = EXCLUDED.company_logo,
                            scored_at = EXCLUDED.scored_at,
                            tailored_resume_path = EXCLUDED.tailored_resume_path,
                            tailored_at = EXCLUDED.tailored_at,
                            cover_letter_path = EXCLUDED.cover_letter_path,
                            cover_letter_at = EXCLUDED.cover_letter_at,
                            applied_at = EXCLUDED.applied_at,
                            apply_status = EXCLUDED.apply_status,
                            sub_status = EXCLUDED.sub_status,
                            apply_error = EXCLUDED.apply_error,
                            is_saved = EXCLUDED.is_saved,
                            is_archived = EXCLUDED.is_archived,
                            notes = EXCLUDED.notes,
                            search_id = EXCLUDED.search_id,
                            country = EXCLUDED.country,
                            work_mode = EXCLUDED.work_mode
                    """, **vals)

            pg_conn.close()
            return True
        except Exception as exc:
            print(f"[Postgres Session Save Exception]: {exc}")
            if pg_conn:
                try:
                    pg_conn.close()
                except Exception:
                    pass

    kv_url = os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL")
    kv_token = os.environ.get("KV_REST_API_TOKEN") or os.environ.get("UPSTASH_REDIS_REST_TOKEN")
    blob_token = os.environ.get("BLOB_READ_WRITE_TOKEN") or os.environ.get("VERCEL_BLOB_READ_WRITE_TOKEN")

    if not (kv_url and kv_token) and not blob_token:
        return False

    try:
        payload = {
            "session_id": sid,
            "searches_yaml": "",
            "profile": None,
            "resume_txt": "",
            "jobs": []
        }

        searches_file = session_dir / "searches.yaml"
        if searches_file.exists():
            payload["searches_yaml"] = searches_file.read_text(encoding="utf-8")

        profile_file = session_dir / "profile.json"
        if profile_file.exists():
            try:
                payload["profile"] = json.loads(profile_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        resume_file = session_dir / "resume.txt"
        if resume_file.exists():
            payload["resume_txt"] = resume_file.read_text(encoding="utf-8")

        db_file = session_dir / "applypilot.db"
        if db_file.exists():
            try:
                conn = sqlite3.connect(str(db_file))
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM jobs")
                payload["jobs"] = [dict(row) for row in cursor.fetchall()]
                conn.close()
            except Exception:
                pass

        payload_bytes = json.dumps(payload).encode("utf-8")

        if kv_url and kv_token:
            import urllib.request
            req_url = f"{kv_url.rstrip('/')}/set/session:{sid}"
            req = urllib.request.Request(
                req_url,
                data=payload_bytes,
                headers={"Authorization": f"Bearer {kv_token}", "Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=10):
                pass

        if blob_token:
            import urllib.request
            req_url = f"https://blob.vercel-storage.com/sessions/{sid}.json"
            req = urllib.request.Request(
                req_url,
                data=payload_bytes,
                headers={"Authorization": f"Bearer {blob_token}", "Content-Type": "application/json", "x-api-version": "1"},
                method="PUT"
            )
            with urllib.request.urlopen(req, timeout=10):
                pass

        return True
    except Exception as err:
        print(f"[Cloud Session Save Exception]: {err}")
        return False


class CRMDashboardHandler(SimpleHTTPRequestHandler):
    """Handler for Dashboard HTML, static PDF assets, search CRUD, and background runner APIs."""

    def __init__(self, *args, **kwargs):
        app_dir = os.environ.get("APPLYPILOT_DIR", str(APP_DIR))
        super().__init__(*args, directory=app_dir, **kwargs)

    def get_session_id(self, post_body_data=None) -> str | None:
        import re
        import urllib.parse
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        sid = None
        if "s" in qs and qs["s"][0]:
            sid = qs["s"][0].strip()
        
        if not sid:
            clean_path = parsed.path.strip("/")
            if clean_path.startswith("s/"):
                parts = clean_path.split("/", 1)
                if len(parts) == 2 and re.match(r'^[a-zA-Z0-9_\-]+$', parts[1]):
                    sid = parts[1]
            elif clean_path and "/" not in clean_path:
                reserved = {"api", "static", "resumes", "logs", "index.html", "dashboard.html", "favicon.ico", "favicon.png", "favicon-96x96.png", "apple-touch-icon.png", "favicon.svg", "robots.txt", "admin"}
                if clean_path not in reserved and re.match(r'^[a-zA-Z0-9_\-]+$', clean_path):
                    sid = clean_path

        if not sid:
            hdr_sid = self.headers.get("X-Session-ID")
            if hdr_sid and re.match(r'^[a-zA-Z0-9_\-]+$', hdr_sid):
                sid = hdr_sid

        if not sid and post_body_data and isinstance(post_body_data, dict):
            p_sid = post_body_data.get("session_id")
            if p_sid and re.match(r'^[a-zA-Z0-9_\-]+$', str(p_sid)):
                sid = str(p_sid)

        if not sid:
            referer = self.headers.get("Referer")
            if referer:
                try:
                    ref_parsed = urllib.parse.urlparse(referer)
                    ref_qs = urllib.parse.parse_qs(ref_parsed.query)
                    if "s" in ref_qs and ref_qs["s"][0]:
                        sid = ref_qs["s"][0].strip()
                    if not sid:
                        ref_path = ref_parsed.path.strip("/")
                        if ref_path.startswith("s/"):
                            ref_parts = ref_path.split("/", 1)
                            if len(ref_parts) == 2 and re.match(r'^[a-zA-Z0-9_\-]+$', ref_parts[1]):
                                sid = ref_parts[1]
                        elif ref_path and "/" not in ref_path:
                            reserved = {"api", "static", "resumes", "logs", "index.html", "dashboard.html", "favicon.ico", "favicon.png", "favicon-96x96.png", "apple-touch-icon.png", "favicon.svg", "robots.txt", "admin"}
                            if ref_path not in reserved and re.match(r'^[a-zA-Z0-9_\-]+$', ref_path):
                                sid = ref_path
                except Exception:
                    pass

        if sid:
            sid = sid.removeprefix("ap_")
            if re.match(r'^[a-zA-Z0-9_\-]+$', sid):
                return sid

        return None

    def get_target_app_dir(self, post_body_data=None) -> Path:
        base_dir = Path(os.environ.get("APPLYPILOT_BASE_DIR", os.environ.get("APPLYPILOT_DIR", str(APP_DIR))))
        # In single admin/user mode, base_dir (.data/) is the primary source of truth
        if (base_dir / "searches.yaml").exists() or (base_dir / "applypilot.db").exists():
            return base_dir

        sid = self.get_session_id(post_body_data)
        if sid:
            session_dir = base_dir / "sessions" / sid
            if (session_dir / "searches.yaml").exists() or (session_dir / "applypilot.db").exists():
                return session_dir

        return base_dir

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "geolocation=(), camera=(), microphone=(), payment=()")
        super().end_headers()

    def _verify_admin_request(self) -> bool:
        # In single user mode (1 admin/user), localhost and local network requests are automatically trusted
        client_ip = self.client_address[0] if hasattr(self, "client_address") and self.client_address else "127.0.0.1"
        host_hdr = self.headers.get("Host", "")
        if client_ip in ("127.0.0.1", "::1", "localhost") or "localhost" in host_hdr or "127.0.0.1" in host_hdr:
            return True

        auth_token = self.headers.get("X-Admin-Token", "")
        csrf_token = self.headers.get("X-CSRF-Token", "")
        cfg = load_admin_config()
        target_pwd = cfg.get("admin_password", "ddw2026")
        if not verify_hmac_token(auth_token, target_pwd):
            return False
        if csrf_token and not verify_csrf_token(csrf_token, target_pwd):
            return False
        return True

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        app_dir = self.get_target_app_dir()
        os.environ["APPLYPILOT_DIR"] = str(app_dir)
        self.directory = str(app_dir)

        clean_path = self.path.split("?")[0]

        # Favicon & Static Assets Handler (immediate, exempt from redirects and maintenance)
        icon_map = {
            "/favicon.ico": ("favicon.ico", "image/x-icon"),
            "/favicon.svg": ("favicon.svg", "image/svg+xml"),
            "/favicon.png": ("favicon.png", "image/png"),
            "/favicon-96x96.png": ("favicon-96x96.png", "image/png"),
            "/favicon-32x32.png": ("favicon-32x32.png", "image/png"),
            "/favicon-16x16.png": ("favicon-16x16.png", "image/png"),
            "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
            "/static/images/favicon.ico": ("favicon.ico", "image/x-icon"),
            "/static/images/favicon.png": ("favicon.png", "image/png"),
            "/static/images/duckduckwork_logo.png": ("duckduckwork_logo.png", "image/png"),
        }
        if clean_path in icon_map:
            fname, mime = icon_map[clean_path]
            from applypilot.config import PACKAGE_DIR
            target_file = PACKAGE_DIR / "static" / "images" / fname
            if not target_file.exists():
                target_file = PACKAGE_DIR / "static" / "images" / "duckduckwork_logo.png"
            if not target_file.exists():
                target_file = PACKAGE_DIR / "static" / "images" / "favicon.png"
            if target_file.exists():
                content = target_file.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                self.wfile.write(content)
                return

        if clean_path.startswith("/static/"):
            rel_path = clean_path[len("/static/"):]
            from applypilot.config import PACKAGE_DIR
            static_file = PACKAGE_DIR / "static" / rel_path
            if static_file.exists():
                content = static_file.read_bytes()
                sfx = static_file.suffix.lower()
                if sfx in (".jpg", ".jpeg"):
                    mime = "image/jpeg"
                elif sfx == ".png":
                    mime = "image/png"
                elif sfx == ".svg":
                    mime = "image/svg+xml"
                elif sfx == ".ico":
                    mime = "image/x-icon"
                elif sfx == ".css":
                    mime = "text/css"
                elif sfx == ".js":
                    mime = "application/javascript"
                else:
                    mime = "application/octet-stream"
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                self.wfile.write(content)
                return

        sid = self.get_session_id()

        if sid:
            _SESSION_ACTIVITY[sid] = time.time()

        # Maintenance Mode Interceptor
        admin_cfg = load_admin_config()
        if admin_cfg.get("maintenance_mode", False):
            if clean_path not in ("/admin", "/admin/") and not clean_path.startswith("/api/admin/") and not clean_path.startswith("/static/") and clean_path not in ("/favicon.ico", "/favicon.svg", "/apple-touch-icon.png", "/robots.txt"):
                from applypilot.view import generate_maintenance_html
                content = generate_maintenance_html().encode("utf-8")
                self.send_response(530)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.end_headers()
                self.wfile.write(content)
                return

        # Deleted Session Interceptor
        is_root_path = clean_path in ("/", "/index.html", "")
        is_admin_path = clean_path in ("/admin", "/admin/") or clean_path.startswith("/api/admin/") or clean_path.startswith("/static/")

        if is_root_path:
            if sid and sid in admin_cfg.get("deleted_sessions", []):
                self.send_header("Set-Cookie", "ddw_sid=; Path=/; Max-Age=0; SameSite=Lax")
        elif sid and sid in admin_cfg.get("deleted_sessions", []) and not is_admin_path:
            if clean_path.startswith("s/") or clean_path == f"s/{sid}" or clean_path == sid:
                from applypilot.view import generate_deleted_session_html
                content = generate_deleted_session_html(sid).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Set-Cookie", "ddw_sid=; Path=/; Max-Age=0; SameSite=Lax")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.send_header("X-Robots-Tag", "noindex, nofollow, noarchive")
                self.end_headers()
                self.wfile.write(content)
                return

        # Serve /robots.txt
        if clean_path == "/robots.txt":
            robots_txt = (
                b"User-agent: *\n"
                b"Allow: /\n"
                b"Disallow: /s/\n"
                b"Disallow: /api/\n"
                b"Disallow: /admin/\n"
                b"Disallow: /resumes/\n"
                b"Disallow: /logs/\n"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(robots_txt)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(robots_txt)
            return

        # Serve /admin Cockpit
        if clean_path in ("/admin", "/admin/"):
            from applypilot.view import generate_admin_html
            content = generate_admin_html().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("X-Robots-Tag", "noindex, nofollow, noarchive")
            self.end_headers()
            self.wfile.write(content)
            return

        # Serve tailored resumes and cover letters
        if "/tailored_resumes/" in clean_path or "/cover_letters/" in clean_path or clean_path.startswith("tailored_resumes/") or clean_path.startswith("cover_letters/"):
            folder = "tailored_resumes" if "tailored_resumes" in clean_path else "cover_letters"
            fname = clean_path.split(f"{folder}/")[-1]
            target_file = app_dir / folder / fname
            if not target_file.exists():
                base_dir = Path(os.environ.get("APPLYPILOT_DIR", str(APP_DIR)))
                target_file = base_dir / folder / fname
            if target_file.exists() and target_file.is_file():
                content = target_file.read_bytes()
                mime = "application/pdf" if target_file.suffix.lower() == ".pdf" else "text/plain; charset=utf-8"
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(content)
                return

        # Analytics API Endpoint (handles both /api/analytics and /s/<session_id>/api/analytics)
        norm_analytics_path = clean_path.rstrip("/")
        if norm_analytics_path == "/api/analytics" or (
            (norm_analytics_path.startswith("/s/") or norm_analytics_path.startswith("s/"))
            and norm_analytics_path.endswith("/api/analytics")
        ):
            import urllib.parse
            from applypilot.analytics import get_analytics_summary

            parsed_url = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed_url.query)
            search_id_param = qs.get("search_id", [None])[0]

            target_db = app_dir / "applypilot.db"
            if norm_analytics_path.startswith("/s/") or norm_analytics_path.startswith("s/"):
                parts = norm_analytics_path.strip("/").split("/")
                if len(parts) >= 4 and parts[0] == "s" and parts[2] == "api" and parts[3] == "analytics":
                    sid_cand = parts[1]
                    base_dir = Path(os.environ.get("APPLYPILOT_BASE_DIR", os.environ.get("APPLYPILOT_DIR", str(APP_DIR))))
                    candidate_db = base_dir / "sessions" / sid_cand / "applypilot.db"
                    if candidate_db.exists():
                        target_db = candidate_db

            if not target_db.exists():
                base_dir = Path(os.environ.get("APPLYPILOT_BASE_DIR", os.environ.get("APPLYPILOT_DIR", str(APP_DIR))))
                if (base_dir / "applypilot.db").exists():
                    target_db = base_dir / "applypilot.db"

            analytics_data = get_analytics_summary(target_db, search_id=search_id_param)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.end_headers()
            self.wfile.write(json.dumps(analytics_data).encode("utf-8"))
            return

        # Serve /analytics HTML View (handles both /analytics and /s/<session_id>/analytics)
        norm_analytics_view_path = clean_path.rstrip("/")
        if norm_analytics_view_path == "/analytics" or (
            (norm_analytics_view_path.startswith("/s/") or norm_analytics_view_path.startswith("s/"))
            and norm_analytics_view_path.endswith("/analytics")
        ):
            from applypilot.view import generate_analytics_html
            sid = None
            if norm_analytics_view_path.startswith("/s/") or norm_analytics_view_path.startswith("s/"):
                parts = norm_analytics_view_path.strip("/").split("/")
                if len(parts) >= 2 and parts[0] == "s":
                    sid = parts[1]
            content = generate_analytics_html(session_id=sid).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.send_header("X-Robots-Tag", "noindex, nofollow, noarchive")
            self.end_headers()
            self.wfile.write(content)
            return

        # Single admin/user mode: redirect legacy /s/<sid> paths to root /
        if clean_path.startswith("/s/") or clean_path.startswith("s/"):
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
            return

        # Serve /onboarding directly
        if clean_path in ("/onboarding", "/onboarding/"):
            from applypilot.view import generate_onboarding_html
            content = generate_onboarding_html().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.end_headers()
            self.wfile.write(content)
            return

        # Route for root "/" serving onboarding (if new user) or the primary dashboard
        if clean_path in ("/", "/index.html", "/dashboard.html", ""):
            if is_new_user(app_dir):
                from applypilot.view import generate_onboarding_html
                content = generate_onboarding_html().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, max-age=0")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
                self.end_headers()
                self.wfile.write(content)
                return

            dash_path = app_dir / "dashboard.html"
            db_path = app_dir / "applypilot.db"

            from applypilot.database import init_db
            from applypilot.view import generate_dashboard

            os.environ["APPLYPILOT_DIR"] = str(app_dir.resolve())
            try:
                init_db(db_path)
                generate_dashboard()
            except Exception as err:
                print(f"[Dashboard Generate Notice]: {err}")

            if dash_path.exists():
                content = dash_path.read_bytes()
            else:
                generate_dashboard()
                content = dash_path.read_bytes()

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.end_headers()
            self.wfile.write(content)
            return

        if clean_path in ("/favicon.ico", "/apple-touch-icon.png", "/favicon.svg", "/favicon.png", "/static/images/favicon.ico", "/static/images/favicon.png", "/static/images/duckduckwork_logo.png"):
            from applypilot.config import PACKAGE_DIR
            target_icon = PACKAGE_DIR / "static" / "images" / "duckduckwork_logo.png"
            if not target_icon.exists():
                target_icon = PACKAGE_DIR / "static" / "images" / "favicon.png"
            if target_icon.exists():
                content = target_icon.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                self.wfile.write(content)
                return

        if clean_path.startswith("/static/"):
            rel_path = clean_path[len("/static/"):]
            from applypilot.config import PACKAGE_DIR
            static_file = PACKAGE_DIR / "static" / rel_path
            if static_file.exists():
                content = static_file.read_bytes()
                sfx = static_file.suffix.lower()
                if sfx in (".jpg", ".jpeg"):
                    mime = "image/jpeg"
                elif sfx == ".png":
                    mime = "image/png"
                elif sfx == ".svg":
                    mime = "image/svg+xml"
                elif sfx == ".ico":
                    mime = "image/x-icon"
                elif sfx == ".css":
                    mime = "text/css"
                elif sfx == ".js":
                    mime = "application/javascript"
                else:
                    mime = "application/octet-stream"
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                self.wfile.write(content)
                return

        if clean_path in ("/api/search/status", "/api/status"):
            with _RUNNER_LOCK:
                st_data = dict(_SEARCH_RUNNER_STATE)
            try:
                profiles = get_search_profiles()
                st_data["profile_last_runs"] = {str(p.get("id")): p.get("last_run") for p in profiles if p.get("id")}
            except Exception:
                st_data["profile_last_runs"] = {}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(json.dumps(st_data).encode("utf-8"))
            return

        if clean_path in ("/api/settings/auto-run", "/api/admin/auto-run"):
            with _AUTO_RUN_LOCK:
                resp_data = dict(_AUTO_RUN_STATE)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, max-age=0")
            self.end_headers()
            self.wfile.write(json.dumps(resp_data).encode("utf-8"))
            return

        if clean_path in ("/api/settings/notifications", "/api/admin/notifications"):
            cfg = load_admin_config()
            tok = str(cfg.get("telegram_bot_token", "")).strip()
            masked = f"{tok[:6]}••••••••{tok[-4:]}" if len(tok) >= 12 else ("Configured" if tok else "")
            wa_key = str(cfg.get("whatsapp_api_key", "")).strip()
            wa_masked = f"{wa_key[:2]}••••••••{wa_key[-2:]}" if len(wa_key) >= 5 else ("Configured" if wa_key else "")
            resp_data = {
                "telegram_enabled": bool(cfg.get("telegram_enabled", False)),
                "telegram_bot_token": tok,
                "telegram_bot_token_masked": masked,
                "telegram_chat_id": str(cfg.get("telegram_chat_id", "")).strip(),
                "telegram_min_score": int(cfg.get("telegram_min_score", 0)),
                "telegram_configured": bool(tok and cfg.get("telegram_chat_id")),
                "whatsapp_enabled": bool(cfg.get("whatsapp_enabled", False)),
                "whatsapp_phone": str(cfg.get("whatsapp_phone", "")).strip(),
                "whatsapp_api_key": wa_key,
                "whatsapp_api_key_masked": wa_masked,
                "whatsapp_configured": bool(cfg.get("whatsapp_phone") and wa_key),
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, max-age=0")
            self.end_headers()
            self.wfile.write(json.dumps(resp_data).encode("utf-8"))
            return

        if clean_path == "/api/searches":
            profiles = get_search_profiles()
            for p in profiles:
                sid = p.get("id", "search_1")
                pdf_p = app_dir / "resumes" / f"{sid}.pdf"
                p["has_pdf_resume"] = pdf_p.exists()
                p["pdf_url"] = f"/resumes/{sid}.pdf" if pdf_p.exists() else None

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"searches": profiles}).encode("utf-8"))
            return

        if clean_path == "/api/quota/status":
            quota = get_session_quota(app_dir)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, max-age=0")
            self.end_headers()
            self.wfile.write(json.dumps(quota).encode("utf-8"))
            return

        if clean_path == "/api/llm/status":
            from applypilot.llm import get_llm_status
            status = get_llm_status()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, max-age=0")
            self.end_headers()
            self.wfile.write(json.dumps(status).encode("utf-8"))
            return

        if clean_path == "/api/candidate/profile":
            p_file = app_dir / "profile.json"
            data = {}
            if p_file.exists():
                try:
                    data = json.loads(p_file.read_text(encoding="utf-8"))
                except Exception:
                    data = {}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode("utf-8"))
            return

        if clean_path == "/api/admin/data":
            if not self._verify_admin_request():
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Unauthorized"}).encode("utf-8"))
                return
            admin_data = get_admin_data()
            cfg = load_admin_config()
            target_pwd = cfg.get("admin_password", "ddw2026")
            admin_data["csrf_token"] = generate_csrf_token(target_pwd)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.end_headers()
            self.wfile.write(json.dumps(admin_data).encode("utf-8"))
            return

        if clean_path in ("/api/system/check-update", "/api/system/version"):
            try:
                from applypilot import __version__
                import urllib.request
                import subprocess

                mock_ver = os.environ.get("DDW_MOCK_LATEST_VERSION", "").strip()
                latest_ver = mock_ver
                release_url = "https://github.com/jorgecerda/duckduckwork/releases"
                release_notes = ""

                if not latest_ver:
                    try:
                        req = urllib.request.Request(
                            "https://api.github.com/repos/jorgecerda/duckduckwork/releases/latest",
                            headers={"User-Agent": "DuckDuckWork-App"}
                        )
                        with urllib.request.urlopen(req, timeout=3) as resp:
                            if resp.status == 200:
                                rel_data = json.loads(resp.read().decode("utf-8"))
                                latest_ver = rel_data.get("tag_name", "").lstrip("v")
                                release_url = rel_data.get("html_url", release_url)
                                release_notes = rel_data.get("body", "")
                    except Exception:
                        pass

                def parse_semver(s: str):
                    clean = re.sub(r'^[^\d]*', '', s.strip())
                    parts = clean.split('.')
                    return tuple(int(re.sub(r'\D.*', '', p)) for p in parts if re.sub(r'\D.*', '', p).isdigit())

                update_available = False
                if latest_ver:
                    try:
                        update_available = parse_semver(latest_ver) > parse_semver(__version__)
                    except Exception:
                        update_available = False

                git_commit = ""
                try:
                    git_commit = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL).decode("utf-8").strip()
                except Exception:
                    pass

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, max-age=0")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "current_version": __version__,
                    "latest_version": latest_ver or __version__,
                    "update_available": update_available,
                    "release_url": release_url,
                    "release_notes": release_notes,
                    "git_commit": git_commit
                }).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return

        if clean_path == "/api/admin/test-gemini":
            if not self._verify_admin_request():
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, max-age=0")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Unauthorized"}).encode("utf-8"))
                return
            try:
                from applypilot.llm import get_client
                client = get_client()
                if client:
                    t_start = time.time()
                    reply = client.ask("Say 'API Connection Verified' in 3 words.")
                    elapsed_ms = int((time.time() - t_start) * 1000)
                    res_msg = f"Gemini API Health Ping SUCCESS! Response: '{reply.strip()}' (Latency: {elapsed_ms}ms, Model: {client.model})"
                    is_ok = True
                else:
                    res_msg = "Gemini API Client not initialized (Check GEMINI_API_KEY environment variable)."
                    is_ok = False
            except Exception as e:
                res_msg = f"Gemini API Error: {e}"
                is_ok = False
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.end_headers()
            self.wfile.write(json.dumps({"success": is_ok, "message": res_msg}).encode("utf-8"))
            return

        if clean_path in ("/api/admin/proxy/test", "/api/admin/test-proxy"):
            if not self._verify_admin_request():
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, max-age=0")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Unauthorized"}).encode("utf-8"))
                return

            try:
                target_key = (os.environ.get("WEBSHARE_API_KEY") or cfg.get("webshare_api_key", "")).strip()
                target_proxy = (os.environ.get("APPLYPILOT_PROXY") or os.environ.get("JOBSPY_PROXY") or os.environ.get("PROXY_URL") or cfg.get("proxy", "")).strip()

                from applypilot.discovery.jobspy import fetch_webshare_info, resolve_webshare_proxy

                ws_info = fetch_webshare_info(api_key=target_key) if target_key else {"valid": False}
                resolved_proxy = resolve_webshare_proxy(api_key=target_key if target_key else None, proxy_override=target_proxy if target_proxy else None)

                if not resolved_proxy and not ws_info.get("valid"):
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, max-age=0")
                    self.send_header("Pragma", "no-cache")
                    self.send_header("Expires", "0")
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": False, "message": "No proxy URL or Webshare API Key configured in environment."}).encode("utf-8"))
                    return

                res_ip = None
                latency_ms = 0
                ping_err = None

                if resolved_proxy:
                    try:
                        import urllib.request
                        if resolved_proxy.get("user") and resolved_proxy.get("pass"):
                            proxy_url = f"http://{resolved_proxy['user']}:{resolved_proxy['pass']}@{resolved_proxy['host']}:{resolved_proxy['port']}"
                        else:
                            proxy_url = f"http://{resolved_proxy['host']}:{resolved_proxy['port']}"

                        proxy_handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
                        opener = urllib.request.build_opener(proxy_handler)

                        t_start = time.time()
                        req = urllib.request.Request("https://api.ipify.org?format=json", headers={"User-Agent": "Mozilla/5.0"})
                        with opener.open(req, timeout=8) as resp:
                            raw = resp.read().decode("utf-8")
                            ip_data = json.loads(raw)
                            res_ip = ip_data.get("ip", "Unknown")
                            latency_ms = int((time.time() - t_start) * 1000)
                    except Exception as pe:
                        ping_err = str(pe)

                msg_parts = []
                if res_ip:
                    msg_parts.append(f"Proxy Connection Active! IP: {res_ip} ({latency_ms}ms)")
                elif ping_err:
                    msg_parts.append(f"Direct Proxy Ping Note: {ping_err}")

                if ws_info.get("valid"):
                    p_cnt = ws_info.get("proxy_count", len(ws_info.get("proxies", [])))
                    countries_str = ", ".join(ws_info.get("countries", []))
                    bw_mb = ws_info.get("stats", {}).get("bandwidth_mb", 0.0)
                    msg_parts.append(f"Webshare API: {p_cnt} active proxies ({countries_str}). Bandwidth used: {bw_mb} MB.")

                full_msg = " | ".join(msg_parts) if msg_parts else "Webshare configuration processed."
                is_success = bool(res_ip or ws_info.get("valid"))

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, max-age=0")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "success": is_success,
                    "ip": res_ip,
                    "latency_ms": latency_ms,
                    "webshare_info": ws_info,
                    "message": full_msg
                }).encode("utf-8"))
                return
            except Exception as outer_err:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, max-age=0")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "message": f"Webshare Test Notice: {outer_err}"}).encode("utf-8"))
                return

        return super().do_GET()

    def do_POST(self):
        import time
        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length)

        post_json = None
        try:
            if post_data:
                post_json = json.loads(post_data.decode("utf-8"))
        except Exception:
            pass

        app_dir = self.get_target_app_dir(post_json)
        os.environ["APPLYPILOT_DIR"] = str(app_dir)
        self.directory = str(app_dir)

        clean_path = self.path.split("?")[0]

        if clean_path == "/api/llm/test":
            try:
                data = post_json or {}
                provider = (data.get("provider") or "gemini").lower()
                api_key = (data.get("api_key") or "").strip()
                model = (data.get("model") or "").strip()
                base_url = (data.get("base_url") or "").strip()

                cfg = load_admin_config()
                if not api_key:
                    if provider == "gemini":
                        api_key = os.environ.get("GEMINI_API_KEY", "").strip() or cfg.get("gemini_api_key", "").strip()
                    elif provider == "openai":
                        api_key = os.environ.get("OPENAI_API_KEY", "").strip() or cfg.get("openai_api_key", "").strip()
                    elif provider in ("groq", "openrouter", "deepseek"):
                        api_key = os.environ.get("LLM_API_KEY", "").strip() or cfg.get("llm_api_key", "").strip()
                    else:
                        api_key = os.environ.get("GEMINI_API_KEY", "").strip() or cfg.get("gemini_api_key", "").strip()

                if provider == "gemini":
                    base_url = base_url or "https://generativelanguage.googleapis.com/v1beta/openai"
                    model = model or "gemini-2.0-flash"
                elif provider == "openai":
                    base_url = base_url or "https://api.openai.com/v1"
                    model = model or "gpt-4o-mini"
                elif provider == "groq":
                    base_url = base_url or "https://api.groq.com/openai/v1"
                    model = model or "llama-3.3-70b-versatile"
                elif provider == "deepseek":
                    base_url = base_url or "https://api.deepseek.com/v1"
                    model = model or "deepseek-chat"
                elif provider == "ollama":
                    base_url = base_url or "http://localhost:11434/v1"
                    model = model or "llama3.2"
                    api_key = api_key or "local-key"
                elif provider == "openrouter":
                    base_url = base_url or "https://openrouter.ai/api/v1"
                    model = model or "anthropic/claude-3.5-sonnet"

                from applypilot.llm import test_llm_credentials
                res = test_llm_credentials(base_url, model, api_key)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps(res).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "message": str(e)}).encode("utf-8"))
                return

        if clean_path == "/api/llm/save":
            try:
                data = post_json or {}
                provider = (data.get("provider") or "gemini").lower()
                api_key = (data.get("api_key") or "").strip()
                model = (data.get("model") or "").strip()
                base_url = (data.get("base_url") or "").strip()

                cfg = load_admin_config()
                if provider == "gemini":
                    cfg["gemini_api_key"] = api_key
                    if model:
                        cfg["llm_model"] = model
                elif provider == "openai":
                    cfg["openai_api_key"] = api_key
                    if model:
                        cfg["llm_model"] = model
                elif provider == "groq":
                    cfg["llm_url"] = "https://api.groq.com/openai/v1"
                    cfg["llm_api_key"] = api_key
                    cfg["llm_model"] = model or "llama-3.3-70b-versatile"
                elif provider == "deepseek":
                    cfg["llm_url"] = "https://api.deepseek.com/v1"
                    cfg["llm_api_key"] = api_key
                    cfg["llm_model"] = model or "deepseek-chat"
                elif provider == "ollama":
                    cfg["llm_url"] = base_url or "http://localhost:11434/v1"
                    cfg["llm_model"] = model or "llama3.2"
                    cfg["llm_api_key"] = "local-key"
                elif provider == "openrouter":
                    cfg["llm_url"] = "https://openrouter.ai/api/v1"
                    cfg["llm_api_key"] = api_key
                    cfg["llm_model"] = model or "meta-llama/llama-3.3-70b-instruct:free"
                else:
                    if base_url:
                        cfg["llm_url"] = base_url
                    if api_key:
                        cfg["llm_api_key"] = api_key
                    if model:
                        cfg["llm_model"] = model

                save_admin_config(cfg)
                if api_key:
                    update_session_quota(app_dir, byok_active=True)
                    record_telemetry_event("byok_connected")
                from applypilot.llm import reset_client, get_llm_status
                reset_client()

                status = get_llm_status()
                quota = get_session_quota(app_dir)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"success": True, "status": status, "quota": quota, "message": "Configuración guardada exitosamente."}).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "message": str(e)}).encode("utf-8"))
                return

        if clean_path == "/api/waitlist/join":
            try:
                data = post_json or {}
                email = (data.get("email") or "").strip()
                target_role = (data.get("target_role") or "").strip()
                source = (data.get("source") or "pro_modal").strip()
                sid = self.get_session_id(data) or app_dir.name.removeprefix("ap_")
                lead = add_waitlist_lead(email=email, target_role=target_role, session_id=sid, source=source)
                quota = update_session_quota(app_dir, bonus_granted=True)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "success": True,
                    "lead": lead,
                    "bonus_granted": True,
                    "quota": quota,
                    "message": "Early Access confirmed! +1 bonus search unlocked."
                }).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode("utf-8"))
                return

        if clean_path == "/api/telemetry/track":
            try:
                data = post_json or {}
                event_key = (data.get("event") or "").strip()
                if event_key:
                    record_telemetry_event(event_key)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"success": True}).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode("utf-8"))
                return

        if clean_path == "/api/system/update":
            try:
                import subprocess
                from pathlib import Path

                repo_dir = Path(__file__).resolve().parent.parent.parent
                git_dir = repo_dir / ".git"

                if not git_dir.exists():
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        "success": False,
                        "error": "This instance was not installed via Git clone. Please update manually using git or pip."
                    }).encode("utf-8"))
                    return

                # Fetch and pull from origin main
                subprocess.run(["git", "-C", str(repo_dir), "fetch", "origin", "main"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
                pull_proc = subprocess.run(["git", "-C", str(repo_dir), "pull", "origin", "main"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)

                if pull_proc.returncode != 0:
                    err = pull_proc.stderr.strip() or pull_proc.stdout.strip()
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        "success": False,
                        "error": f"Git pull failed: {err}"
                    }).encode("utf-8"))
                    return

                # Read updated version
                new_ver = ""
                try:
                    init_f = repo_dir / "src" / "applypilot" / "__init__.py"
                    if init_f.exists():
                        m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', init_f.read_text(encoding="utf-8"))
                        if m:
                            new_ver = m.group(1)
                except Exception:
                    pass

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "success": True,
                    "message": f"Successfully updated DuckDuckWork to {new_ver or 'latest'}!",
                    "output": pull_proc.stdout.strip(),
                    "version": new_ver
                }).encode("utf-8"))
                return
            except subprocess.TimeoutExpired:
                self.send_response(504)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "success": False,
                    "error": "Git operation timed out while communicating with GitHub."
                }).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode("utf-8"))
                return

        if clean_path == "/api/candidate/profile":
            try:
                data = post_json or {}
                p_file = app_dir / "profile.json"
                existing = {}
                if p_file.exists():
                    try:
                        existing = json.loads(p_file.read_text(encoding="utf-8"))
                    except Exception:
                        existing = {}

                for sec in ("personal", "compensation", "availability", "work_authorization", "experience"):
                    if sec in data and isinstance(data[sec], dict):
                        existing.setdefault(sec, {}).update(data[sec])

                p_file.write_text(json.dumps(existing, indent=2), encoding="utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"success": True, "message": "Perfil actualizado correctamente."}).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode("utf-8"))
                return

        if clean_path in ("/api/onboarding/setup", "/api/onboarding/start", "/api/onboarding/search"):
            try:
                data = post_json or {}
                query = (data.get("query") or "").strip()
                if not query:
                    query = "Software Engineer"

                loc_val = (data.get("location") or data.get("country") or "Worldwide").strip()
                work_mode = (data.get("work_mode") or "any").lower()
                candidate_summary = (data.get("candidate_summary") or "").strip()
                provider = (data.get("provider") or "gemini").lower()
                api_key = (data.get("api_key") or "").strip()
                resume_b64 = data.get("resume_b64")
                resume_filename = data.get("resume_filename", "resume.pdf")

                target_dir = self.get_target_app_dir(data)
                if not is_new_user(target_dir):
                    base_dir = Path(os.environ.get("APPLYPILOT_BASE_DIR", os.environ.get("APPLYPILOT_DIR", str(APP_DIR))))
                    req_sid = str(data.get("session_id") or "sandbox").strip()
                    target_dir = base_dir / "sessions" / req_sid
                target_dir.mkdir(parents=True, exist_ok=True)
                os.environ["APPLYPILOT_DIR"] = str(target_dir.resolve())
                self.directory = str(target_dir.resolve())

                # 1. Update admin config with AI settings and onboarded flag
                cfg = load_admin_config()
                cfg["ai_provider"] = provider
                if api_key:
                    if provider == "gemini":
                        cfg["gemini_api_key"] = api_key
                        cfg["llm_model"] = "gemini-2.0-flash"
                    elif provider == "openai":
                        cfg["openai_api_key"] = api_key
                        cfg["llm_model"] = "gpt-4o-mini"
                    elif provider == "openrouter":
                        cfg["llm_url"] = "https://openrouter.ai/api/v1"
                        cfg["llm_api_key"] = api_key
                        cfg["llm_model"] = "anthropic/claude-3.5-sonnet"
                    elif provider == "groq":
                        cfg["llm_url"] = "https://api.groq.com/openai/v1"
                        cfg["llm_api_key"] = api_key
                        cfg["llm_model"] = "llama-3.3-70b-versatile"
                    else:
                        cfg["llm_api_key"] = api_key
                cfg["onboarded"] = True
                save_admin_config(cfg)

                # 2. Update .env file in target_dir if gemini key is provided
                if api_key and provider == "gemini":
                    env_file = target_dir / ".env"
                    try:
                        lines = []
                        if env_file.exists():
                            lines = [line for line in env_file.read_text(encoding="utf-8").splitlines() if not line.startswith("GEMINI_API_KEY=")]
                        lines.append(f"GEMINI_API_KEY={api_key}")
                        env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
                        os.environ["GEMINI_API_KEY"] = api_key
                    except Exception as env_err:
                        print(f"[Onboarding Env Write Warning]: {env_err}")

                try:
                    from applypilot.llm import reset_client
                    reset_client()
                except Exception:
                    pass

                # 3. Ensure database is initialized
                from applypilot.database import init_db
                init_db(target_dir / "applypilot.db")

                # Handle uploaded CV / Resume PDF
                has_pdf = False
                if resume_b64:
                    try:
                        resumes_dir = target_dir / "resumes"
                        resumes_dir.mkdir(parents=True, exist_ok=True)
                        pdf_path = resumes_dir / "search_1.pdf"
                        txt_path = resumes_dir / "search_1.txt"
                        raw_pdf_bytes = base64.b64decode(resume_b64)
                        pdf_path.write_bytes(raw_pdf_bytes)
                        (target_dir / "resume.pdf").write_bytes(raw_pdf_bytes)
                        
                        extracted_txt = extract_pdf_text(pdf_path, txt_path)
                        if extracted_txt:
                            (target_dir / "resume.txt").write_text(extracted_txt, encoding="utf-8")
                            if not candidate_summary:
                                # Auto-extract first non-empty lines as preliminary candidate summary
                                non_empty = [line.strip() for line in extracted_txt.splitlines() if line.strip() and len(line.strip()) > 15][:4]
                                candidate_summary = " ".join(non_empty)[:350]
                        has_pdf = True
                    except Exception as cv_err:
                        print(f"[Onboarding CV Processing Warning]: {cv_err}")

                # 4. Save Candidate Profile
                cand_profile = {
                    "personal": {
                        "full_name": "Candidate",
                        "summary": candidate_summary
                    },
                    "experience": {
                        "summary": candidate_summary
                    },
                    "has_pdf_resume": has_pdf,
                    "resume_filename": resume_filename if has_pdf else None
                }
                p_file = target_dir / "profile.json"
                try:
                    p_file.write_text(json.dumps(cand_profile, indent=2), encoding="utf-8")
                except Exception as p_err:
                    print(f"[Onboarding Profile Save Warning]: {p_err}")

                # 5. Build first search profile
                is_rem = (work_mode == "remote")
                search_name = query if loc_val == "Worldwide" else f"{query} - {loc_val}"
                profile = {
                    "id": "search_1",
                    "name": search_name,
                    "queries": [query],
                    "locations": [loc_val],
                    "job_types": [],
                    "sites": ["linkedin", "indeed", "glassdoor", "remoteok", "getonboard"],
                    "hours_old": 24,
                    "results_wanted": 10,
                    "results_per_site": 10,
                    "quick_scan": True,
                    "is_remote": is_rem,
                    "strategy": "easy_apply",
                    "has_pdf_resume": has_pdf,
                    "resume_filename": resume_filename if has_pdf else None,
                    "pdf_url": "/resumes/search_1.pdf" if has_pdf else None,
                    "candidate": cand_profile
                }
                full_cfg = {"searches": [profile]}
                save_search_config(full_cfg)
                if is_cloud_mode():
                    update_session_quota(target_dir, searches_used=1, byok_active=bool(api_key))
                    record_telemetry_event("cloud_searches_run")

                # 6. Generate dashboard
                from applypilot.view import generate_dashboard
                generate_dashboard()

                # 7. Start search background runner
                with _RUNNER_LOCK:
                    _SEARCH_RUNNER_STATE["status"] = "scraping"
                    _SEARCH_RUNNER_STATE["message"] = "Searching job listings across enabled sources..."
                    _SEARCH_RUNNER_STATE["search_id"] = "search_1"

                def _bg_run():
                    try:
                        _run_search_background_task("search_1", skip_scoring=False, target_app_dir=target_dir)
                    except Exception as r_err:
                        print(f"[Onboarding Search Run Warning]: {r_err}")

                t = threading.Thread(target=_bg_run, daemon=True)
                t.start()

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "success": True,
                    "redirect": "/?search=search_1",
                    "message": "Onboarding completed and search started"
                }).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode("utf-8"))
                return

        if clean_path in ("/api/status", "/api/job/status", "/api/job/save", "/api/job/archive"):
            try:
                data = json.loads(post_data.decode("utf-8"))
                url = data.get("url")

                if url:
                    import html
                    url = html.unescape(str(url))
                    target_dir = self.get_target_app_dir(data)
                    os.environ["APPLYPILOT_DIR"] = str(target_dir.resolve())
                    db_file = target_dir / "applypilot.db"
                    conn = sqlite3.connect(str(db_file), timeout=10)
                    try:
                        from applypilot.database import ensure_columns
                        ensure_columns(conn)
                    except Exception:
                        pass

                    if "is_saved" in data or "saved" in data:
                        is_saved_val = 1 if (data.get("is_saved") if "is_saved" in data else data.get("saved")) else 0
                        conn.execute("UPDATE jobs SET is_saved = ? WHERE url = ?", (is_saved_val, url))

                    if "is_archived" in data or "archived" in data:
                        is_archived_val = 1 if (data.get("is_archived") if "is_archived" in data else data.get("archived")) else 0
                        conn.execute("UPDATE jobs SET is_archived = ? WHERE url = ?", (is_archived_val, url))

                    if "notes" in data:
                        from applypilot.database import encrypt_sensitive
                        raw_notes_val = str(data.get("notes", "") or "")
                        notes_val = encrypt_sensitive(raw_notes_val)
                        conn.execute("UPDATE jobs SET notes = ? WHERE url = ?", (notes_val, url))

                    if "sub_status" in data:
                        sub_status_val = str(data.get("sub_status", "") or "")
                        conn.execute("UPDATE jobs SET sub_status = ? WHERE url = ?", (sub_status_val, url))
                        if sub_status_val.lower() == "applied":
                            applied_at = datetime.now().strftime("%Y-%m-%d")
                            conn.execute("UPDATE jobs SET apply_status = 'applied', applied_at = COALESCE(applied_at, ?) WHERE url = ?", (applied_at, url))
                        elif sub_status_val.lower() == "saved":
                            conn.execute("UPDATE jobs SET is_saved = 1 WHERE url = ?", (url,))
                        elif sub_status_val.lower() == "archived":
                            conn.execute("UPDATE jobs SET is_archived = 1 WHERE url = ?", (url,))

                    if "status" in data:
                        status = data.get("status")
                        applied_at = data.get("applied_at")
                        if status == "applied":
                            if not applied_at:
                                applied_at = datetime.now().strftime("%Y-%m-%d")
                            conn.execute("UPDATE jobs SET apply_status = 'applied', applied_at = ?, sub_status = COALESCE(sub_status, 'Applied') WHERE url = ?", (applied_at, url))
                        elif status == "archived":
                            conn.execute("UPDATE jobs SET is_archived = 1 WHERE url = ?", (url,))
                        elif status == "unarchived":
                            conn.execute("UPDATE jobs SET is_archived = 0 WHERE url = ?", (url,))
                        elif status in ("new", "unapplied"):
                            conn.execute("UPDATE jobs SET apply_status = NULL, applied_at = NULL WHERE url = ?", (url,))

                    conn.commit()
                    conn.close()

                    try:
                        from applypilot.view import generate_dashboard
                        generate_dashboard()
                    except Exception as gen_err:
                        print("Dashboard regeneration notice:", gen_err)

                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": True}).encode("utf-8"))
                    return
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return

        if clean_path == "/api/application/prepare":
            try:
                data = post_json or {}
                url = data.get("url")
                if not url:
                    raise ValueError("Job URL is required")

                target_dir = self.get_target_app_dir(data)
                os.environ["APPLYPILOT_DIR"] = str(target_dir.resolve())
                db_file = target_dir / "applypilot.db"
                conn = sqlite3.connect(str(db_file), timeout=10)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                row = cur.execute("SELECT * FROM jobs WHERE url = ?", (url,)).fetchone()
                job = dict(row) if row else {}
                conn.close()

                from applypilot.config import load_profile
                profile = load_profile(job.get("search_id"))
                personal = profile.get("personal", {})
                experience = profile.get("experience", {})
                resume_facts = profile.get("resume_facts", {})
                comp = profile.get("compensation", {})
                avail = profile.get("availability", {})
                work_auth = profile.get("work_authorization", {})

                # Check existing tailored resume / cover letter
                tailored_resume_text = None
                resume_pdf_url = None
                if job.get("tailored_resume_path"):
                    p = Path(job["tailored_resume_path"])
                    if p.exists():
                        tailored_resume_text = p.read_text(encoding="utf-8")
                        pdf_candidate = p.with_suffix(".pdf")
                        if pdf_candidate.exists():
                            sid_val = self.get_session_id(data)
                            resume_pdf_url = f"/s/{sid_val}/tailored_resumes/{pdf_candidate.name}" if sid_val else f"/tailored_resumes/{pdf_candidate.name}"

                cover_letter_text = None
                cover_pdf_url = None
                if job.get("cover_letter_path"):
                    p = Path(job["cover_letter_path"])
                    if p.exists():
                        cover_letter_text = p.read_text(encoding="utf-8")
                        pdf_candidate = p.with_suffix(".pdf")
                        if pdf_candidate.exists():
                            sid_val = self.get_session_id(data)
                            cover_pdf_url = f"/s/{sid_val}/cover_letters/{pdf_candidate.name}" if sid_val else f"/cover_letters/{pdf_candidate.name}"

                # Parse verification report if stored
                verification_report = None
                if job.get("verification_report"):
                    try:
                        verification_report = json.loads(job["verification_report"])
                    except Exception:
                        pass

                company_name = job.get("company") or "the company"
                role_title = job.get("title") or "this role"
                why_answer = (
                    f"I am eager to contribute as {role_title} at {company_name}. My background aligns directly "
                    f"with the technical and leadership requirements of this position, and I am excited to apply my experience "
                    f"to drive impactful outcomes for your team."
                )

                salary_expectation = str(comp.get("target_salary") or job.get("salary") or "Competitive / Open to negotiation")
                notice_period = str(avail.get("notice_period") or "Immediately available / 2 weeks")
                auth_str = str(work_auth.get("status") or "Remote LATAM / Eligible to work remotely")

                resp_payload = {
                    "success": True,
                    "job": {
                        "url": job.get("url"),
                        "title": job.get("title"),
                        "company": job.get("company"),
                        "application_url": job.get("application_url") or job.get("url"),
                        "location": job.get("location"),
                        "salary": job.get("salary"),
                        "fit_score": job.get("fit_score"),
                        "tailored_resume_text": tailored_resume_text,
                        "resume_pdf_url": resume_pdf_url,
                        "cover_letter_text": cover_letter_text,
                        "cover_pdf_url": cover_pdf_url,
                        "verification_status": job.get("verification_status") or ("verified" if tailored_resume_text else None),
                        "verification_confidence": job.get("verification_confidence") or ("HIGH" if tailored_resume_text else None),
                        "verification_report": verification_report,
                    },
                    "candidate": {
                        "full_name": personal.get("full_name") or personal.get("preferred_name") or "",
                        "preferred_name": personal.get("preferred_name") or "",
                        "email": personal.get("email") or "",
                        "phone": personal.get("phone") or "",
                        "location": personal.get("country") or personal.get("location") or "",
                        "linkedin_url": personal.get("linkedin_url") or "",
                        "github_url": personal.get("github_url") or "",
                        "portfolio_url": personal.get("portfolio_url") or "",
                        "work_authorization": auth_str,
                        "years_experience": experience.get("years_experience") or "10+ years",
                        "education": str(experience.get("education_level") or "") + " - " + str(resume_facts.get("preserved_school") or ""),
                    },
                    "common_answers": {
                        "why_role": why_answer,
                        "salary_expectation": salary_expectation,
                        "notice_period": notice_period,
                        "work_authorization": auth_str,
                    }
                }

                try:
                    from applypilot.scoring.outreach import generate_outreach_strategies
                    resp_payload["outreach"] = generate_outreach_strategies(job, profile, use_llm=False)
                except Exception as e_outreach:
                    print(f"[Outreach Notice]: {e_outreach}")
                    resp_payload["outreach"] = None

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps(resp_payload).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode("utf-8"))
                return

        if clean_path == "/api/application/tailor":
            try:
                data = post_json or {}
                url = data.get("url")
                asset_type = data.get("asset_type", "both")
                if not url:
                    raise ValueError("Job URL is required")

                target_dir = self.get_target_app_dir(data)
                os.environ["APPLYPILOT_DIR"] = str(target_dir.resolve())
                db_file = target_dir / "applypilot.db"
                conn = sqlite3.connect(str(db_file), timeout=10)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                row = cur.execute("SELECT * FROM jobs WHERE url = ?", (url,)).fetchone()
                if not row:
                    raise ValueError(f"Job not found for URL: {url}")
                job = dict(row)

                from applypilot.config import load_profile
                from applypilot.scoring.fact_checker import verify_facts
                profile = load_profile(job.get("search_id"))

                # Get base resume text
                search_id = job.get("search_id")
                resume_text = ""
                if search_id:
                    sr_path = target_dir / "resumes" / f"{search_id}.txt"
                    if sr_path.exists():
                        resume_text = sr_path.read_text(encoding="utf-8").strip()
                if not resume_text:
                    mr_path = target_dir / "resume.txt"
                    if mr_path.exists():
                        resume_text = mr_path.read_text(encoding="utf-8").strip()
                if not resume_text:
                    from applypilot.config import RESUME_PATH
                    if RESUME_PATH.exists():
                        resume_text = RESUME_PATH.read_text(encoding="utf-8").strip()

                sid_val = self.get_session_id(data)
                now_iso = datetime.now(UTC).isoformat()
                tailored_dir = target_dir / "tailored_resumes"
                tailored_dir.mkdir(parents=True, exist_ok=True)
                cl_dir = target_dir / "cover_letters"
                cl_dir.mkdir(parents=True, exist_ok=True)

                safe_title = re.sub(r"[^\w\s-]", "", job.get("title", "role"))[:50].strip().replace(" ", "_")
                safe_site = re.sub(r"[^\w\s-]", "", job.get("site", "job"))[:20].strip().replace(" ", "_")
                prefix = f"{safe_site}_{safe_title}"

                tailored_resume_text = None
                resume_pdf_url = None
                cover_letter_text = None
                cover_pdf_url = None
                fact_report = None

                # Generate Resume if requested
                if asset_type in ("resume", "both"):
                    from applypilot.scoring.tailor import tailor_resume
                    tailored_resume_text, tailor_report = tailor_resume(
                        resume_text=resume_text,
                        job=job,
                        profile=profile,
                        original_text=resume_text,
                        validation_mode="normal",
                    )
                    fact_report = verify_facts(tailored_resume_text, profile, doc_type="resume", original_text=resume_text)
                    txt_path = tailored_dir / f"{prefix}.txt"
                    txt_path.write_text(tailored_resume_text, encoding="utf-8")

                    pdf_path = None
                    try:
                        from applypilot.scoring.pdf import convert_to_pdf
                        pdf_path = convert_to_pdf(txt_path)
                        resume_pdf_url = f"/s/{sid_val}/tailored_resumes/{Path(pdf_path).name}" if sid_val else f"/tailored_resumes/{Path(pdf_path).name}"
                    except Exception as pe:
                        print("PDF conversion notice:", pe)

                    conn.execute("""
                        UPDATE jobs 
                        SET tailored_resume_path = ?, tailored_at = ?,
                            verification_confidence = ?, verification_status = ?, verification_report = ?
                        WHERE url = ?
                    """, (
                        str(txt_path), now_iso,
                        fact_report["confidence_level"],
                        "verified" if fact_report["verified"] else "flagged",
                        json.dumps(fact_report),
                        url,
                    ))
                    conn.commit()

                # Generate Cover Letter if requested
                if asset_type in ("cover_letter", "both"):
                    from applypilot.scoring.cover_letter import generate_cover_letter
                    cover_letter_text = generate_cover_letter(
                        resume_text=resume_text,
                        job=job,
                        profile=profile,
                        validation_mode="normal",
                    )
                    cl_fact_report = verify_facts(cover_letter_text, profile, doc_type="cover_letter", original_text=resume_text)
                    if not fact_report:
                        fact_report = cl_fact_report

                    cl_path = cl_dir / f"{prefix}_CL.txt"
                    cl_path.write_text(cover_letter_text, encoding="utf-8")

                    try:
                        from applypilot.scoring.pdf import convert_to_pdf
                        cl_pdf_path = convert_to_pdf(cl_path)
                        cover_pdf_url = f"/s/{sid_val}/cover_letters/{Path(cl_pdf_path).name}" if sid_val else f"/cover_letters/{Path(cl_pdf_path).name}"
                    except Exception as pe:
                        print("Cover letter PDF conversion notice:", pe)

                    conn.execute("""
                        UPDATE jobs 
                        SET cover_letter_path = ?, cover_letter_at = ?
                        WHERE url = ?
                    """, (str(cl_path), now_iso, url))
                    conn.commit()

                conn.close()

                try:
                    from applypilot.view import generate_dashboard
                    generate_dashboard()
                except Exception as g_err:
                    print("Dashboard regen notice:", g_err)

                resp_payload = {
                    "success": True,
                    "tailored_resume_text": tailored_resume_text,
                    "resume_pdf_url": resume_pdf_url,
                    "cover_letter_text": cover_letter_text,
                    "cover_pdf_url": cover_pdf_url,
                    "verification": fact_report,
                }

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps(resp_payload).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode("utf-8"))
                return

        if clean_path == "/api/outreach/generate":
            try:
                data = post_json or {}
                url = data.get("url")
                use_llm = bool(data.get("use_llm", True))
                if not url:
                    raise ValueError("Job URL is required")

                target_dir = self.get_target_app_dir(data)
                os.environ["APPLYPILOT_DIR"] = str(target_dir.resolve())
                db_file = target_dir / "applypilot.db"
                conn = sqlite3.connect(str(db_file), timeout=10)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                row = cur.execute("SELECT * FROM jobs WHERE url = ?", (url,)).fetchone()
                job = dict(row) if row else {}
                conn.close()

                from applypilot.config import load_profile
                from applypilot.scoring.outreach import generate_outreach_strategies
                profile = load_profile(job.get("search_id"))
                outreach_data = generate_outreach_strategies(job, profile, use_llm=use_llm)

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"success": True, "outreach": outreach_data}).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode("utf-8"))
                return

        if clean_path == "/api/ats/ingest":
            try:
                data = post_json or {}
                url = data.get("url")
                board_token = data.get("board_token")
                ats_type = data.get("ats_type")
                target_location = data.get("location")
                max_jobs = int(data.get("max_jobs", 100))

                target_dir = self.get_target_app_dir(data)
                os.environ["APPLYPILOT_DIR"] = str(target_dir.resolve())
                db_file = target_dir / "applypilot.db"
                conn = sqlite3.connect(str(db_file), timeout=10)

                from applypilot.discovery.providers.ats_providers import (
                    fetch_greenhouse_jobs,
                    fetch_ashby_jobs,
                    fetch_lever_jobs,
                    fetch_ats_jobs_from_url,
                )

                if url:
                    res = fetch_ats_jobs_from_url(url, target_location=target_location, max_jobs=max_jobs, conn=conn)
                elif board_token and ats_type:
                    ats_type = str(ats_type).lower()
                    if ats_type == "greenhouse":
                        res = fetch_greenhouse_jobs(board_token, target_location=target_location, max_jobs=max_jobs, conn=conn)
                    elif ats_type == "ashby":
                        res = fetch_ashby_jobs(board_token, target_location=target_location, max_jobs=max_jobs, conn=conn)
                    elif ats_type == "lever":
                        res = fetch_lever_jobs(board_token, target_location=target_location, max_jobs=max_jobs, conn=conn)
                    else:
                        raise ValueError(f"Unsupported ATS type: {ats_type}")
                else:
                    raise ValueError("Either 'url' or both 'ats_type' and 'board_token' must be provided.")

                conn.close()

                try:
                    from applypilot.view import generate_dashboard
                    generate_dashboard()
                except Exception as g_err:
                    print("Dashboard regen notice:", g_err)

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps(res).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode("utf-8"))
                return

        if clean_path == "/api/jobs/archive-all":
            try:
                data = json.loads(post_data.decode("utf-8"))
                search_id = data.get("search_id")
                urls = data.get("urls")

                target_dir = self.get_target_app_dir(data)
                os.environ["APPLYPILOT_DIR"] = str(target_dir.resolve())
                db_file = target_dir / "applypilot.db"
                conn = sqlite3.connect(str(db_file), timeout=10)

                if urls and isinstance(urls, list) and len(urls) > 0:
                    placeholders = ",".join(["?"] * len(urls))
                    conn.execute(f"UPDATE jobs SET is_archived = 1 WHERE url IN ({placeholders})", urls)
                elif search_id and search_id != "all":
                    conn.execute("UPDATE jobs SET is_archived = 1 WHERE search_id = ?", (search_id,))
                else:
                    conn.execute("UPDATE jobs SET is_archived = 1 WHERE is_archived = 0")

                conn.commit()
                conn.close()

                try:
                    from applypilot.view import generate_dashboard
                    generate_dashboard()
                except Exception as gen_err:
                    print("Dashboard regeneration notice:", gen_err)

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"success": True}).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return

        if clean_path == "/api/searches/reorder":
            try:
                data = json.loads(post_data.decode("utf-8"))
                new_order = data.get("order")
                if new_order and isinstance(new_order, list):
                    cfg = load_search_config()
                    searches = cfg.get("searches", [])
                    if isinstance(searches, list) and len(searches) > 0:
                        order_map = {sid: idx for idx, sid in enumerate(new_order)}
                        searches.sort(key=lambda s: order_map.get(s.get("id", "search_1"), 999))
                        cfg["searches"] = searches
                        save_search_config(cfg)
                        try:
                            from applypilot.view import generate_dashboard
                            generate_dashboard()
                        except Exception as gen_err:
                            print("Dashboard regeneration notice:", gen_err)

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"success": True}).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return

        if clean_path == "/api/searches":
            try:
                import urllib.parse
                data = json.loads(post_data.decode("utf-8"))
                profile = data.get("search")
                if not profile:
                    raise ValueError("Missing 'search' object")

                if isinstance(profile.get("name"), str):
                    profile["name"] = urllib.parse.unquote(profile["name"])
                if isinstance(profile.get("queries"), list):
                    profile["queries"] = [urllib.parse.unquote(q) if isinstance(q, str) else q for q in profile["queries"]]
                if isinstance(profile.get("locations"), list):
                    profile["locations"] = [urllib.parse.unquote(loc) if isinstance(loc, str) else loc for loc in profile["locations"]]
                if isinstance(profile.get("job_types"), list):
                    profile["job_types"] = [urllib.parse.unquote(t) if isinstance(t, str) else t for t in profile["job_types"]]
                if isinstance(profile.get("blocked_posters"), list):
                    profile["blocked_posters"] = [urllib.parse.unquote(b) if isinstance(b, str) else b for b in profile["blocked_posters"]]

                all_profiles = get_search_profiles()
                existing_idx = next((i for i, p in enumerate(all_profiles) if p.get("id") == profile.get("id")), -1)

                quota = get_session_quota(app_dir)
                if quota["cloud_mode"] and not quota["unlimited"]:
                    if existing_idx < 0 and len(all_profiles) >= quota["max_free_tabs"]:
                        record_telemetry_event("paywall_views")
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "success": False,
                            "quota_exceeded": True,
                            "reason": "max_tabs",
                            "quota": quota,
                            "error": f"Free cloud tier includes up to {quota['max_free_tabs']} search tabs. Join Pro Early Access, connect your own free API key (BYOK), or run locally for unlimited tabs."
                        }).encode("utf-8"))
                        return
                    profile["results_per_site"] = min(int(profile.get("results_per_site", 12) or 12), 12)
                    profile["results_wanted"] = min(int(profile.get("results_wanted", 12) or 12), 12)

                if existing_idx >= 0:
                    all_profiles[existing_idx] = profile
                else:
                    if not profile.get("id"):
                        profile["id"] = f"search_{int(time.time())}"
                    all_profiles.append(profile)

                full_cfg = load_search_config()
                full_cfg["searches"] = all_profiles
                save_search_config(full_cfg)

                # Regenerate dashboard to update UI
                try:
                    from applypilot.view import generate_dashboard
                    generate_dashboard()
                except Exception:
                    pass

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": True, "search": profile}).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return

        if clean_path == "/api/searches/delete":
            try:
                data = json.loads(post_data.decode("utf-8"))
                target_id = data.get("id")
                all_profiles = get_search_profiles()
                filtered = [p for p in all_profiles if p.get("id") != target_id]

                full_cfg = load_search_config()
                full_cfg["searches"] = filtered
                save_search_config(full_cfg)

                try:
                    from applypilot.view import generate_dashboard
                    generate_dashboard()
                except Exception:
                    pass

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": True}).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return

        if clean_path == "/api/searches/upload-resume":
            try:
                data = json.loads(post_data.decode("utf-8"))
                target_id = data.get("id")
                b64_content = data.get("file_b64")
                filename = data.get("filename", f"{target_id}.pdf")

                if not target_id or not b64_content:
                    raise ValueError("Missing search id or file content")

                app_dir = Path(os.environ.get("APPLYPILOT_DIR", str(APP_DIR)))
                resumes_dir = app_dir / "resumes"
                resumes_dir.mkdir(parents=True, exist_ok=True)

                pdf_path = resumes_dir / f"{target_id}.pdf"
                txt_path = resumes_dir / f"{target_id}.txt"

                pdf_bytes = base64.b64decode(b64_content)
                pdf_path.write_bytes(pdf_bytes)

                # Parse PDF to TXT
                extracted_txt = extract_pdf_text(pdf_path, txt_path)

                # Update searches.yaml reference
                all_profiles = get_search_profiles()
                for p in all_profiles:
                    if p.get("id") == target_id:
                        p["resume_filename"] = filename

                full_cfg = load_search_config()
                full_cfg["searches"] = all_profiles
                save_search_config(full_cfg)

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "success": True,
                    "pdf_url": f"/resumes/{target_id}.pdf",
                    "text_chars": len(extracted_txt),
                }).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return

        if clean_path == "/api/search/run":
            try:
                data = json.loads(post_data.decode("utf-8")) if post_data else {}
                target_id = data.get("id")

                quota = get_session_quota(app_dir)
                if quota["cloud_mode"] and not quota["unlimited"]:
                    if quota["searches_remaining"] <= 0:
                        record_telemetry_event("paywall_views")
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "success": False,
                            "quota_exceeded": True,
                            "reason": "max_searches",
                            "quota": quota,
                            "error": "Free cloud search limit reached. Join Pro Early Access (+1 bonus search), connect your own free API key (BYOK), or download the local open-source version."
                        }).encode("utf-8"))
                        return

                with _RUNNER_LOCK:
                    if _SEARCH_RUNNER_STATE["status"] in ("scraping", "scoring"):
                        self.send_response(409)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({"error": "Search already in progress"}).encode("utf-8"))
                        return

                if quota["cloud_mode"]:
                    quota = update_session_quota(app_dir, searches_used=quota["searches_used"] + 1)
                    record_telemetry_event("cloud_searches_run")

                is_quick = False
                if target_id and target_id != "all":
                    sp_match = [p for p in get_search_profiles() if p.get("id") == target_id]
                    if sp_match and sp_match[0].get("quick_scan"):
                        is_quick = True

                if os.environ.get("VERCEL"):
                    _run_search_background_task(target_id, is_quick, app_dir)
                else:
                    t = threading.Thread(target=_run_search_background_task, args=(target_id, is_quick, app_dir), daemon=True)
                    t.start()

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": True, "message": "Search runner started", "quota": quota}).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return

        if clean_path == "/api/user/delete-all-data":
            try:
                data = post_json or {}
                sid = data.get("session_id") or self.get_session_id(data)
                if sid:
                    cfg = load_admin_config()
                    dels = cfg.get("deleted_sessions", [])
                    if sid not in dels:
                        dels.append(sid)
                        cfg["deleted_sessions"] = dels
                        save_admin_config(cfg)

                    base_dir = Path(os.environ.get("APPLYPILOT_BASE_DIR", os.environ.get("APPLYPILOT_DIR", str(APP_DIR))))
                    target_dir = base_dir / "sessions" / sid
                    alt_dir = base_dir / "sessions" / f"ap_{sid}"
                    import shutil
                    if target_dir.exists():
                        shutil.rmtree(target_dir, ignore_errors=True)
                    if alt_dir.exists():
                        shutil.rmtree(alt_dir, ignore_errors=True)
                    _SESSION_ACTIVITY.pop(sid, None)

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Set-Cookie", "ddw_sid=; Path=/; Max-Age=0; SameSite=Lax")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"success": True}).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return

        if clean_path == "/api/session/import":
            try:
                data = post_json or {}
                target_dir = self.get_target_app_dir(data)
                os.environ["APPLYPILOT_DIR"] = str(target_dir.resolve())
                target_dir.mkdir(parents=True, exist_ok=True)

                searches_yaml = data.get("searches_yaml")
                if searches_yaml and isinstance(searches_yaml, str):
                    (target_dir / "searches.yaml").write_text(searches_yaml, encoding="utf-8")

                profile_data = data.get("profile")
                if profile_data:
                    import json as _json
                    profile_str = _json.dumps(profile_data, indent=2) if isinstance(profile_data, dict) else str(profile_data)
                    (target_dir / "profile.json").write_text(profile_str, encoding="utf-8")

                resume_txt = data.get("resume_txt")
                if resume_txt and isinstance(resume_txt, str):
                    (target_dir / "resume.txt").write_text(resume_txt, encoding="utf-8")

                jobs = data.get("jobs", [])
                jobs_imported = 0
                if jobs and isinstance(jobs, list):
                    db_file = target_dir / "applypilot.db"
                    conn = sqlite3.connect(str(db_file), timeout=10)
                    try:
                        from applypilot.database import ensure_columns, init_db
                        init_db(db_file)
                        ensure_columns(conn)

                        cursor = conn.cursor()
                        cursor.execute("PRAGMA table_info(jobs)")
                        valid_cols = {row[1] for row in cursor.fetchall()}

                        for j in jobs:
                            if not isinstance(j, dict) or not j.get("url"):
                                continue
                            filtered_job = {k: v for k, v in j.items() if k in valid_cols and k != "url"}
                            cols = ["url"] + list(filtered_job.keys())
                            placeholders = ",".join(["?"] * len(cols))
                            updates = ",".join([f"{k}=EXCLUDED.{k}" for k in filtered_job.keys()])

                            vals = [j["url"]] + list(filtered_job.values())
                            sql = f"INSERT INTO jobs ({','.join(cols)}) VALUES ({placeholders}) ON CONFLICT(url) DO UPDATE SET {updates}"
                            cursor.execute(sql, vals)
                            jobs_imported += 1

                        conn.commit()
                    finally:
                        conn.close()

                try:
                    from applypilot.view import generate_dashboard
                    generate_dashboard()
                except Exception as gen_err:
                    print("[Session Import Dashboard Notice]:", gen_err)

                try:
                    save_session_to_cloud(self.get_session_id(data), target_dir)
                except Exception as cloud_err:
                    print("[Session Cloud Save Notice]:", cloud_err)

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "success": True,
                    "message": "Session imported successfully",
                    "session_id": self.get_session_id(data),
                    "jobs_imported": jobs_imported
                }).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return

        if clean_path in ("/api/settings/auto-run", "/api/admin/auto-run/save", "/api/admin/auto-run"):
            try:
                data = post_json or {}
                cfg = load_admin_config()
                with _AUTO_RUN_LOCK:
                    if "enabled" in data:
                        _AUTO_RUN_STATE["enabled"] = bool(data["enabled"])
                        cfg["auto_run_enabled"] = _AUTO_RUN_STATE["enabled"]
                    if "interval" in data:
                        raw_interval = str(data["interval"]).strip()
                        if raw_interval in AUTO_RUN_INTERVAL_MAP:
                            _AUTO_RUN_STATE["interval"] = raw_interval
                            _AUTO_RUN_STATE["interval_minutes"] = AUTO_RUN_INTERVAL_MAP[raw_interval]
                            cfg["auto_run_interval"] = raw_interval

                    if _AUTO_RUN_STATE["enabled"]:
                        _AUTO_RUN_STATE["next_run"] = calculate_auto_run_next_run(int(_AUTO_RUN_STATE["interval_minutes"]))
                    else:
                        _AUTO_RUN_STATE["next_run"] = 0.0

                    resp_data = dict(_AUTO_RUN_STATE)

                save_admin_config(cfg)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, max-age=0")
                self.end_headers()
                self.wfile.write(json.dumps({"success": True, "settings": resp_data}).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return

        if clean_path in ("/api/settings/notifications", "/api/admin/notifications/save", "/api/admin/notifications"):
            try:
                data = post_json or {}
                cfg = load_admin_config()
                if "telegram_enabled" in data:
                    cfg["telegram_enabled"] = bool(data["telegram_enabled"])
                if "telegram_bot_token" in data:
                    raw_tok = str(data["telegram_bot_token"]).strip()
                    if raw_tok and "••••" not in raw_tok:
                        cfg["telegram_bot_token"] = raw_tok
                    elif not raw_tok:
                        cfg["telegram_bot_token"] = ""
                if "telegram_chat_id" in data:
                    cfg["telegram_chat_id"] = str(data["telegram_chat_id"]).strip()
                if "telegram_min_score" in data:
                    try:
                        cfg["telegram_min_score"] = int(data["telegram_min_score"])
                    except Exception:
                        cfg["telegram_min_score"] = 0
                if "whatsapp_enabled" in data:
                    cfg["whatsapp_enabled"] = bool(data["whatsapp_enabled"])
                if "whatsapp_phone" in data:
                    cfg["whatsapp_phone"] = str(data["whatsapp_phone"]).strip()
                if "whatsapp_api_key" in data:
                    raw_wa = str(data["whatsapp_api_key"]).strip()
                    if raw_wa and "••••" not in raw_wa:
                        cfg["whatsapp_api_key"] = raw_wa
                    elif not raw_wa:
                        cfg["whatsapp_api_key"] = ""
                if "server_external_url" in data:
                    cfg["server_external_url"] = str(data["server_external_url"]).strip()

                save_admin_config(cfg)

                tok = str(cfg.get("telegram_bot_token", "")).strip()
                masked = f"{tok[:6]}••••••••{tok[-4:]}" if len(tok) >= 12 else ("Configured" if tok else "")
                wa_key = str(cfg.get("whatsapp_api_key", "")).strip()
                wa_masked = f"{wa_key[:2]}••••••••{wa_key[-2:]}" if len(wa_key) >= 5 else ("Configured" if wa_key else "")
                resp_data = {
                    "telegram_enabled": bool(cfg.get("telegram_enabled", False)),
                    "telegram_bot_token": tok,
                    "telegram_bot_token_masked": masked,
                    "telegram_chat_id": str(cfg.get("telegram_chat_id", "")).strip(),
                    "telegram_min_score": int(cfg.get("telegram_min_score", 0)),
                    "telegram_configured": bool(tok and cfg.get("telegram_chat_id")),
                    "whatsapp_enabled": bool(cfg.get("whatsapp_enabled", False)),
                    "whatsapp_phone": str(cfg.get("whatsapp_phone", "")).strip(),
                    "whatsapp_api_key": wa_key,
                    "whatsapp_api_key_masked": wa_masked,
                    "whatsapp_configured": bool(cfg.get("whatsapp_phone") and wa_key),
                    "server_external_url": str(cfg.get("server_external_url", "")).strip(),
                }

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, max-age=0")
                self.end_headers()
                self.wfile.write(json.dumps({"success": True, "notifications": resp_data}).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return

        if clean_path in ("/api/settings/notifications/test-whatsapp", "/api/admin/notifications/test-whatsapp"):
            try:
                from applypilot.notifications import send_test_whatsapp_notification
                data = post_json or {}
                cfg = load_admin_config()
                phone = str(data.get("phone") or cfg.get("whatsapp_phone", "")).strip()
                api_key = str(data.get("api_key") or cfg.get("whatsapp_api_key", "")).strip()

                if "••••" in api_key:
                    api_key = str(cfg.get("whatsapp_api_key", "")).strip()

                if not phone:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": False, "message": "Phone number is required."}).encode("utf-8"))
                    return

                if not api_key:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": False, "message": "CallMeBot API Key is required."}).encode("utf-8"))
                    return

                success, message = send_test_whatsapp_notification(phone, api_key)
                self.send_response(200 if success else 400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"success": success, "message": message}).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "message": str(e)}).encode("utf-8"))
                return

        if clean_path in ("/api/settings/notifications/test", "/api/admin/notifications/test"):
            try:
                from applypilot.notifications import send_test_telegram_notification
                data = post_json or {}
                cfg = load_admin_config()
                bot_token = str(data.get("bot_token") or cfg.get("telegram_bot_token", "")).strip()
                chat_id = str(data.get("chat_id") or cfg.get("telegram_chat_id", "")).strip()

                if "••••" in bot_token:
                    bot_token = str(cfg.get("telegram_bot_token", "")).strip()

                if not bot_token:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": False, "message": "Bot Token is required."}).encode("utf-8"))
                    return

                if not chat_id:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": False, "message": "Chat ID is required."}).encode("utf-8"))
                    return

                success, message = send_test_telegram_notification(bot_token, chat_id)
                self.send_response(200 if success else 400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"success": success, "message": message}).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "message": str(e)}).encode("utf-8"))
                return

        if clean_path in ("/api/settings/notifications/detect-chat", "/api/admin/notifications/detect-chat"):
            try:
                from applypilot.notifications import detect_telegram_chat_id
                data = post_json or {}
                cfg = load_admin_config()
                bot_token = str(data.get("bot_token") or cfg.get("telegram_bot_token", "")).strip()
                if "••••" in bot_token:
                    bot_token = str(cfg.get("telegram_bot_token", "")).strip()

                cid, message = detect_telegram_chat_id(bot_token)
                if cid:
                    cfg["telegram_chat_id"] = str(cid)
                    save_admin_config(cfg)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": True, "chat_id": cid, "message": message}).encode("utf-8"))
                    return
                else:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": False, "message": message}).encode("utf-8"))
                    return
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "message": str(e)}).encode("utf-8"))
                return

        if clean_path == "/api/admin/login":
            client_ip = self.client_address[0] if self.client_address else "127.0.0.1"
            if not check_rate_limit(client_ip):
                self.send_response(429)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "success": False,
                    "error": "Too many failed login attempts. Please wait 15 minutes before trying again."
                }).encode("utf-8"))
                return

            data = post_json or {}
            pwd = data.get("password", "")
            cfg = load_admin_config()
            target_pwd = cfg.get("admin_password", "ddw2026")
            if pwd == target_pwd:
                token = generate_hmac_token(target_pwd)
                csrf_token = generate_csrf_token(target_pwd)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "success": True,
                    "token": token,
                    "csrf_token": csrf_token
                }).encode("utf-8"))
                return
            else:
                record_failed_login(client_ip)
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": "Invalid password"}).encode("utf-8"))
                return

        if clean_path == "/api/admin/maintenance/save":
            if not self._verify_admin_request():
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Unauthorized or invalid CSRF token"}).encode("utf-8"))
                return
            data = post_json or {}
            cfg = load_admin_config()
            cfg["maintenance_mode"] = bool(data.get("maintenance_mode", False))
            save_admin_config(cfg)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True}).encode("utf-8"))
            return

        if clean_path == "/api/admin/password/change":
            if not self._verify_admin_request():
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Unauthorized or invalid CSRF token"}).encode("utf-8"))
                return
            data = post_json or {}
            curr_pwd = data.get("current_password", "")
            new_pwd = data.get("new_password", "")
            cfg = load_admin_config()
            if curr_pwd != cfg.get("admin_password", "ddw2026"):
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": "Current password is incorrect"}).encode("utf-8"))
                return
            if len(new_pwd) < 4:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": "New password must be at least 4 characters long"}).encode("utf-8"))
                return
            cfg["admin_password"] = new_pwd
            save_admin_config(cfg)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True}).encode("utf-8"))
            return

        if clean_path == "/api/admin/sources/save":
            if not self._verify_admin_request():
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Unauthorized or invalid CSRF token"}).encode("utf-8"))
                return
            data = post_json or {}
            base_data_dir = Path(os.environ.get("APPLYPILOT_BASE_DIR", os.environ.get("APPLYPILOT_DIR", str(Path.cwd() / ".data"))))
            if base_data_dir.name == "sessions" or "sessions" in base_data_dir.parts:
                idx = base_data_dir.parts.index("sessions") if "sessions" in base_data_dir.parts else 0
                base_data_dir = Path(*base_data_dir.parts[:idx])
            sources_file = base_data_dir / "sources.json"
            sources_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True}).encode("utf-8"))
            return

        if clean_path == "/api/admin/database/vacuum":
            if not self._verify_admin_request():
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Unauthorized"}).encode("utf-8"))
                return
            root_db = app_dir / "applypilot.db"
            if root_db.exists():
                try:
                    conn = sqlite3.connect(str(root_db))
                    conn.execute("VACUUM")
                    conn.close()
                    new_size_mb = round(root_db.stat().st_size / (1024 * 1024), 2)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": True, "size_mb": new_size_mb}).encode("utf-8"))
                    return
                except Exception as exc:
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": False, "error": str(exc)}).encode("utf-8"))
                    return
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": False, "error": "Database file not found"}).encode("utf-8"))
            return

        if clean_path == "/api/admin/session/purge":
            if not self._verify_admin_request():
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Unauthorized or invalid CSRF token"}).encode("utf-8"))
                return
            data = post_json or {}
            sid = data.get("session_id")
            purged = 0
            if sid:
                base_data_dir = Path(os.environ.get("APPLYPILOT_BASE_DIR", os.environ.get("APPLYPILOT_DIR", str(Path.cwd() / ".data"))))
                if base_data_dir.name == "sessions" or "sessions" in base_data_dir.parts:
                    idx = base_data_dir.parts.index("sessions") if "sessions" in base_data_dir.parts else 0
                    base_data_dir = Path(*base_data_dir.parts[:idx])
                s_db = base_data_dir / "sessions" / sid / "applypilot.db"
                if s_db.exists():
                    try:
                        from applypilot.database import ensure_columns
                        conn = sqlite3.connect(str(s_db))
                        ensure_columns(conn)
                        c = conn.cursor()
                        # Purge un-saved and un-applied jobs: mark archived and clear heavy text fields to shrink DB size while retaining URL deduplication.
                        c.execute("""
                            UPDATE jobs
                            SET is_archived = 1,
                                full_description = NULL,
                                description = NULL,
                                score_reasoning = NULL,
                                tailored_resume_path = NULL,
                                cover_letter_path = NULL,
                                archived_at = COALESCE(archived_at, strftime('%b %d', 'now'))
                            WHERE COALESCE(is_saved, 0) = 0
                            AND COALESCE(apply_status, '') != 'applied'
                            AND (
                                COALESCE(is_archived, 0) = 1
                                OR apply_status = 'new'
                                OR apply_status IS NULL
                            )
                        """)
                        purged = c.rowcount
                        conn.commit()
                        conn.execute("VACUUM")
                        conn.close()
                    except Exception:
                        pass
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True, "purged_count": purged}).encode("utf-8"))
            return

        if clean_path == "/api/admin/session/permanent":
            if not self._verify_admin_request():
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Unauthorized or invalid CSRF token"}).encode("utf-8"))
                return
            data = post_json or {}
            sid = data.get("session_id")
            make_perm = bool(data.get("permanent", True))
            if sid:
                cfg = load_admin_config()
                perm_set = set(cfg.get("permanent_sessions", []))
                
                base_dir = Path(os.environ.get("APPLYPILOT_BASE_DIR", os.environ.get("APPLYPILOT_DIR", str(Path.cwd() / ".data"))))
                if base_dir.name == "sessions" or "sessions" in base_dir.parts:
                    idx = base_dir.parts.index("sessions") if "sessions" in base_dir.parts else 0
                    base_dir = Path(*base_dir.parts[:idx])
                s_folder = base_dir / "sessions" / sid
                s_folder.mkdir(parents=True, exist_ok=True)
                perm_marker = s_folder / ".permanent"

                if make_perm:
                    perm_set.add(sid)
                    try:
                        perm_marker.touch(exist_ok=True)
                    except Exception:
                        pass
                else:
                    perm_set.discard(sid)
                    if perm_marker.exists():
                        try:
                            perm_marker.unlink()
                        except Exception:
                            pass

                cfg["permanent_sessions"] = sorted(list(perm_set))
                save_admin_config(cfg)

                # Persist directly to Neon PostgreSQL cloud database
                pg_conn = get_postgres_connection()
                if pg_conn:
                    try:
                        init_postgres_db(pg_conn)
                        pg_conn.run("""
                            INSERT INTO duckduckwork_sessions (session_id, is_permanent, updated_at)
                            VALUES (:sid, :perm_val, CURRENT_TIMESTAMP)
                            ON CONFLICT (session_id) DO UPDATE SET
                                is_permanent = EXCLUDED.is_permanent,
                                updated_at = CURRENT_TIMESTAMP
                        """, sid=sid, perm_val=1 if make_perm else 0)
                    except Exception as pg_err:
                        print(f"[Postgres Permanent Toggle Error]: {pg_err}")

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True, "is_permanent": make_perm}).encode("utf-8"))
        if clean_path == "/api/admin/gemini":
            if not self._verify_admin_request():
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Unauthorized or invalid CSRF token"}).encode("utf-8"))
                return
            data = post_json or {}
            new_key = str(data.get("gemini_api_key", "")).strip()
            cfg = load_admin_config()
            cfg["gemini_api_key"] = new_key
            if new_key:
                os.environ["GEMINI_API_KEY"] = new_key
            save_admin_config(cfg)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True, "gemini_key_configured": bool(new_key)}).encode("utf-8"))
            return

        if clean_path == "/api/admin/proxy":
            if not self._verify_admin_request():
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Unauthorized or invalid CSRF token"}).encode("utf-8"))
                return
            data = post_json or {}
            new_proxy = str(data.get("proxy", "")).strip()
            new_key = str(data.get("webshare_api_key", "")).strip()
            cfg = load_admin_config()
            cfg["proxy"] = new_proxy
            if new_key:
                cfg["webshare_api_key"] = new_key
            save_admin_config(cfg)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True, "proxy": new_proxy, "webshare_api_key_set": bool(cfg.get("webshare_api_key"))}).encode("utf-8"))
            return

        if clean_path == "/api/admin/proxy/test":
            if not self._verify_admin_request():
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Unauthorized or invalid CSRF token"}).encode("utf-8"))
                return
            data = post_json or {}
            cfg = load_admin_config()
            target_key = str(data.get("webshare_api_key") or os.environ.get("WEBSHARE_API_KEY") or cfg.get("webshare_api_key", "")).strip()
            target_proxy = str(data.get("proxy") or os.environ.get("APPLYPILOT_PROXY") or os.environ.get("JOBSPY_PROXY") or os.environ.get("PROXY_URL") or cfg.get("proxy", "")).strip()

            from applypilot.discovery.jobspy import fetch_webshare_info, resolve_webshare_proxy

            ws_info = fetch_webshare_info(api_key=target_key) if target_key else {"valid": False}
            resolved_proxy = resolve_webshare_proxy(api_key=target_key if target_key else None, proxy_override=target_proxy if target_proxy else None)

            if not resolved_proxy and not ws_info.get("valid"):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "message": "No proxy URL or Webshare API Key configured or specified."}).encode("utf-8"))
                return

            try:
                import urllib.request
                import time

                if resolved_proxy.get("user") and resolved_proxy.get("pass"):
                    proxy_url = f"http://{resolved_proxy['user']}:{resolved_proxy['pass']}@{resolved_proxy['host']}:{resolved_proxy['port']}"
                else:
                    proxy_url = f"http://{resolved_proxy['host']}:{resolved_proxy['port']}"

                proxy_handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
                opener = urllib.request.build_opener(proxy_handler)

                t_start = time.time()
                req = urllib.request.Request("https://api.ipify.org?format=json", headers={"User-Agent": "Mozilla/5.0"})
                with opener.open(req, timeout=10) as resp:
                    raw = resp.read().decode("utf-8")
                    ip_data = json.loads(raw)
                    res_ip = ip_data.get("ip", "Unknown")
                    latency_ms = int((time.time() - t_start) * 1000)

                msg_parts = [f"Proxy Connection Active! IP: {res_ip} ({latency_ms}ms)"]
                if ws_info.get("valid"):
                    p_cnt = ws_info.get("proxy_count", len(ws_info.get("proxies", [])))
                    countries_str = ", ".join(ws_info.get("countries", []))
                    bw_mb = ws_info.get("stats", {}).get("bandwidth_mb", 0.0)
                    msg_parts.append(f"Webshare API: {p_cnt} active proxies ({countries_str}). Bandwidth used: {bw_mb} MB.")

                full_msg = " | ".join(msg_parts)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "success": True,
                    "ip": res_ip,
                    "latency_ms": latency_ms,
                    "webshare_info": ws_info,
                    "message": full_msg
                }).encode("utf-8"))
            except Exception as p_err:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "message": f"Proxy Ping Failed: {p_err}"}).encode("utf-8"))
                return

        if clean_path == "/api/session/reactivate":
            data = post_json or {}
            sid = data.get("session_id") or self.get_session_id(data)
            if sid:
                base_data_dir = Path(os.environ.get("APPLYPILOT_BASE_DIR", os.environ.get("APPLYPILOT_DIR", str(Path.cwd() / ".data"))))
                if base_data_dir.name == "sessions" or "sessions" in base_data_dir.parts:
                    idx = base_data_dir.parts.index("sessions") if "sessions" in base_data_dir.parts else 0
                    base_data_dir = Path(*base_data_dir.parts[:idx])
                s_dir = base_data_dir / "sessions" / sid
                if s_dir.exists():
                    marker = s_dir / ".inactive"
                    if marker.exists():
                        try:
                            marker.unlink()
                        except Exception:
                            pass
                    try:
                        os.utime(s_dir)
                    except Exception:
                        pass
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True}).encode("utf-8"))
            return

        if clean_path == "/api/admin/session/delete":
            if not self._verify_admin_request():
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Unauthorized or invalid CSRF token"}).encode("utf-8"))
                return
            data = post_json or {}
            sid = data.get("session_id")
            if sid:
                cfg = load_admin_config()
                dels = cfg.get("deleted_sessions", [])
                if sid not in dels:
                    dels.append(sid)
                    cfg["deleted_sessions"] = dels
                    save_admin_config(cfg)
                base_data_dir = Path(os.environ.get("APPLYPILOT_BASE_DIR", os.environ.get("APPLYPILOT_DIR", str(Path.cwd() / ".data"))))
                if base_data_dir.name == "sessions" or "sessions" in base_data_dir.parts:
                    idx = base_data_dir.parts.index("sessions") if "sessions" in base_data_dir.parts else 0
                    base_data_dir = Path(*base_data_dir.parts[:idx])
                s_dir = base_data_dir / "sessions" / sid
                import shutil
                if s_dir.exists():
                    shutil.rmtree(s_dir, ignore_errors=True)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True}).encode("utf-8"))
            return

        if clean_path in ("/api/admin/proxy/test", "/api/admin/test-proxy", "/api/admin/test-gemini"):
            return self.do_GET()

        self.send_error(404, "Endpoint not found")


def run_server(port: int = 5050):
    local_ip = get_local_ip()
    app_dir = os.environ.get("APPLYPILOT_DIR", str(APP_DIR))
    os.environ["APPLYPILOT_BASE_DIR"] = app_dir
    server_address = ("0.0.0.0", port)
    httpd = ThreadingHTTPServer(server_address, CRMDashboardHandler)

    print("\n==================================================", flush=True)
    print("DuckDuckWork CRM Dashboard Server Running!", flush=True)
    print("==================================================", flush=True)
    print(f"Access from your Phone/Network: http://{local_ip}:{port}/", flush=True)
    print(f"Access from Local Computer:     http://localhost:{port}/", flush=True)
    print(f"Serving Directory:              {app_dir}", flush=True)
    print("==================================================\n", flush=True)

    start_auto_run_scheduler()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
        httpd.server_close()


if __name__ == "__main__":
    run_server()
