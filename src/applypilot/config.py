"""DuckDuckWork configuration: paths, platform detection, user data."""

import os
import platform
import shutil
import time
from pathlib import Path

# User data directory — default fallback
_default_dir = Path("/tmp/.data") if os.environ.get("VERCEL") else (Path.cwd() / ".data")
APP_DIR = Path(os.environ.get("APPLYPILOT_DIR", str(_default_dir)))

def get_app_dir() -> Path:
    """Get dynamic app directory for current request/session."""
    return Path(os.environ.get("APPLYPILOT_DIR", str(APP_DIR)))

def get_resumes_dir() -> Path:
    """Get resumes directory for current session."""
    d = get_app_dir() / "resumes"
    d.mkdir(parents=True, exist_ok=True)
    return d

def get_cover_letter_dir() -> Path:
    """Get cover letters directory for current session."""
    d = get_app_dir() / "cover_letters"
    d.mkdir(parents=True, exist_ok=True)
    return d

def get_tailored_dir() -> Path:
    """Get tailored resumes directory for current session."""
    d = get_app_dir() / "tailored_resumes"
    d.mkdir(parents=True, exist_ok=True)
    return d

# Core paths (legacy/fallbacks)
DB_PATH = APP_DIR / "applypilot.db"
PROFILE_PATH = APP_DIR / "profile.json"
RESUME_PATH = APP_DIR / "resume.txt"
RESUME_PDF_PATH = APP_DIR / "resume.pdf"
SEARCH_CONFIG_PATH = APP_DIR / "searches.yaml"
ENV_PATH = APP_DIR / ".env"

# Generated output (legacy/fallbacks)
TAILORED_DIR = APP_DIR / "tailored_resumes"
COVER_LETTER_DIR = APP_DIR / "cover_letters"
LOG_DIR = APP_DIR / "logs"
RESUMES_DIR = APP_DIR / "resumes"

# Chrome worker isolation
CHROME_WORKER_DIR = APP_DIR / "chrome-workers"
APPLY_WORKER_DIR = APP_DIR / "apply-workers"

# Package-shipped config (YAML registries)
PACKAGE_DIR = Path(__file__).parent
CONFIG_DIR = PACKAGE_DIR / "config"


def get_chrome_path() -> str:
    """Auto-detect Chrome/Chromium executable path, cross-platform.

    Override with CHROME_PATH environment variable.
    """
    env_path = os.environ.get("CHROME_PATH")
    if env_path and Path(env_path).exists():
        return env_path

    system = platform.system()

    if system == "Windows":
        candidates = [
            Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        ]
    elif system == "Darwin":
        candidates = [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
        ]
    else:  # Linux
        candidates = []
        for name in ("google-chrome", "google-chrome-stable", "chromium-browser", "chromium"):
            found = shutil.which(name)
            if found:
                candidates.append(Path(found))

    for c in candidates:
        if c and c.exists():
            return str(c)

    # Fall back to PATH search
    for name in ("google-chrome", "google-chrome-stable", "chromium-browser", "chromium", "chrome"):
        found = shutil.which(name)
        if found:
            return found

    raise FileNotFoundError(
        "Chrome/Chromium not found. Install Chrome or set CHROME_PATH environment variable."
    )


def get_chrome_user_data() -> Path:
    """Default Chrome user data directory, cross-platform."""
    system = platform.system()
    if system == "Windows":
        return Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"
    elif system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
    else:
        return Path.home() / ".config" / "google-chrome"


def ensure_dirs():
    """Create all required directories."""
    app_dir = Path(os.environ.get("APPLYPILOT_DIR", APP_DIR))
    for d in [app_dir, app_dir / "tailored_resumes", app_dir / "cover_letters", app_dir / "logs", app_dir / "chrome-workers", app_dir / "apply-workers", app_dir / "resumes"]:
        d.mkdir(parents=True, exist_ok=True)


def load_profile(search_id: str | None = None) -> dict:
    """Load candidate profile for a specific search profile, falling back to profile.json."""
    import json
    app_dir = Path(os.environ.get("APPLYPILOT_DIR", APP_DIR))

    base_profile: dict = {}
    p_path = app_dir / "profile.json"
    if p_path.exists():
        try:
            base_profile = json.loads(p_path.read_text(encoding="utf-8"))
        except Exception:
            base_profile = {}

    if search_id:
        cfg = load_search_config()
        searches = cfg.get("searches", [])
        for sp in searches:
            if sp.get("id") == search_id:
                cand = sp.get("candidate") or sp.get("profile") or sp.get("candidate_profile")
                if isinstance(cand, dict) and cand:
                    merged = dict(base_profile)
                    merged.update(cand)
                    return merged

    if not base_profile and not p_path.exists():
        raise FileNotFoundError(
            f"Profile not found at {p_path}. Run `applypilot init` first."
        )
    return base_profile


def load_search_config() -> dict:
    """Load search configuration from ~/.applypilot/searches.yaml."""
    import yaml
    app_dir = Path(os.environ.get("APPLYPILOT_DIR", APP_DIR))
    cfg_path = app_dir / "searches.yaml"
    if not cfg_path.exists():
        return {}
    return yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}


def save_search_config(data: dict) -> None:
    """Save search configuration data to ~/.applypilot/searches.yaml."""
    import yaml
    app_dir = Path(os.environ.get("APPLYPILOT_DIR", APP_DIR))
    cfg_path = app_dir / "searches.yaml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cfg_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False)


def update_profile_last_run(profile_id: str, timestamp: float | None = None) -> None:
    """Update last_run timestamp for a search profile in searches.yaml."""
    if timestamp is None:
        timestamp = time.time()
    cfg = load_search_config()
    searches = cfg.get("searches", [])
    updated = False
    for sp in searches:
        if str(sp.get("id")) == str(profile_id):
            sp["last_run"] = int(timestamp)
            updated = True
            break
    if updated:
        save_search_config(cfg)


def extract_pdf_text(pdf_path: str | Path, txt_path: str | Path | None = None) -> str:
    """Extract plain text from a PDF file using pypdf."""
    from pypdf import PdfReader
    pdf_p = Path(pdf_path)
    if not pdf_p.exists():
        return ""
    
    reader = PdfReader(str(pdf_p))
    extracted_text = []
    for page in reader.pages:
        txt = page.extract_text()
        if txt:
            extracted_text.append(txt)
    
    full_text = "\n\n".join(extracted_text).strip()
    if txt_path:
        out_p = Path(txt_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(full_text, encoding="utf-8")
    
    return full_text


def recursive_unquote(val):
    import urllib.parse
    if isinstance(val, str):
        prev = None
        curr = val
        while curr != prev and "%" in curr:
            prev = curr
            curr = urllib.parse.unquote(curr)
        return curr
    elif isinstance(val, list):
        return [recursive_unquote(x) for x in val]
    elif isinstance(val, dict):
        return {k: recursive_unquote(v) for k, v in val.items()}
    return val


def get_search_profiles() -> list[dict]:
    """Return a normalized list of search profiles from searches.yaml."""
    cfg = load_search_config()
    searches = cfg.get("searches", [])
    if isinstance(searches, list) and len(searches) > 0:
        cleaned_searches = []
        for sp in searches:
            clean_sp = recursive_unquote(sp)
            clean_sp.setdefault("sites", ["linkedin", "indeed", "glassdoor"])
            clean_sp.setdefault("results_per_site", 50)
            clean_sp.setdefault("hours_old", 24)
            clean_sp.setdefault("easy_apply_only", False)
            clean_sp.setdefault("min_salary", "")
            clean_sp.setdefault("experience_level", "any")
            clean_sp.setdefault("title_match_mode", "flexible")
            clean_sp.setdefault("blocked_posters", [])
            clean_sp.setdefault("score_filters", ["high", "solid", "moderate"])
            clean_sp.setdefault("last_run", sp.get("last_run"))
            cleaned_searches.append(clean_sp)
        return cleaned_searches

    # Convert single search legacy config to structured format if needed
    queries = []
    for q in cfg.get("queries", []):
        if isinstance(q, dict) and "query" in q:
            queries.append(q["query"])
        elif isinstance(q, str):
            queries.append(q)

    locations = []
    for loc in cfg.get("locations", []):
        if isinstance(loc, dict) and "location" in loc:
            locations.append(loc["location"])
        elif isinstance(loc, str):
            locations.append(loc)

    legacy_profile = {
        "id": "search_1",
        "name": "Job Search",
        "enabled": True,
        "queries": queries or ["Job Search"],
        "locations": locations or ["Worldwide"],
        "location_accept": cfg.get("location", {}).get("accept_patterns", []),
        "location_reject": cfg.get("location", {}).get("reject_patterns", []),
        "country": cfg.get("country", "worldwide"),
        "sites": cfg.get("sites", ["linkedin"]),
        "results_per_site": cfg.get("defaults", {}).get("results_per_site", 5),
        "hours_old": cfg.get("defaults", {}).get("hours_old", 24),
        "easy_apply_only": False,
        "min_salary": "",
        "experience_level": "any",
        "blocked_posters": [],
        "score_filters": ["high", "solid", "moderate"],
        "resume_filename": "search_1.pdf",
    }
    return [legacy_profile]


def load_sites_config() -> dict:
    """Load sites.yaml configuration (sites list, manual_ats, blocked, etc.)."""
    import yaml
    path = CONFIG_DIR / "sites.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def is_manual_ats(url: str | None) -> bool:
    """Check if a URL routes through an ATS that requires manual application."""
    if not url:
        return False
    sites_cfg = load_sites_config()
    domains = sites_cfg.get("manual_ats", [])
    url_lower = url.lower()
    return any(domain in url_lower for domain in domains)


def load_blocked_sites() -> tuple[set[str], list[str]]:
    """Load blocked sites and URL patterns from sites.yaml.

    Returns:
        (blocked_site_names, blocked_url_patterns)
    """
    cfg = load_sites_config()
    blocked = cfg.get("blocked", {})
    sites = set(blocked.get("sites", []))
    patterns = blocked.get("url_patterns", [])
    return sites, patterns


def load_blocked_sso() -> list[str]:
    """Load blocked SSO domains from sites.yaml."""
    cfg = load_sites_config()
    return cfg.get("blocked_sso", [])


def load_base_urls() -> dict[str, str | None]:
    """Load site base URLs for URL resolution from sites.yaml."""
    cfg = load_sites_config()
    return cfg.get("base_urls", {})


# ---------------------------------------------------------------------------
# Default values — referenced across modules instead of magic numbers
# ---------------------------------------------------------------------------

DEFAULTS = {
    "min_score": 7,
    "max_apply_attempts": 3,
    "max_tailor_attempts": 5,
    "poll_interval": 60,
    "apply_timeout": 300,
    "viewport": "1280x900",
}


def load_env():
    """Load environment variables from ~/.applypilot/.env if it exists."""
    from dotenv import load_dotenv
    if ENV_PATH.exists():
        load_dotenv(ENV_PATH)
    # Also try CWD .env as fallback
    load_dotenv()


# ---------------------------------------------------------------------------
# Tier system — feature gating by installed dependencies
# ---------------------------------------------------------------------------

TIER_LABELS = {
    1: "Discovery",
    2: "AI Scoring & Tailoring",
    3: "Full Auto-Apply",
}

TIER_COMMANDS: dict[int, list[str]] = {
    1: ["init", "run discover", "run enrich", "status", "dashboard"],
    2: ["run score", "run tailor", "run cover", "run pdf", "run"],
    3: ["apply"],
}


def get_tier() -> int:
    """Detect the current tier based on available dependencies.

    Tier 1 (Discovery):            Python + pip
    Tier 2 (AI Scoring & Tailoring): + LLM API key
    Tier 3 (Full Auto-Apply):       + Claude Code CLI + Chrome
    """
    load_env()

    has_llm = any(os.environ.get(k) for k in ("GEMINI_API_KEY", "OPENAI_API_KEY", "LLM_URL"))
    if not has_llm:
        return 1

    has_claude = shutil.which("claude") is not None
    try:
        get_chrome_path()
        has_chrome = True
    except FileNotFoundError:
        has_chrome = False

    if has_claude and has_chrome:
        return 3

    return 2


def check_tier(required: int, feature: str) -> None:
    """Raise SystemExit with a clear message if the current tier is too low.

    Args:
        required: Minimum tier needed (1, 2, or 3).
        feature: Human-readable description of the feature being gated.
    """
    current = get_tier()
    if current >= required:
        return

    from rich.console import Console
    _console = Console(stderr=True)

    missing: list[str] = []
    if required >= 2 and not any(os.environ.get(k) for k in ("GEMINI_API_KEY", "OPENAI_API_KEY", "LLM_URL")):
        missing.append("LLM API key — run [bold]applypilot init[/bold] or set GEMINI_API_KEY")
    if required >= 3:
        if not shutil.which("claude"):
            missing.append("Claude Code CLI — install from [bold]https://claude.ai/code[/bold]")
        try:
            get_chrome_path()
        except FileNotFoundError:
            missing.append("Chrome/Chromium — install or set CHROME_PATH")

    _console.print(
        f"\n[red]'{feature}' requires {TIER_LABELS.get(required, f'Tier {required}')} (Tier {required}).[/red]\n"
        f"Current tier: {TIER_LABELS.get(current, f'Tier {current}')} (Tier {current})."
    )
    if missing:
        _console.print("\n[yellow]Missing:[/yellow]")
        for m in missing:
            _console.print(f"  - {m}")
    _console.print()
    raise SystemExit(1)
