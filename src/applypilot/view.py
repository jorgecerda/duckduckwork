import json
import os
import re
import time
import webbrowser
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html import escape
from pathlib import Path

from rich.console import Console

from applypilot.config import APP_DIR, PACKAGE_DIR, get_search_profiles
from applypilot.database import init_db

console = Console()
STATIC_DIR = PACKAGE_DIR / "static"


def load_static_css() -> str:
    """Load dashboard CSS stylesheet."""
    css_path = STATIC_DIR / "css" / "dashboard.css"
    if css_path.exists():
        return css_path.read_text(encoding="utf-8")
    return ""


def load_static_js() -> str:
    """Load dashboard JavaScript logic."""
    js_path = STATIC_DIR / "js" / "dashboard.js"
    if js_path.exists():
        return js_path.read_text(encoding="utf-8")
    return ""


# Country lookup table (Plain text names, no emojis)
COUNTRY_FLAGS = {
    "guatemala": ("Guatemala", ""),
    "nicaragua": ("Nicaragua", ""),
    "costa rica": ("Costa Rica", ""),
    "panama": ("Panama", ""),
    "panamá": ("Panama", ""),
    "colombia": ("Colombia", ""),
    "mexico": ("Mexico", ""),
    "méxico": ("Mexico", ""),
    "argentina": ("Argentina", ""),
    "chile": ("Chile", ""),
    "peru": ("Peru", ""),
    "perú": ("Peru", ""),
    "united states": ("United States", ""),
    "usa": ("United States", ""),
    "canada": ("Canada", ""),
    "latam": ("LATAM", ""),
    "latin america": ("LATAM", ""),
    "américa latina": ("LATAM", ""),
}


def resolve_company_logo(company_name: str | None, company_logo_db: str | None) -> str | None:
    if company_logo_db and str(company_logo_db).strip().startswith("http"):
        return str(company_logo_db).strip()
    return None


def format_applied_date(val):
    if not val:
        return datetime.now().strftime("%b %d")
    val_str = str(val).strip()
    try:
        if "T" in val_str:
            dt = datetime.fromisoformat(val_str.replace("Z", "+00:00"))
            return dt.strftime("%b %d")
        elif "-" in val_str:
            parts = val_str.split(" ")[0].split("-")
            if len(parts) == 3:
                dt = datetime(int(parts[0]), int(parts[1]), int(parts[2]))
                return dt.strftime("%b %d")
    except Exception:
        pass
    return val_str


def detect_country_meta(title="", location="", description="", full_desc="") -> tuple[str, str]:
    """Detect country name from job fields. Returns (country_name, "")."""
    loc_raw = location or ""
    loc_lower = loc_raw.lower()
    t = title or ""
    fd = full_desc or description or ""
    text_raw = t + " " + loc_raw + " " + fd
    text_lower = text_raw.lower()

    for key, (name, _) in COUNTRY_FLAGS.items():
        pattern = r'\b' + re.escape(key) + r'\b'
        if re.search(pattern, loc_lower):
            return name, ""

    if re.search(r'\b(united states|usa|pennsylvania|california|texas|florida)\b', loc_lower):
        return "United States", ""
    if re.search(r'\b(canada|ontario|quebec)\b', loc_lower):
        return "Canada", ""

    for key, (name, _) in COUNTRY_FLAGS.items():
        pattern = r'\b' + re.escape(key) + r'\b'
        if re.search(pattern, text_lower):
            return name, ""

    if re.search(r'\b(remote|wfh|work from home)\b', text_lower):
        return "Remote", ""

    return "Worldwide", ""


def detect_work_mode(title="", location="", description="", full_desc="") -> tuple[str, str]:
    """Detect work mode (Remote, Hybrid, On-site). Returns (work_mode_label, "")."""
    loc_lower = (location or "").lower()
    t_lower = (title or "").lower()
    fd_lower = (full_desc or description or "").lower()
    combined = t_lower + " " + loc_lower + " " + fd_lower

    if "hybrid" in loc_lower or "hybrid" in t_lower or "hybrid" in fd_lower:
        return "Hybrid", ""
    if re.search(r'\b(on-site|onsite|in-office|in office)\b', combined):
        return "On-site", ""
    if re.search(r'\b(remote|wfh|work from home|distributed)\b', combined):
        return "Remote", ""

    return "Remote", ""


EMOJI_PATTERN = re.compile(r'[\U0001F300-\U0001F9FF\U00002600-\U000026FF\U00002700-\U000027BF]')

def strip_emojis(text: str) -> str:
    if not text:
        return ""
    return EMOJI_PATTERN.sub("", text)


def format_source_name(raw_name: str) -> str:
    """Format raw source site string to clean Display Name."""
    val = (raw_name or "").strip().lower()
    mapping = {
        "linkedin": "LinkedIn",
        "indeed": "Indeed",
        "get on board": "Get on Board",
        "getonboard": "Get on Board",
        "remotive": "Remotive",
        "weworkremotely": "WeWorkRemotely",
        "hirewithnear": "Hire With Near",
        "hire with near": "Hire With Near",
        "near": "Hire With Near",
    }
    return mapping.get(val, val.capitalize() if val else "Direct")


def is_company_blocked(company: str | None, blocked_posters: list[str] | None) -> bool:
    """Check if a company matches any blocked job poster keyword."""
    if not company or not blocked_posters:
        return False
    company_clean = re.sub(r"[^a-zA-Z0-9]", "", company).lower()
    for bp in blocked_posters:
        bp_clean = re.sub(r"[^a-zA-Z0-9]", "", str(bp)).lower()
        if bp_clean and (bp_clean in company_clean or company_clean in bp_clean):
            return True
    return False


def format_relative_publication_time(date_val: str | None) -> str:
    """Format publication date into human-readable relative time string (e.g. '5 hours ago', '1 day ago')."""
    if not date_val:
        return ""
    try:
        raw = str(date_val).strip()
        if not raw:
            return ""
        clean_str = raw.replace("Z", "+00:00")
        if "T" in clean_str:
            dt = datetime.fromisoformat(clean_str)
        else:
            dt = datetime.strptime(clean_str.split()[0], "%Y-%m-%d").replace(tzinfo=UTC)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)

        now = datetime.now(UTC)
        diff = now - dt

        seconds = int(diff.total_seconds())
        if seconds < 0:
            return "Just now"

        minutes = seconds // 60
        hours = minutes // 60
        days = hours // 24
        weeks = days // 7
        months = days // 30

        if minutes < 5:
            return "Just now"
        elif minutes < 60:
            return f"{minutes} mins ago" if minutes != 1 else "1 min ago"
        elif hours < 24:
            return f"{hours} hours ago" if hours != 1 else "1 hour ago"
        elif days < 7:
            return f"{days} days ago" if days != 1 else "1 day ago"
        elif days < 30:
            return f"{weeks} weeks ago" if weeks != 1 else "1 week ago"
        else:
            return f"{months} months ago" if months != 1 else "1 month ago"
    except Exception:
        return ""


def extract_role_summary(title: str, company: str, raw_desc: str) -> str:
    """Extract a concise 2-line summary of role responsibilities, omitting company marketing boilerplate."""
    if not raw_desc:
        return ""
    text = re.sub(r'<br\s*/?>|</p>|</li>|</div>', '\n', raw_desc, flags=re.I)
    text = re.sub(r'<[^>]*>', ' ', text)
    text = re.sub(r'&nbsp;', ' ', text)

    lines = [line.strip() for line in text.split('\n') if line.strip()]

    boilerplate_patterns = [
        r'^(about|acerca de|quienes somos|who we are|why work with us|nuestra empresa|our mission|our culture)\b',
        r'leading the way',
        r'4,?000\+ team',
        r'top 1%',
        r'annual revenue',
        r'global network of top talent',
        r'is building the next generation',
        r'not another.*vendor',
        r'we deliver cutting-edge',
        r'we are an equal opportunity',
        r'equal opportunity employer',
    ]

    role_section_headers = [
        r'(propósito|objetivo|misión) del puesto',
        r'(role|position|job)\s*(overview|summary|description)',
        r'about the role',
        r'the role',
        r'what you(\'ll|\s+will)\s+do',
        r'what you(\'ll|\s+will)\s+be doing',
        r'(key\s+)?responsibilities',
        r'(funciones|responsabilidades|principales funciones)',
        r'you will\b',
        r'as a\b',
        r'we are (looking for|seeking)\b',
    ]

    found_idx = -1
    for i, line in enumerate(lines):
        clean_l = line.lower()
        if any(re.search(pat, clean_l) for pat in role_section_headers):
            found_idx = i
            break

    candidate_lines = []
    if found_idx != -1:
        for line_item in lines[found_idx:found_idx+4]:
            if any(re.match(r'^' + pat + r'[:\s]*$', line_item.strip().lower()) for pat in role_section_headers):
                continue
            if any(re.search(bp, line_item.lower()) for bp in boilerplate_patterns):
                continue
            if len(line_item) > 15:
                candidate_lines.append(line_item)
            if len(candidate_lines) >= 2:
                break

    if not candidate_lines:
        for line in lines:
            if any(re.search(bp, line.lower()) for bp in boilerplate_patterns):
                continue
            if company and company.lower() in line.lower() and ('welcome' in line.lower() or 'leading' in line.lower() or 'founded' in line.lower() or 'team' in line.lower()):
                continue
            if len(line.split()) < 5:
                continue
            candidate_lines.append(line)
            if len(candidate_lines) >= 2:
                break

    summary = ' '.join(candidate_lines)
    summary = re.sub(r'\s+', ' ', summary).strip()
    summary = strip_emojis(summary)

    if len(summary) > 220:
        clipped = summary[:220]
        last_punct = max(clipped.rfind('.'), clipped.rfind(';'), clipped.rfind(','))
        if last_punct > 140:
            summary = clipped[:last_punct+1]
        else:
            last_space = clipped.rfind(' ')
            summary = (clipped[:last_space] if last_space > 0 else clipped) + '...'
    return summary


def format_search_updated_text(last_run: float | int | str | None) -> str:
    """Format relative search updated text (e.g. 'Search Updated 15 minutes ago')."""
    if not last_run:
        return "Search not run yet"
    try:
        if isinstance(last_run, (int, float)):
            ts = float(last_run)
            ts_sec = ts if ts < 1e11 else ts / 1000.0
        elif isinstance(last_run, str):
            clean_str = last_run.strip()
            if clean_str.isdigit():
                val = float(clean_str)
                ts_sec = val if val < 1e11 else val / 1000.0
            else:
                try:
                    dt = parsedate_to_datetime(clean_str)
                    ts_sec = dt.timestamp()
                except Exception:
                    dt = datetime.fromisoformat(clean_str.replace("Z", "+00:00"))
                    ts_sec = dt.timestamp()
        else:
            return "Search not run yet"

        now_sec = time.time()
        diff_sec = max(0, int(now_sec - ts_sec))
        diff_min = diff_sec // 60
        diff_hours = diff_min // 60
        diff_days = diff_hours // 24

        if diff_min < 1:
            return "Search Updated just now"
        if diff_min == 1:
            return "Search Updated 1 minute ago"
        if diff_min < 60:
            return f"Search Updated {diff_min} minutes ago"
        if diff_hours == 1:
            return "Search Updated 1 hour ago"
        if diff_hours < 24:
            return f"Search Updated {diff_hours} hours ago"
        if diff_days == 1:
            return "Search Updated 1 day ago"
        return f"Search Updated {diff_days} days ago"
    except Exception:
        return "Search not run yet"


def format_fit_summary(score: int | None, raw_reasoning: str, title: str = "") -> tuple[str, str]:
    """Format fit analysis into a 2-line alignment summary taking into account the match percentage."""
    raw = strip_emojis(raw_reasoning or '').strip()
    if any(err in raw.lower() for err in ('429', 'http/status', 'llm error', 'too many requests', 'rate limit')):
        raw = ''

    pct = (score * 10) if (score is not None and 1 <= score <= 10) else (score or 0)

    if score is None or (score == 0 and not raw):
        return 'Pending Evaluation: Role has not been evaluated against candidate resume yet.', 'fit-pending'

    if pct >= 80:
        badge_label = f'Strongly Aligned ({pct}%)'
        css_cls = 'fit-aligned'
        fallback = 'Resume strongly aligns with core technical and leadership requirements.'
    elif pct >= 60:
        badge_label = f'Moderately Aligned ({pct}%)'
        css_cls = 'fit-moderate'
        fallback = 'Resume partially aligns with role requirements and offers transferable domain skills.'
    else:
        badge_label = f'Not Aligned ({pct}%)' if pct > 0 else 'Not Aligned (0%)'
        css_cls = 'fit-unaligned'
        fallback = 'Candidate background does not closely align with core role prerequisites.'

    clean_text = ''
    lines = [line.strip() for line in raw.split('\n') if line.strip()]

    for line in lines:
        if line.lower().startswith('reasoning:'):
            clean_text = line[10:].strip()
            break

    if not clean_text:
        sentence_lines = []
        for line_item in lines:
            if re.match(r'^(score|keywords)\s*:', line_item, re.I):
                continue
            if line_item.startswith('*') or line_item.startswith('-'):
                continue
            words = line_item.split()
            has_verbs = any(v in f' {line_item.lower()} ' for v in (' is ', ' has ', ' with ', ' for ', ' the ', ' and ', ' in ', ' requires ', ' possess ', ' es ', ' tiene ', ' para ', ' con '))
            if len(words) >= 6 and (has_verbs or line_item.endswith('.')):
                sentence_lines.append(line_item)

        if sentence_lines:
            clean_text = ' '.join(sentence_lines)

    clean_text = re.sub(r'\s+', ' ', clean_text).strip()
    if not clean_text or clean_text.lower() in ('none', 'null', 'n/a'):
        clean_text = fallback

    if len(clean_text) > 170:
        clipped = clean_text[:170]
        last_dot = clipped.rfind('.')
        if last_dot > 100:
            clean_text = clipped[:last_dot+1]
        else:
            last_space = clipped.rfind(' ')
            clean_text = (clipped[:last_space] if last_space > 0 else clipped) + '...'

    formatted = f'{badge_label}: {clean_text}'
    return formatted, css_cls


def get_job_score_category(score: int | None) -> str:
    """Return normalized score category: high, solid, moderate, or low."""
    s = score if score is not None else 0
    if s >= 85 or (s >= 8 and s <= 10):
        return "high"
    elif s >= 70 or s == 7:
        return "solid"
    elif s >= 50 or s in (5, 6):
        return "moderate"
    else:
        return "low"


def render_job_card(j: dict, idx: int, colors: dict, active_search_id: str = "search_1", blocked_posters: list[str] | None = None, score_filters: list[str] | None = None) -> str:
    """Render a single modern elevated Job Card component."""
    score = j["fit_score"] if j["fit_score"] is not None else 0
    title = escape(strip_emojis(j["title"] or "Untitled"))
    company_name = escape(strip_emojis(j["company"] or "Direct Employer"))
    company_initial = company_name[0].upper() if company_name and company_name[0].isalnum() else "A"
    url = escape(j["url"] or "")
    raw_app_url = (j.get("application_url") or "").strip()
    raw_site = (j.get("site") or "").strip().lower()
    strategy_val = (j.get("strategy") or "").lower()

    # Green Easy Apply is ONLY for LinkedIn jobs that are Easy Apply
    is_easy_apply = (raw_site == "linkedin") and (
        ("easy" in strategy_val) or ("easy_apply" in strategy_val) or bool(j.get("is_easy_apply")) or (not raw_app_url or raw_app_url == "None")
    )

    if is_easy_apply:
        final_apply_url = url
        apply_btn_label = "Easy Apply &rarr;"
        easy_apply_class = " btn-easy-apply"
    elif raw_app_url and raw_app_url != "None" and raw_app_url.startswith("http"):
        final_apply_url = escape(raw_app_url)
        apply_btn_label = "Apply Now &rarr;"
        easy_apply_class = ""
    else:
        final_apply_url = url
        apply_btn_label = "Apply Now &rarr;"
        easy_apply_class = ""

    salary = escape(strip_emojis(j["salary"] or ""))
    location = escape(strip_emojis(j["location"] or ""))
    raw_site = j["site"] or ""
    site_display = format_source_name(raw_site)
    site_color = colors.get(raw_site.lower(), "#64748b")

    pub_date_raw = j.get("posted_date") or j.get("date_posted") or j.get("discovered_at")
    posted_relative = format_relative_publication_time(pub_date_raw)

    is_saved = bool(j["is_saved"])
    is_archived = bool(j["is_archived"]) or (j["apply_status"] == "archived")
    is_applied = (j["apply_status"] == "applied") or (j["applied_at"] is not None)
    status = "applied" if is_applied else "new"
    applied_at_raw = j["applied_at"] or ""
    applied_timestamp = j["applied_at"] or ""

    archived_at = j["archived_at"]
    if not archived_at and j["discovered_at"]:
        try:
            dt = datetime.fromisoformat(j["discovered_at"].replace("Z", "+00:00"))
            archived_at = dt.strftime("%b %d")
        except Exception:
            archived_at = datetime.now().strftime("%b %d")
    if not archived_at:
        archived_at = datetime.now().strftime("%b %d")

    reasoning_raw = strip_emojis(j["score_reasoning"] or "")
    if reasoning_raw.strip().lower() in ("none", "null", ""):
        reasoning_raw = ""
    reasoning_lines = [line_item for line_item in reasoning_raw.split("\n") if line_item.strip() and line_item.strip().lower() != "none"]
    reasoning = reasoning_lines[1][:200] if len(reasoning_lines) > 1 else (reasoning_lines[0][:200] if reasoning_lines else "")
    if reasoning and any(err_token in reasoning.lower() for err_token in ("429", "http/status", "llm error", "developer.mozilla.org", "too many requests", "rate limit")):
        reasoning = ""

    c_name = j["country"] or "Worldwide"
    country_display = c_name

    wm_name, _ = detect_work_mode(
        j["title"] or "",
        j["location"] or "",
        j["description"] or "",
        j["full_description"] or ""
    )
    workmode_display = wm_name

    # Score Pill Class, Label & Category
    if score >= 85 or (score >= 8 and score <= 10):
        score_cls = "score-high"
        score_badge_text = f"High Match ({score if score > 10 else score * 10}%)"
        score_cat = "high"
    elif score >= 70 or score == 7:
        score_cls = "score-solid"
        score_badge_text = f"Solid Match ({score if score > 10 else score * 10}%)"
        score_cat = "solid"
    elif score >= 50 or score in (5, 6):
        score_cls = "score-moderate"
        score_badge_text = f"Moderate ({score if score > 10 else score * 10}%)"
        score_cat = "moderate"
    else:
        score_cls = "score-low"
        if score > 0:
            score_badge_text = f"Low Match ({score if score > 10 else score * 10}%)"
        elif not is_saved and not is_applied and not is_archived and status in ("all", "new"):
            score_badge_text = "New Role"
        else:
            score_badge_text = ""
        score_cat = "low"

    from applypilot.database import decrypt_sensitive
    raw_notes = (j.get("notes") or "").strip() if isinstance(j, dict) else ""
    notes_val = (decrypt_sensitive(raw_notes) or "").strip()
    sub_status_val = (j.get("sub_status") or "").strip() if isinstance(j, dict) else ""

    meta_parts = [
        f'<span class="meta-tag country-tag">{escape(country_display)}</span>',
        f'<span class="meta-tag workmode-tag">{escape(workmode_display)}</span>',
        f'<span class="meta-tag company-tag">{company_name}</span>',
        f'<span class="meta-tag site-tag" style="background:{site_color}18;color:{site_color};border:1px solid {site_color}33">{escape(site_display)}</span>'
    ]
    if salary:
        meta_parts.append(f'<span class="meta-tag salary-tag">{salary}</span>')
    if notes_val:
        meta_parts.append(f'<span class="meta-tag notes-badge" style="background:#fef3c7;color:#92400e;border:1px solid #fde68a;" title="{escape(notes_val[:80])}">Notes</span>')

    repost_count = int(j.get("repost_count") or 1)
    is_expired = bool(j.get("is_expired"))
    expired_reason = (j.get("expired_reason") or "Oferta cerrada o no disponible").strip()

    if repost_count >= 2:
        meta_parts.append(
            f'<span class="meta-tag repost-tag" style="background:#fef2f2;color:#991b1b;border:1px solid #fecaca;font-weight:600;" '
            f'title="Detectado como posible Ghost Job por multiples publicaciones recientes">&#9888;&#65039; Re-publicado {repost_count} veces en 90 d&iacute;as</span>'
        )

    if is_expired:
        meta_parts.append(
            f'<span class="meta-tag expired-tag" style="background:#f3f4f6;color:#6b7280;border:1px solid #d1d5db;font-weight:600;" '
            f'title="{escape(expired_reason)}">&#128683; Oferta Expirada</span>'
        )

    meta_html = " ".join(meta_parts)

    resume_path = j.get("tailored_resume_path")
    cover_path = j.get("cover_letter_path")
    resume_html = ""
    cover_html = ""

    if resume_path:
        pdf_path = resume_path.replace(".txt", ".pdf")
        rel_pdf = f"tailored_resumes/{Path(pdf_path).name}"
        rel_txt = f"tailored_resumes/{Path(resume_path).name}"
        if Path(pdf_path).exists():
            resume_html = f'<a href="{rel_pdf}" class="action-btn btn-outline btn-asset-pdf" target="_blank" rel="noreferrer noopener" onclick="event.stopPropagation()">Resume (PDF)</a>'
        else:
            resume_html = f'<a href="{rel_txt}" class="action-btn btn-outline btn-asset-pdf" target="_blank" rel="noreferrer noopener" onclick="event.stopPropagation()">Resume (Text)</a>'

    if cover_path:
        cover_pdf_path = cover_path.replace(".txt", ".pdf")
        rel_cover_pdf = f"cover_letters/{Path(cover_pdf_path).name}"
        rel_cover_txt = f"cover_letters/{Path(cover_path).name}"
        if Path(cover_pdf_path).exists():
            cover_html = f'<a href="{rel_cover_pdf}" class="action-btn btn-outline btn-asset-pdf" target="_blank" rel="noreferrer noopener" onclick="event.stopPropagation()">Cover Letter (PDF)</a>'
        else:
            cover_html = f'<a href="{rel_cover_txt}" class="action-btn btn-outline btn-asset-pdf" target="_blank" rel="noreferrer noopener" onclick="event.stopPropagation()">Cover Letter (Text)</a>'

    btn_saved_active = ' active' if is_saved else ''
    btn_saved_text = 'Saved' if is_saved else 'Save'

    btn_applied_active = ' active' if is_applied else ''
    btn_applied_text = 'Applied' if is_applied else 'Mark Applied'

    btn_archive_active = ' active' if is_archived else ''
    btn_archive_text = 'Archived' if is_archived else 'Archive'

    search_id_val = escape(str(j["search_id"])) if ("search_id" in j and j["search_id"]) else active_search_id
    is_blocked = is_company_blocked(j.get("company"), blocked_posters)
    allowed_scores = [s.lower() for s in (score_filters or ["high", "solid", "moderate"])]
    is_score_filtered = score_cat not in allowed_scores
    is_hidden = (search_id_val != active_search_id) or is_archived or is_saved or is_applied or is_blocked or is_score_filtered
    hidden_cls = " hidden" if is_hidden else ""

    db_logo = j.get("company_logo") if isinstance(j, dict) else (j["company_logo"] if "company_logo" in j else None)
    company_logo_url = resolve_company_logo(company_name, db_logo)
    if company_logo_url:
        logo_html = f'<img src="{escape(company_logo_url)}" class="company-avatar-img" referrerpolicy="no-referrer" alt="{company_name}" onerror="this.style.display=\'none\'; this.nextElementSibling.style.display=\'flex\';"><div class="company-avatar" style="display:none">{company_initial}</div>'
    else:
        logo_html = f'<div class="company-avatar">{company_initial}</div>'

    score_badge_html = f'<span class="score-pill {score_cls}">{score_badge_text}</span>' if score_badge_text else ''
    from applypilot.enrichment.cleaner import clean_job_description
    raw_desc = j["full_description"] or j["description"] or ""
    clean_desc = clean_job_description(raw_desc)
    ai_summary_val = strip_emojis(j.get("ai_summary") or "").strip()
    if ai_summary_val and ai_summary_val.lower() not in ("none", "null", ""):
        desc_snippet = ai_summary_val
    else:
        desc_snippet = extract_role_summary(title, company_name, raw_desc)
    fit_summary_text, fit_css_cls = format_fit_summary(score, reasoning_raw, title)

    return f"""
    <div class="job-card radar-card-row{hidden_cls}" data-order="{idx}" data-job-url="{url}" data-apply-url="{final_apply_url}" data-is-easy-apply="{'true' if is_easy_apply else 'false'}" data-app-type="{'easy_apply' if is_easy_apply else 'website'}" data-search-id="{search_id_val}" data-is-blocked="{'true' if is_blocked else 'false'}" data-score="{score}" data-score-category="{score_cat}" data-repost-count="{repost_count}" data-is-expired="{'1' if is_expired else '0'}" data-site="{escape(raw_site.lower())}" data-company="{company_name.lower()}" data-company-name="{company_name}" data-title="{escape(title.lower())}" data-date="{escape(str(pub_date_raw or ''))}" data-country="{escape(c_name.lower())}" data-workmode="{escape(wm_name.lower())}" data-location="{location.lower()}" data-status="{status}" data-sub-status="{escape(sub_status_val)}" data-saved="{'true' if is_saved else 'false'}" data-archived="{'true' if is_archived else 'false'}" data-archived-at="{archived_at}" data-applied-at="{escape(applied_at_raw)}" data-applied-timestamp="{escape(applied_timestamp)}" data-posted-relative="{posted_relative}" onclick="handleCardClick(event, this)">
      <div class="card-left-content">
        <div class="card-company-title-row">
          {logo_html}
          <div class="company-and-title">
            <span class="company-name">{company_name}</span>
            <a href="{url}" class="job-title" onclick="event.preventDefault(); openJobDrawer(this.closest('.job-card'));">{title}</a>
          </div>
        </div>
        <div class="meta-tags">{meta_html}</div>
        {f'<div class="job-desc-snippet">{escape(desc_snippet)}</div>' if desc_snippet else ''}
        {f'<div class="reasoning-row {fit_css_cls}">{escape(fit_summary_text)}</div>' if fit_summary_text else ''}
        <div class="full-desc-data" style="display:none">{strip_emojis(clean_desc)}</div>
        <div class="job-notes-data" style="display:none">{escape(strip_emojis(notes_val))}</div>
      </div>
      <div class="card-right-column">
        <div class="card-right-top">
          {score_badge_html}
          {f'<span class="posted-time-tag">{posted_relative}</span>' if posted_relative else ''}
        </div>
        <div class="card-right-actions" onclick="event.stopPropagation()">
          <a href="{final_apply_url}" target="_blank" rel="noreferrer noopener" class="action-btn btn-apply{easy_apply_class}" onclick="event.stopPropagation()">{apply_btn_label}</a>
          <div class="card-secondary-grid">
            <button class="action-btn btn-outline" style="border-color:var(--primary); color:var(--primary); font-weight:600;" onclick="event.stopPropagation(); openJobPrep(this.closest('.job-card'))">Prep</button>
            <button class="action-btn btn-applied{btn_applied_active}" onclick="event.stopPropagation(); toggleAppliedJob(this, '{url}')">{btn_applied_text}</button>
            <button class="action-btn btn-saved{btn_saved_active}" onclick="event.stopPropagation(); toggleSaveJob(this, '{url}')">{btn_saved_text}</button>
            <button class="action-btn btn-archive{btn_archive_active}" onclick="event.stopPropagation(); toggleArchiveJob(this, '{url}')">{btn_archive_text}</button>
          </div>
          {f'<div class="asset-links">{resume_html} {cover_html}</div>' if (resume_html or cover_html) else ''}
        </div>
      </div>
    </div>"""


def render_copilot_drawer() -> str:
    """Render the slide-out Application Copilot Drawer HTML with Preparation Modal & Fact Verification."""
    return """
    <!-- Slide-out Application Copilot Drawer -->
    <div class="drawer-overlay" id="drawer-overlay" onclick="closeCopilotDrawer()"></div>
    <div class="copilot-drawer" id="copilot-drawer">
      <div class="drawer-header">
        <div id="drawer-company-logo-container" style="display:flex; align-items:center; gap:0.75rem; flex:1; min-width:0;">
          <div id="drawer-company-logo"></div>
          <div style="display:flex; flex-direction:column; gap:0.15rem; flex:1; min-width:0;">
            <div class="company-name" id="drawer-job-company">Company</div>
            <div class="drawer-title" id="drawer-job-title">Job Title</div>
          </div>
        </div>
        <button class="drawer-close" onclick="closeCopilotDrawer()">&times;</button>
      </div>

      <!-- Drawer Tabs Navigation -->
      <div class="drawer-nav-tabs" style="display:flex; border-bottom:1px solid var(--border-light); padding:0 1.5rem; background:var(--bg-app); gap:1.25rem;">
        <button id="tab-btn-details" class="drawer-nav-tab active" onclick="switchDrawerTab('details')" style="padding:0.7rem 0.2rem; font-size:0.83rem; font-weight:700; border:none; background:none; color:var(--primary); border-bottom:2px solid var(--primary); cursor:pointer;">Job Details</button>
        <button id="tab-btn-prep" class="drawer-nav-tab" onclick="switchDrawerTab('prep')" style="padding:0.7rem 0.2rem; font-size:0.83rem; font-weight:700; border:none; background:none; color:var(--text-secondary); border-bottom:2px solid transparent; cursor:pointer;">Application Prep &amp; Copilot</button>
      </div>

      <!-- Drawer Tags Bar (ABOVE Action Buttons Bar) -->
      <div class="drawer-tags-bar" style="padding:0.75rem 1.5rem 0.5rem 1.5rem; border-bottom:1px solid var(--border-subtle); display:flex; flex-direction:column; gap:0.5rem;">
        <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.5rem;" id="drawer-score-row">
          <div style="display:flex; align-items:center; gap:0.5rem; flex-wrap:wrap;">
            <div class="score-pill" id="drawer-job-score" style="display:none;"></div>
            <span class="posted-time-tag" id="drawer-job-posted-time"></span>
          </div>
          <div id="drawer-asset-links" class="asset-links"></div>
        </div>
        <div id="drawer-meta-tags" class="meta-tags"></div>
      </div>

      <!-- Action Buttons Bar -->
      <div class="drawer-actions-bar">
        <a href="#" target="_blank" id="drawer-btn-apply" class="action-btn btn-apply">Apply Now &rarr;</a>
        <button id="drawer-btn-prep-toggle" class="action-btn btn-outline" style="border-color:var(--primary); color:var(--primary); font-weight:600;" onclick="switchDrawerTab('prep')">Prepare Application</button>
        <button id="drawer-btn-applied" class="action-btn btn-applied" onclick="triggerDrawerApplied()">Mark Applied</button>
        <button id="drawer-btn-save" class="action-btn btn-saved" onclick="triggerDrawerSave()">Save</button>
        <button id="drawer-btn-archive" class="action-btn btn-archive" onclick="triggerDrawerArchive()">Archive</button>
      </div>

      <div class="drawer-body" style="padding:1.25rem 1.5rem; display:flex; flex-direction:column; gap:0.85rem;">
        <!-- TAB 1: Job Details -->
        <div id="drawer-tab-details" class="drawer-tab-content">
          <!-- Personal Notes Section -->
          <div class="copilot-section notes-section" style="background:var(--bg-app); border:1px solid var(--border-light); border-radius:10px; padding:1rem; display:flex; flex-direction:column; gap:0.75rem;">
            <div style="display:flex; justify-content:space-between; align-items:center;">
              <div class="copilot-section-title" style="margin:0; font-size:0.85rem; font-weight:700; color:var(--text-primary);">Personal Notes</div>
              <span id="drawer-notes-status" style="font-size:0.75rem; color:var(--text-muted); font-weight:500;"></span>
            </div>

            <div style="display:flex; flex-direction:column; gap:0.35rem;">
              <textarea id="drawer-crm-notes-input" class="form-input" rows="3" style="width:100%; padding:0.5rem; font-size:0.82rem; border-radius:6px; background:var(--bg-card); color:var(--text-primary); border:1px solid var(--border-light); resize:vertical;" placeholder="Add private notes... (e.g. Recruiter contact, salary expectations, interview date)" oninput="handleNotesInput()"></textarea>
            </div>
            <div style="display:flex; justify-content:flex-end;">
              <button class="action-btn btn-apply" style="padding:0.4rem 0.9rem; font-size:0.8rem; text-decoration:none;" onclick="saveJobNotesFromDrawer()">Save Notes</button>
            </div>
          </div>

          <div id="drawer-reasoning-section" style="display:flex; flex-direction:column; gap:0.35rem; margin-top:0.75rem;">
            <div class="keywords-row" id="drawer-keywords-row"></div>
            <div class="reasoning-row" id="drawer-reasoning-row"></div>
          </div>

          <!-- Full Job Description Section -->
          <div class="copilot-section" style="margin-top:0.75rem;">
            <div class="copilot-section-title">Job Description</div>
            <div id="drawer-full-description" class="drawer-full-desc-text"></div>
          </div>
        </div>

        <!-- TAB 2: Application Prep & 1-Click Copilot -->
        <div id="drawer-tab-prep" class="drawer-tab-content" style="display:none; flex-direction:column; gap:1.15rem;">
          <!-- Section A: On-Demand Tailoring -->
          <div class="prep-section" style="background:var(--bg-app); border:1px solid var(--border-light); border-radius:10px; padding:1rem; display:flex; flex-direction:column; gap:0.75rem;">
            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.5rem;">
              <div>
                <div style="font-size:0.9rem; font-weight:700; color:var(--text-primary);">On-Demand Document Tailoring</div>
                <div style="font-size:0.75rem; color:var(--text-muted);">Tailor your resume and cover letter for this specific role with anti-hallucination fact checking.</div>
              </div>
            </div>

            <div style="display:flex; gap:0.6rem; flex-wrap:wrap;">
              <button id="btn-prep-generate-resume" class="action-btn btn-apply" onclick="generateTailoredAsset('resume')" style="display:inline-flex; align-items:center; gap:0.4rem; padding:0.45rem 0.85rem; font-size:0.8rem;">
                <span>Generate Tailored Resume</span>
              </button>
              <button id="btn-prep-generate-cl" class="action-btn btn-outline" onclick="generateTailoredAsset('cover_letter')" style="display:inline-flex; align-items:center; gap:0.4rem; padding:0.45rem 0.85rem; font-size:0.8rem; border-color:var(--primary); color:var(--primary);">
                <span>Generate Cover Letter</span>
              </button>
            </div>

            <div id="prep-spinner" style="display:none; font-size:0.82rem; color:var(--primary); font-weight:600; padding:0.4rem 0;">
              Processing with AI and verifying career facts... Please wait a few seconds.
            </div>

            <!-- Fact-Check Verification Gate Status -->
            <div id="prep-fact-check-badge" style="display:none; padding:0.65rem 0.85rem; border-radius:8px; font-size:0.8rem;"></div>

            <!-- Tailored Resume Preview & Actions -->
            <div id="prep-resume-box" style="display:none; background:var(--bg-card); border:1px solid var(--border-light); border-radius:8px; padding:0.85rem; display:flex; flex-direction:column; gap:0.5rem;">
              <div style="display:flex; justify-content:space-between; align-items:center;">
                <span style="font-size:0.82rem; font-weight:700; color:var(--text-primary);">Tailored Resume</span>
                <div style="display:flex; gap:0.4rem;">
                  <button class="action-btn btn-outline" style="font-size:0.75rem; padding:0.25rem 0.6rem;" onclick="copyTailoredText('resume')">Copy Resume</button>
                  <a href="#" target="_blank" id="prep-resume-pdf-link" class="action-btn btn-outline" style="font-size:0.75rem; padding:0.25rem 0.6rem; display:none;">View PDF</a>
                </div>
              </div>
              <textarea id="prep-resume-text" class="form-input" rows="6" readonly style="width:100%; font-size:0.78rem; font-family:monospace; background:var(--bg-app); border:1px solid var(--border-light); border-radius:6px; padding:0.5rem;"></textarea>
            </div>

            <!-- Cover Letter Preview & Actions -->
            <div id="prep-cl-box" style="display:none; background:var(--bg-card); border:1px solid var(--border-light); border-radius:8px; padding:0.85rem; display:flex; flex-direction:column; gap:0.5rem;">
              <div style="display:flex; justify-content:space-between; align-items:center;">
                <span style="font-size:0.82rem; font-weight:700; color:var(--text-primary);">Cover Letter</span>
                <div style="display:flex; gap:0.4rem;">
                  <button class="action-btn btn-outline" style="font-size:0.75rem; padding:0.25rem 0.6rem;" onclick="copyTailoredText('cover_letter')">Copy Letter</button>
                  <a href="#" target="_blank" id="prep-cl-pdf-link" class="action-btn btn-outline" style="font-size:0.75rem; padding:0.25rem 0.6rem; display:none;">View PDF</a>
                </div>
              </div>
              <textarea id="prep-cl-text" class="form-input" rows="6" readonly style="width:100%; font-size:0.78rem; font-family:inherit; background:var(--bg-app); border:1px solid var(--border-light); border-radius:6px; padding:0.5rem;"></textarea>
            </div>
          </div>

          <!-- Section B: Candidate Profile 1-Click Copy Fields -->
          <div class="prep-section" style="background:var(--bg-app); border:1px solid var(--border-light); border-radius:10px; padding:1rem; display:flex; flex-direction:column; gap:0.75rem;">
            <div style="font-size:0.9rem; font-weight:700; color:var(--text-primary);">Candidate Profile (1-Click Copy)</div>
            <div style="display:grid; grid-template-columns:repeat(auto-fill, minmax(240px, 1fr)); gap:0.6rem;" id="prep-fields-grid">
              <!-- Dynamically populated candidate profile copy rows -->
            </div>
          </div>

          <!-- Section C: Common Application Questions -->
          <div class="prep-section" style="background:var(--bg-app); border:1px solid var(--border-light); border-radius:10px; padding:1rem; display:flex; flex-direction:column; gap:0.75rem;">
            <div style="font-size:0.9rem; font-weight:700; color:var(--text-primary);">Common Application Answers</div>
            <div style="display:flex; flex-direction:column; gap:0.65rem;" id="prep-qa-container">
              <!-- Dynamically populated Q&A items -->
            </div>
          </div>

          <!-- Section D: High-Match LinkedIn Outreach & Contact Strategy -->
          <div class="prep-section" id="prep-outreach-section" style="background:var(--bg-app); border:1px solid var(--border-light); border-radius:10px; padding:1rem; display:flex; flex-direction:column; gap:0.75rem;">
            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.5rem;">
              <div>
                <div style="font-size:0.9rem; font-weight:700; color:var(--text-primary); display:flex; align-items:center; gap:0.4rem;">
                  <span>LinkedIn Outreach Strategy &bull; Decision Makers</span>
                  <span id="prep-outreach-high-fit-badge" style="display:none; background:#ecfdf5; color:#047857; border:1px solid #a7f3d0; font-size:0.7rem; font-weight:700; padding:0.15rem 0.5rem; border-radius:12px;">High Match (&ge;80%)</span>
                </div>
                <div style="font-size:0.75rem; color:var(--text-muted);">Reach out directly to decision makers with Voice DNA matched templates and LinkedIn search links.</div>
              </div>
              <button id="btn-prep-generate-outreach" class="action-btn btn-outline" style="font-size:0.75rem; padding:0.25rem 0.6rem; border-color:var(--primary); color:var(--primary);" onclick="regenerateOutreachLLM()">Refine with AI</button>
            </div>

            <!-- Archetypes list / search links -->
            <div style="display:flex; flex-direction:column; gap:0.4rem;">
              <div style="font-size:0.78rem; font-weight:700; color:var(--text-secondary);">Decision Maker Archetypes:</div>
              <div id="prep-outreach-archetypes" style="display:grid; grid-template-columns:repeat(auto-fill, minmax(220px, 1fr)); gap:0.5rem;">
                <!-- Populated via JS -->
              </div>
            </div>

            <!-- 3 Message variations with quick copy and counters -->
            <div id="prep-outreach-messages" style="display:flex; flex-direction:column; gap:0.75rem; margin-top:0.35rem;">
              <!-- Populated via JS -->
            </div>
          </div>
        </div>
      </div>
    </div>"""


def render_search_manager_modal() -> str:
    """Render Search Manager Configuration modal dialog."""
    return """
    <!-- Search Manager Modal -->
    <div class="modal-overlay" id="search-manager-modal">
      <div class="modal-dialog">
        <div class="modal-header">
          <h3 class="modal-title" id="form-title">Edit Search Profile</h3>
          <button class="modal-close" onclick="closeSearchManagerModal()">&times;</button>
        </div>
        <div class="modal-body">
          <div id="modal-search-form" class="modal-form-container">
            <input type="hidden" id="form-search-id">
            <input type="hidden" id="form-search-country" value="worldwide">

            <!-- Section 1: Basic Information -->
            <div class="modal-section">
              <span class="modal-section-title">Basic Information</span>
              <div class="form-group">
                <label class="form-label">Search Profile Name</label>
                <input type="text" id="form-search-name" class="form-input" placeholder="e.g. Digital Project Manager - LATAM">
              </div>

              <div class="form-group">
                <label class="form-label">Job Title Queries (comma-separated)</label>
                <input type="text" id="form-search-queries" class="form-input" placeholder="e.g. Digital Product Manager, Project Manager">
              </div>

              <div class="form-grid-2col">
                <div class="form-group">
                  <label class="form-label">Country (comma-separated)</label>
                  <input type="text" id="form-search-locations" class="form-input" placeholder="e.g. United States, Remote, Latin America">
                </div>

                <div class="form-group">
                  <label class="form-label">Type</label>
                  <select id="form-search-types" class="form-input">
                    <option value="Any" selected>Any</option>
                    <option value="Remote">Remote</option>
                    <option value="Hybrid">Hybrid</option>
                    <option value="In-Person">In-Person</option>
                  </select>
                </div>
              </div>

              <div class="form-group">
                <label class="form-label">Blocked Job Posters (comma-separated)</label>
                <input type="text" id="form-search-blocked-posters" class="form-input" placeholder="e.g. BAIRESDEV, CyberCoders">
                <span class="form-help-text" style="font-size:0.75rem; color:var(--text-muted); margin-top:0.25rem; display:block;">List companies to block and exclude from this search profile (e.g. BAIRESDEV).</span>
              </div>
            </div>

            <!-- Section 2: Websites & Search Depth -->
            <div class="modal-section">
              <span class="modal-section-title">Websites & Search Depth</span>
              <div class="form-group">
                <label class="form-label">Target Job Boards</label>
                <div class="modal-checkbox-group">
                  <label style="display:flex; align-items:center; gap:0.3rem; cursor:pointer;"><input type="checkbox" id="form-site-linkedin" checked> LinkedIn</label>
                  <label style="display:flex; align-items:center; gap:0.3rem; cursor:pointer;"><input type="checkbox" id="form-site-indeed" checked> Indeed</label>
                  <label style="display:flex; align-items:center; gap:0.3rem; cursor:pointer;"><input type="checkbox" id="form-site-getonboard" checked> Get on Board</label>
                  <label style="display:flex; align-items:center; gap:0.3rem; cursor:pointer;"><input type="checkbox" id="form-site-remotive" checked> Remotive</label>
                  <label style="display:flex; align-items:center; gap:0.3rem; cursor:pointer;"><input type="checkbox" id="form-site-weworkremotely" checked> WeWorkRemotely</label>
                  <label style="display:flex; align-items:center; gap:0.3rem; cursor:pointer;"><input type="checkbox" id="form-site-hirewithnear" checked> Hire With Near</label>
                </div>
              </div>

              <div class="form-grid-3col">
                <div class="form-group">
                  <label class="form-label">Max Results / Site</label>
                  <select id="form-search-results-per-site" class="form-input">
                    <option value="25">25 Jobs</option>
                    <option value="50" selected>50 Jobs</option>
                    <option value="100">100 Jobs</option>
                  </select>
                </div>

                <div class="form-group">
                  <label class="form-label">Posting Recency</label>
                  <select id="form-search-hours-old" class="form-input">
                    <option value="24" selected>Past 24 Hours</option>
                    <option value="48">Past 48 Hours</option>
                    <option value="168">Past 7 Days</option>
                    <option value="720">Past 30 Days</option>
                  </select>
                </div>

                <div class="form-group" style="display:flex; flex-direction:column; justify-content:flex-end;">
                  <label class="modal-checkbox-label">
                    <input type="checkbox" id="form-search-easy-apply"> Easy Apply Only
                  </label>
                </div>
              </div>
            </div>

            <!-- Section 3: Score Filter & Match Visibility -->
            <div class="modal-section">
              <span class="modal-section-title">Score Filter &amp; Match Visibility</span>
              <div class="form-group">
                <label class="form-label">Visible Match Scores</label>
                <div class="modal-checkbox-group" id="form-score-filters-group" style="display:flex; flex-wrap:wrap; gap:1rem; margin-top:0.35rem;">
                  <label style="display:flex; align-items:center; gap:0.35rem; cursor:pointer; font-size:0.83rem;"><input type="checkbox" id="form-score-high" value="high" checked> High Match (85%+)</label>
                  <label style="display:flex; align-items:center; gap:0.35rem; cursor:pointer; font-size:0.83rem;"><input type="checkbox" id="form-score-solid" value="solid" checked> Solid Match (70–84%)</label>
                  <label style="display:flex; align-items:center; gap:0.35rem; cursor:pointer; font-size:0.83rem;"><input type="checkbox" id="form-score-moderate" value="moderate" checked> Moderate Match (50–69%)</label>
                  <label style="display:flex; align-items:center; gap:0.35rem; cursor:pointer; font-size:0.83rem;"><input type="checkbox" id="form-score-low" value="low"> Low Match (&lt;50%)</label>
                </div>
                <span class="form-help-text" style="font-size:0.75rem; color:var(--text-muted); margin-top:0.35rem; display:block;">Select which scored jobs to display on the dashboard (default: High, Solid, and Moderate match enabled, Low match hidden).</span>
              </div>
            </div>

            <!-- Section 4: Salary & Seniority Expectations -->
            <div class="modal-section">
              <span class="modal-section-title">Salary & Seniority Expectations</span>
              <div class="form-grid-2col">
                <div class="form-group">
                  <label class="form-label">Minimum Expected Salary</label>
                  <input type="text" id="form-search-min-salary" class="form-input" placeholder="e.g. $60,000 / year or $40/hr">
                </div>

                <div class="form-group">
                  <label class="form-label">Seniority Level</label>
                  <select id="form-search-experience-level" class="form-input">
                    <option value="any">Any Seniority Level</option>
                    <option value="entry">Entry Level</option>
                    <option value="mid_senior" selected>Mid-Senior Level</option>
                    <option value="director">Director / Lead</option>
                    <option value="executive">Executive</option>
                  </select>
                </div>
              </div>
            </div>

            <!-- Section 4: Candidate Profile for this Search -->
            <div class="modal-section">
              <span class="modal-section-title">Candidate Profile for this Search</span>
              <div class="form-grid-2col">
                <div class="form-group">
                  <label class="form-label">Candidate Name</label>
                  <input type="text" id="form-candidate-name" class="form-input" placeholder="e.g. Alex Morgan">
                </div>
                <div class="form-group">
                  <label class="form-label">Professional Title / Target Role</label>
                  <input type="text" id="form-candidate-title" class="form-input" placeholder="e.g. Senior Software Engineer / Product Manager">
                </div>
              </div>
              <div class="form-grid-2col">
                <div class="form-group">
                  <label class="form-label">Contact Email</label>
                  <input type="email" id="form-candidate-email" class="form-input" placeholder="e.g. alex.morgan@example.com">
                </div>
                <div class="form-group">
                  <label class="form-label">Phone / WhatsApp</label>
                  <input type="text" id="form-candidate-phone" class="form-input" placeholder="e.g. +1 (555) 234-5678">
                </div>
              </div>
              <div class="form-group">
                <label class="form-label">Core Skills &amp; Keywords (comma-separated)</label>
                <input type="text" id="form-candidate-skills" class="form-input" placeholder="e.g. Python, React, Cloud Architecture, CI/CD, Product Discovery">
              </div>
              <div class="form-group">
                <label class="form-label">Candidate Summary / Bio</label>
                <textarea id="form-candidate-summary" class="form-input" rows="3" style="resize:vertical;" placeholder="Brief summary of your career background and key strengths for this role..."></textarea>
              </div>
            </div>

            <!-- Section 5: Attach Resume PDF -->
            <div class="form-group">
              <label class="form-label">Attach / Replace PDF Resume</label>
              <div class="form-pdf-row">
                <input type="file" id="form-search-pdf-input" accept=".pdf" class="form-input" style="padding:0.35rem 0.5rem; font-size:0.8rem; flex:1; min-width:0;">
                <span id="form-pdf-status-badge" style="font-size:0.78rem; color:var(--text-muted); font-weight:600; white-space:nowrap;"></span>
                <a id="form-pdf-view-link" href="#" target="_blank" class="btn-sm btn-outline" style="display:none; padding:0.35rem 0.6rem; flex:none;">View PDF</a>
              </div>
            </div>

            <div style="display:flex; gap:0.5rem; justify-content:space-between; align-items:center;">
              <button id="form-delete-search-btn" class="btn-sm btn-danger" style="flex:none; display:none;" onclick="handleDeleteSearchFromForm()">Delete Search</button>
              <div style="display:flex; gap:0.5rem; margin-left:auto;">
                <button class="btn-sm btn-outline" style="flex:none;" onclick="handleCancelSearchForm()">Cancel</button>
                <button class="btn-sm btn-primary" style="flex:none;" onclick="saveSearchProfileFromModal()">Save Search</button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
    """


def generate_deleted_session_html(session_id: str = "") -> str:
    """Generate clean landing page for tombstoned / deleted sessions."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Session Deleted - DuckDuckWork</title>
    <link rel="icon" type="image/x-icon" href="/favicon.ico">
    <link rel="icon" type="image/png" sizes="96x96" href="/favicon-96x96.png">
    <link rel="icon" type="image/png" sizes="32x32" href="/favicon-32x32.png">
    <link rel="icon" type="image/png" sizes="16x16" href="/favicon-16x16.png">
    <link rel="icon" type="image/png" href="/favicon.png">
    <link rel="shortcut icon" type="image/png" href="/favicon.png">
    <link rel="shortcut icon" href="/favicon.ico">
    <link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">
    <style>
        :root {
            --bg-body: #0b0f19;
            --bg-card: #151d30;
            --border-light: rgba(255,255,255,0.1);
            --text-primary: #f3f4f6;
            --text-muted: #9ca3af;
            --primary: #2563eb;
            --primary-hover: #1d4ed8;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background-color: var(--bg-body);
            color: var(--text-primary);
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            margin: 0;
            padding: 1rem;
        }
        .card {
            background-color: var(--bg-card);
            border: 1px solid var(--border-light);
            border-radius: 12px;
            padding: 2.5rem 2rem;
            max-width: 440px;
            width: 100%;
            text-align: center;
            box-shadow: 0 20px 25px -5px rgba(0,0,0,0.5);
        }
        .icon-box {
            width: 56px;
            height: 56px;
            background: rgba(239, 68, 68, 0.1);
            border: 1px solid rgba(239, 68, 68, 0.2);
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            margin: 0 auto 1.25rem;
            color: #ef4444;
        }
        h1 {
            font-size: 1.25rem;
            font-weight: 700;
            margin: 0 0 0.75rem;
            color: var(--text-primary);
        }
        p {
            font-size: 0.9rem;
            color: var(--text-muted);
            margin: 0 0 1.75rem;
            line-height: 1.5;
        }
        .btn {
            display: inline-block;
            background-color: var(--primary);
            color: #ffffff;
            padding: 0.75rem 1.5rem;
            border-radius: 8px;
            font-weight: 600;
            font-size: 0.9rem;
            text-decoration: none;
            transition: background 0.2s;
        }
        .btn:hover {
            background-color: var(--primary-hover);
        }
    </style>
</head>
<body>
    <div class="card">
        <div class="icon-box">
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="15" y1="9" x2="9" y2="15"></line><line x1="9" y1="9" x2="15" y2="15"></line></svg>
        </div>
        <h1>Session Expired</h1>
        <p>This session has been deleted due to inactivity</p>
        <a href="/?new=1" class="btn" onclick="document.cookie='ddw_sid=; Path=/; Max-Age=0; SameSite=Lax'; try{localStorage.clear();}catch(e){}">Create New Session</a>
    </div>
</body>
</html>"""


def render_save_searches_modal() -> str:
    """Render Save My Searches modal dialog."""
    return """
    <!-- Save My Searches Modal -->
    <div class="modal-overlay" id="save-searches-modal" onclick="if(event.target===this)closeSaveSearchesModal()">
      <div class="modal-dialog" style="max-width:520px;">
        <div class="modal-header">
          <h3 class="modal-title">Save Your Unique URL</h3>
          <button class="modal-close" onclick="closeSaveSearchesModal()">&times;</button>
        </div>
        <div class="modal-body" style="display:flex; flex-direction:column; gap:1.2rem;">
          <div class="modal-notice-box">
            <strong style="color:var(--primary); font-size:0.92rem; display:block; margin-bottom:0.35rem;">No Registration Required</strong>
            DuckDuckWork does not require creating an account or password. Simply save your unique URL (or bookmark it) to return to your personalized searches, saved jobs, and notes at any time from any device.
          </div>

          <div class="form-group" style="display:flex; flex-direction:column; gap:0.6rem;">
            <label class="form-label" style="font-size:0.82rem; font-weight:700;">Your Personal Session Link</label>
            <input type="text" id="modal-save-searches-url-input" class="form-input" readonly style="width:100%; font-family:var(--font-mono); font-size:0.82rem; cursor:pointer; padding:0.6rem 0.75rem;" onclick="copySaveSearchesUrl()">
            <button class="btn-sm btn-primary" onclick="copySaveSearchesUrl()" style="width:100%; padding:0.6rem 1rem; font-weight:700; display:flex; align-items:center; justify-content:center; gap:0.4rem;">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"></path><rect x="8" y="2" width="8" height="4" rx="1" ry="1"></rect></svg>
              Copy URL
            </button>
          </div>

          <div style="border-top:1px solid var(--border-light); margin-top:0.25rem; padding-top:0.85rem; text-align:center;">
            <div class="session-retention-notice" style="background:rgba(234, 179, 8, 0.08); border:1px solid rgba(234, 179, 8, 0.25); border-radius:8px; padding:0.75rem 0.85rem; margin-bottom:0.85rem; text-align:left; font-size:0.82rem;">
              <strong style="color:var(--text-primary); font-size:0.85rem; display:block; margin-bottom:0.25rem;">Session Retention Policy</strong>
              <p style="margin:0; color:var(--text-muted); line-height:1.4;">
                Your session remains active as long as you visit regularly. Inactive sessions will have unclassified jobs cleared after 5 days and will be permanently deleted after 10 days of inactivity.
              </p>
            </div>
            <button type="button" class="btn-delete-all-data" onclick="handleDeleteAllUserData()" style="background:none; border:none; color:#ef4444; font-size:0.82rem; font-weight:600; cursor:pointer; text-decoration:underline; padding:0.4rem 0.6rem;">
              Delete My Searches and Data
            </button>
          </div>
        </div>
      </div>
    </div>
    """


def render_ai_config_modal() -> str:
    """Render Bring Your Own Key (BYOK) AI Configuration Modal dialog."""
    return """
    <!-- AI Provider & API Key Modal -->
    <div class="modal-overlay" id="ai-config-modal" onclick="if(event.target===this)closeAiConfigModal()" style="display:none;">
      <div class="modal-dialog" style="max-width:560px;">
        <div class="modal-header">
          <div style="display:flex; align-items:center; gap:0.5rem;">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="var(--primary)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/></svg>
            <h3 class="modal-title" style="margin:0;">Configure Artificial Intelligence Engine</h3>
          </div>
          <button class="modal-close" onclick="closeAiConfigModal()">&times;</button>
        </div>
        <div class="modal-body" style="display:flex; flex-direction:column; gap:1rem;">
          <p style="font-size:0.83rem; color:var(--text-muted); margin:0; line-height:1.45;">
            DuckDuckWork runs 100% locally on your machine with your own local database. Connect your preferred AI provider to enable job scoring, resume tailoring, and outreach cover letters.
          </p>

          <!-- Provider Tabs -->
          <div style="display:flex; gap:0.4rem; border-bottom:1px solid var(--border-light); padding-bottom:0.5rem; flex-wrap:wrap;">
            <button class="ai-provider-tab active" data-provider="gemini" onclick="switchAiProviderTab('gemini')" style="padding:0.4rem 0.75rem; font-size:0.8rem; font-weight:700; border-radius:6px; border:none; cursor:pointer; background:var(--bg-app); color:var(--primary);">Google Gemini (Free)</button>
            <button class="ai-provider-tab" data-provider="groq" onclick="switchAiProviderTab('groq')" style="padding:0.4rem 0.75rem; font-size:0.8rem; font-weight:600; border-radius:6px; border:none; cursor:pointer; background:none; color:var(--text-secondary);">Groq (Free)</button>
            <button class="ai-provider-tab" data-provider="openai" onclick="switchAiProviderTab('openai')" style="padding:0.4rem 0.75rem; font-size:0.8rem; font-weight:600; border-radius:6px; border:none; cursor:pointer; background:none; color:var(--text-secondary);">OpenAI / ChatGPT</button>
            <button class="ai-provider-tab" data-provider="ollama" onclick="switchAiProviderTab('ollama')" style="padding:0.4rem 0.75rem; font-size:0.8rem; font-weight:600; border-radius:6px; border:none; cursor:pointer; background:none; color:var(--text-secondary);">Ollama (Local)</button>
            <button class="ai-provider-tab" data-provider="custom" onclick="switchAiProviderTab('custom')" style="padding:0.4rem 0.75rem; font-size:0.8rem; font-weight:600; border-radius:6px; border:none; cursor:pointer; background:none; color:var(--text-secondary);">OpenRouter / Other</button>
          </div>

          <!-- Tab Content: Gemini -->
          <div id="ai-tab-gemini" class="ai-tab-panel" style="display:flex; flex-direction:column; gap:0.75rem;">
            <div style="background:rgba(16, 185, 129, 0.08); border:1px solid rgba(16, 185, 129, 0.25); border-radius:8px; padding:0.75rem; font-size:0.8rem; line-height:1.45;">
              <strong style="color:#059669; display:block; margin-bottom:0.2rem;">Recommended: 100% Free with no credit card required</strong>
              Google AI Studio provides up to <strong>1,500 daily requests free</strong> with Gemini 2.0 Flash.
              <div style="margin-top:0.5rem;">
                <a href="https://aistudio.google.com/app/apikey" target="_blank" rel="noopener noreferrer" class="btn-sm btn-primary" style="display:inline-flex; font-size:0.75rem; padding:0.3rem 0.7rem; text-decoration:none;">1. Get key at Google AI Studio &rarr;</a>
              </div>
            </div>
            <div class="form-group" style="display:flex; flex-direction:column; gap:0.35rem;">
              <label class="form-label" style="font-size:0.8rem; font-weight:700;">Google Gemini API Key</label>
              <input type="password" id="ai-key-gemini" class="form-input" placeholder="AIzaSy..." style="width:100%; font-family:var(--font-mono); font-size:0.82rem; padding:0.5rem 0.75rem;">
            </div>
          </div>

          <!-- Tab Content: Groq -->
          <div id="ai-tab-groq" class="ai-tab-panel" style="display:none; flex-direction:column; gap:0.75rem;">
            <div style="background:rgba(234, 179, 8, 0.08); border:1px solid rgba(234, 179, 8, 0.25); border-radius:8px; padding:0.75rem; font-size:0.8rem; line-height:1.45;">
              <strong style="color:#b45309; display:block; margin-bottom:0.2rem;">Ultra Fast Free Inference</strong>
              Groq Cloud provides fast inference with models like Llama 3.3 70B for free without requiring a credit card.
              <div style="margin-top:0.5rem;">
                <a href="https://console.groq.com/keys" target="_blank" rel="noopener noreferrer" class="btn-sm btn-primary" style="display:inline-flex; font-size:0.75rem; padding:0.3rem 0.7rem; text-decoration:none;">1. Get key at Groq Console &rarr;</a>
              </div>
            </div>
            <div class="form-group" style="display:flex; flex-direction:column; gap:0.35rem;">
              <label class="form-label" style="font-size:0.8rem; font-weight:700;">Groq API Key</label>
              <input type="password" id="ai-key-groq" class="form-input" placeholder="gsk_..." style="width:100%; font-family:var(--font-mono); font-size:0.82rem; padding:0.5rem 0.75rem;">
            </div>
          </div>

          <!-- Tab Content: OpenAI -->
          <div id="ai-tab-openai" class="ai-tab-panel" style="display:none; flex-direction:column; gap:0.75rem;">
            <div style="background:var(--bg-app); border:1px solid var(--border-light); border-radius:8px; padding:0.75rem; font-size:0.8rem; line-height:1.45;">
              <strong style="color:var(--text-primary); display:block; margin-bottom:0.2rem;">OpenAI Models (GPT-4o / GPT-4o-mini)</strong>
              Requires OpenAI API credit. With $1 USD you can score approximately 4,500 jobs using gpt-4o-mini.
              <div style="margin-top:0.5rem;">
                <a href="https://platform.openai.com/api-keys" target="_blank" rel="noopener noreferrer" class="btn-sm btn-outline" style="display:inline-flex; font-size:0.75rem; padding:0.3rem 0.7rem; text-decoration:none;">Open OpenAI Platform &rarr;</a>
              </div>
            </div>
            <div class="form-group" style="display:flex; flex-direction:column; gap:0.35rem;">
              <label class="form-label" style="font-size:0.8rem; font-weight:700;">OpenAI API Key</label>
              <input type="password" id="ai-key-openai" class="form-input" placeholder="sk-proj-..." style="width:100%; font-family:var(--font-mono); font-size:0.82rem; padding:0.5rem 0.75rem;">
            </div>
          </div>

          <!-- Tab Content: Ollama -->
          <div id="ai-tab-ollama" class="ai-tab-panel" style="display:none; flex-direction:column; gap:0.75rem;">
            <div style="background:rgba(99, 102, 241, 0.08); border:1px solid rgba(99, 102, 241, 0.25); border-radius:8px; padding:0.75rem; font-size:0.8rem; line-height:1.45;">
              <strong style="color:#4338ca; display:block; margin-bottom:0.2rem;">100% Offline and Private on your Computer</strong>
              Run Ollama on your local machine. No data leaves your computer and no API key is required.
              <pre style="background:var(--bg-card); padding:0.4rem; border-radius:4px; font-size:0.75rem; margin-top:0.4rem;">ollama run llama3.2</pre>
            </div>
            <div class="form-group" style="display:flex; flex-direction:column; gap:0.35rem;">
              <label class="form-label" style="font-size:0.8rem; font-weight:700;">Ollama Server URL</label>
              <input type="text" id="ai-url-ollama" class="form-input" value="http://localhost:11434/v1" style="width:100%; font-family:var(--font-mono); font-size:0.82rem; padding:0.5rem 0.75rem;">
            </div>
            <div class="form-group" style="display:flex; flex-direction:column; gap:0.35rem;">
              <label class="form-label" style="font-size:0.8rem; font-weight:700;">Model Name</label>
              <input type="text" id="ai-model-ollama" class="form-input" value="llama3.2" style="width:100%; font-family:var(--font-mono); font-size:0.82rem; padding:0.5rem 0.75rem;">
            </div>
          </div>

          <!-- Tab Content: Custom / OpenRouter -->
          <div id="ai-tab-custom" class="ai-tab-panel" style="display:none; flex-direction:column; gap:0.75rem;">
            <div class="form-group" style="display:flex; flex-direction:column; gap:0.35rem;">
              <label class="form-label" style="font-size:0.8rem; font-weight:700;">Base URL (OpenAI-compatible)</label>
              <input type="text" id="ai-url-custom" class="form-input" placeholder="https://openrouter.ai/api/v1" style="width:100%; font-family:var(--font-mono); font-size:0.82rem; padding:0.5rem 0.75rem;">
            </div>
            <div class="form-group" style="display:flex; flex-direction:column; gap:0.35rem;">
              <label class="form-label" style="font-size:0.8rem; font-weight:700;">API Key</label>
              <input type="password" id="ai-key-custom" class="form-input" placeholder="sk-..." style="width:100%; font-family:var(--font-mono); font-size:0.82rem; padding:0.5rem 0.75rem;">
            </div>
            <div class="form-group" style="display:flex; flex-direction:column; gap:0.35rem;">
              <label class="form-label" style="font-size:0.8rem; font-weight:700;">Model Name</label>
              <input type="text" id="ai-model-custom" class="form-input" placeholder="anthropic/claude-3.5-sonnet" style="width:100%; font-family:var(--font-mono); font-size:0.82rem; padding:0.5rem 0.75rem;">
            </div>
          </div>

          <!-- Status / Feedback Banner -->
          <div id="ai-config-feedback" style="display:none; padding:0.6rem 0.8rem; border-radius:6px; font-size:0.8rem;"></div>

          <!-- Action Buttons -->
          <div style="display:flex; justify-content:space-between; align-items:center; gap:0.5rem; margin-top:0.25rem;">
            <button type="button" class="btn-sm btn-outline" id="btn-test-ai" onclick="testAiConfigConnection()">Test Connection</button>
            <div style="display:flex; gap:0.5rem;">
              <button type="button" class="btn-sm btn-outline" onclick="closeAiConfigModal()">Cancel</button>
              <button type="button" class="btn-sm btn-primary" id="btn-save-ai" onclick="saveAiConfig()">Save &amp; Activate</button>
            </div>
          </div>
        </div>
      </div>
    </div>
    """


def render_pro_waitlist_modal() -> str:
    """Render the minimal Quota Limit Modal (Waitlist or Run Locally)."""
    return """
    <!-- Quota Limit Modal -->
    <div class="modal-overlay" id="pro-waitlist-modal" onclick="if(event.target===this)closeProWaitlistModal()" style="display:none;">
      <div class="modal-dialog" style="max-width:420px;">
        <div class="modal-header">
          <h3 class="modal-title" style="margin:0;" id="pro-modal-title">Free Search Limit Reached</h3>
          <button class="modal-close" onclick="closeProWaitlistModal()">&times;</button>
        </div>
        <div class="modal-body" style="display:flex; flex-direction:column; gap:0.85rem;">
          <p id="pro-modal-quota-desc" style="font-size:0.85rem; color:var(--text-secondary); margin:0; line-height:1.45;">
            Sign up for the waitlist to unlock +1 free search now, or download the open-source repo to run locally with no limits.
          </p>

          <div style="display:flex; flex-direction:column; gap:0.55rem;">
            <input type="email" id="pro-waitlist-email" class="form-input" placeholder="Enter your email..." style="font-size:0.88rem; padding:0.6rem 0.8rem;">
            <button type="button" id="btn-submit-waitlist" class="btn-sm btn-primary" onclick="submitProWaitlist()" style="width:100%; padding:0.65rem 1rem; font-weight:700; font-size:0.88rem; justify-content:center;">
              Join Waitlist (+1 Free Search)
            </button>
            <div id="pro-waitlist-feedback" style="display:none; font-size:0.8rem; padding:0.5rem 0.7rem; border-radius:6px;"></div>
          </div>

          <div style="display:flex; align-items:center; gap:0.6rem; color:var(--text-muted); font-size:0.75rem; text-transform:uppercase; letter-spacing:0.05em;">
            <span style="flex:1; height:1px; background:var(--border-light);"></span>
            <span>or</span>
            <span style="flex:1; height:1px; background:var(--border-light);"></span>
          </div>

          <a href="https://github.com/jorgecerda/duckduckwork" target="_blank" rel="noopener noreferrer" class="btn-sm btn-outline" onclick="trackTelemetry('github_clicks')" style="width:100%; justify-content:center; font-weight:600; padding:0.6rem 0.85rem; text-decoration:none; text-align:center; font-size:0.86rem;">
            Download Repo &amp; Run Locally for Free &rarr;
          </a>
        </div>
      </div>
    </div>
    """


def generate_dashboard() -> Path:
    """Query DB, compute counts, format modern HTML dashboard, write to disk."""
    app_dir = Path(os.environ.get("APPLYPILOT_DIR", str(APP_DIR)))
    db_path = app_dir / "applypilot.db"
    out_path = app_dir / "dashboard.html"

    conn = init_db(db_path)
    cur = conn.cursor()

    search_profiles = get_search_profiles()
    first_sp = search_profiles[0] if search_profiles else {}
    first_sp_id = str(first_sp.get("id") or "search_1")

    admin_cfg = {}
    base_data_dir = app_dir
    if base_data_dir.name == "sessions" or "sessions" in base_data_dir.parts:
        idx = base_data_dir.parts.index("sessions") if "sessions" in base_data_dir.parts else 0
        base_data_dir = Path(*base_data_dir.parts[:idx])
    cfg_file = base_data_dir / "admin_config.json"
    if cfg_file.exists():
        try:
            admin_cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    auto_run_enabled = bool(admin_cfg.get("auto_run_enabled", False))
    auto_run_interval = str(admin_cfg.get("auto_run_interval", "1h")).strip() or "1h"
    auto_run_checked = " checked" if auto_run_enabled else ""
    auto_run_status_text = "On" if auto_run_enabled else "Off"
    auto_run_status_color = "#10b981" if auto_run_enabled else "var(--text-muted, #64748b)"
    auto_run_wrapper_bg = "rgba(16, 185, 129, 0.1)" if auto_run_enabled else "var(--bg-card, #151d30)"
    auto_run_wrapper_border = "rgba(16, 185, 129, 0.45)" if auto_run_enabled else "var(--border-light, rgba(255, 255, 255, 0.15))"

    valid_sp_ids = {p.get("id") for p in search_profiles if p.get("id")}
    if first_sp_id:
        try:
            cur.execute("UPDATE jobs SET search_id = ? WHERE search_id IS NULL OR search_id = ''", (first_sp_id,))
            conn.commit()
        except Exception:
            pass

    cur.execute("""
        SELECT j.* FROM jobs j 
        ORDER BY 
            COALESCE(j.is_expired, 0) ASC,
            CASE WHEN COALESCE(j.repost_count, 1) >= 2 THEN 1 ELSE 0 END ASC,
            j.fit_score DESC, 
            j.discovered_at DESC
    """)
    jobs = [dict(row) for row in cur.fetchall()]

    for j in jobs:
        sid = j.get("search_id")
        if not sid or sid not in valid_sp_ids:
            j["search_id"] = first_sp_id

    profile_blocked_map = {
        str(p.get("id")): [str(bp).strip() for bp in (p.get("blocked_posters") or []) if str(bp).strip()]
        for p in search_profiles
    }
    first_sp_blocked = profile_blocked_map.get(first_sp_id, [])

    profile_score_map = {
        str(p.get("id")): [str(sf).strip().lower() for sf in (p.get("score_filters") or ["high", "solid", "moderate"]) if str(sf).strip()]
        for p in search_profiles
    }
    first_sp_scores = profile_score_map.get(first_sp_id, ["high", "solid", "moderate"])

    active_profile_jobs = [
        j for j in jobs
        if j.get("search_id") == first_sp_id
        and not is_company_blocked(j.get("company"), first_sp_blocked)
        and get_job_score_category(j.get("fit_score")) in first_sp_scores
    ]
    new_jobs_count = sum(1 for j in active_profile_jobs if not (j.get("is_archived") or j.get("apply_status") == "archived") and not (j.get("apply_status") == "applied" or j.get("applied_at") is not None) and not j.get("is_saved"))
    saved = sum(1 for j in active_profile_jobs if j.get("is_saved"))
    applied = sum(1 for j in active_profile_jobs if (j.get("apply_status") == "applied" or j.get("applied_at") is not None))
    archived = sum(1 for j in active_profile_jobs if (j.get("is_archived") or j.get("apply_status") == "archived"))
    search_profiles_json = json.dumps(search_profiles)
    search_bar_style = ' style="display:none"' if new_jobs_count == 0 else ''
    archive_all_style = ' style="display:inline-flex;"' if new_jobs_count > 0 else ' style="display:none;"'
    filters_block_style = ' style="display:none;"' if new_jobs_count == 0 else ''
    job_count_style = ' style="display:none;"' if new_jobs_count == 0 else ''

    site_counts: dict[str, int] = {"linkedin": 0, "indeed": 0, "getonboard": 0, "remotive": 0, "weworkremotely": 0, "hirewithnear": 0}
    company_counts: dict[str, int] = {}
    country_counts: dict[str, int] = {}
    wm_counts: dict[str, int] = {"Remote": 0, "Hybrid": 0, "On-site": 0}
    score_counts: dict[str, int] = {"high": 0, "solid": 0, "moderate": 0, "low": 0}

    for j in active_profile_jobs:
        raw_s = (j.get("site") or "").strip()
        if raw_s:
            s_lower = raw_s.lower()
            if "near" in s_lower or "hirewithnear" in s_lower:
                canonical = "hirewithnear"
            elif "get" in s_lower and "board" in s_lower:
                canonical = "getonboard"
            elif "weworkremotely" in s_lower or "wwr" in s_lower:
                canonical = "weworkremotely"
            elif "remotive" in s_lower:
                canonical = "remotive"
            elif "indeed" in s_lower:
                canonical = "indeed"
            elif "linkedin" in s_lower:
                canonical = "linkedin"
            else:
                canonical = raw_s
            site_counts[canonical] = site_counts.get(canonical, 0) + 1

        comp = (j.get("company") or "").strip()
        if comp:
            company_counts[comp] = company_counts.get(comp, 0) + 1

        c_name = (j.get("country") or "Worldwide").strip()
        country_counts[c_name] = country_counts.get(c_name, 0) + 1

        wm_name, _ = detect_work_mode(j.get("title") or "", j.get("location") or "", j.get("description") or "", j.get("full_description") or "")
        wm_counts[wm_name] = wm_counts.get(wm_name, 0) + 1

        sc = j.get("fit_score") if j.get("fit_score") is not None else 0
        if sc >= 85 or (sc >= 8 and sc <= 10):
            score_counts["high"] += 1
        elif sc >= 70 or sc == 7:
            score_counts["solid"] += 1
        elif sc >= 50 or sc in (5, 6):
            score_counts["moderate"] += 1
        else:
            score_counts["low"] += 1

    site_stats = [{"site": k, "count": v} for k, v in sorted(site_counts.items(), key=lambda item: item[1], reverse=True)]
    company_stats = [{"company": k, "count": v} for k, v in sorted(company_counts.items(), key=lambda item: item[1], reverse=True)]
    country_stats = [{"c_name": k, "count": v} for k, v in sorted(country_counts.items(), key=lambda item: item[1], reverse=True)]

    score_section_style = ' style="display: none;"' if new_jobs_count == 0 else ''
    source_section_style = ' style="display: none;"' if new_jobs_count == 0 else ''
    country_section_style = ' style="display: none;"' if new_jobs_count == 0 else ''
    workmode_section_style = ' style="display: none;"' if new_jobs_count == 0 else ''
    apptype_section_style = ' style="display: none;"' if new_jobs_count == 0 else ''
    company_section_style = ' style="display: none;"' if new_jobs_count == 0 else ''

    conn.close()

    colors = {
        "linkedin": "#0a66c2",
        "indeed": "#2164f3",
        "get on board": "#00ad8a",
        "getonboard": "#00ad8a",
        "remotive": "#513681",
        "weworkremotely": "#ef4444",
        "hirewithnear": "#1d4ed8",
        "hire with near": "#1d4ed8",
        "near": "#1d4ed8",
    }

    score_counts = {"high": 0, "solid": 0, "moderate": 0, "low": 0}
    for j in jobs:
        sc = j["fit_score"] if j["fit_score"] is not None else 0
        if sc >= 85 or (sc >= 8 and sc <= 10):
            cat = "high"
        elif sc >= 70 or sc == 7:
            cat = "solid"
        elif sc >= 50 or sc in (5, 6):
            cat = "moderate"
        else:
            cat = "low"
        score_counts[cat] = score_counts.get(cat, 0) + 1

    def render_checkbox_item(type_name: str, val: str, label_text: str, count: int, is_all: bool = False) -> str:
        active_cls = " active" if is_all else ""
        checked_attr = " checked" if is_all else ""
        val_lower = escape(val.lower())
        return f'''<label class="filter-checkbox-item{active_cls}" data-filter-type="{type_name}" data-filter-val="{val_lower}">
          <input type="checkbox" class="filter-checkbox-input" data-filter-type="{type_name}" data-filter-val="{val_lower}"{checked_attr} onchange="toggleCheckboxFilter('{type_name}', '{val_lower}', this)">
          <span class="custom-checkbox"></span>
          <span class="filter-label-text">{escape(label_text)}</span>
          <span class="btn-badge">{count}</span>
        </label>'''

    score_buttons_html = render_checkbox_item('scores', 'all', 'All Scores', len(active_profile_jobs), is_all=True)
    score_buttons_html += render_checkbox_item('scores', 'high', 'High Match (85%+)', score_counts.get("high", 0))
    score_buttons_html += render_checkbox_item('scores', 'solid', 'Solid Match (70-84%)', score_counts.get("solid", 0))
    score_buttons_html += render_checkbox_item('scores', 'moderate', 'Moderate (50-69%)', score_counts.get("moderate", 0))
    score_buttons_html += render_checkbox_item('scores', 'low', 'Low Match (<50%)', score_counts.get("low", 0))

    header_title = escape(str(first_sp.get("name") or "Search Profile"))
    header_updated_text = format_search_updated_text(first_sp.get("last_run"))
    q_str = ", ".join(first_sp.get("queries", [])) if isinstance(first_sp.get("queries"), list) else str(first_sp.get("queries") or "")
    l_str = ", ".join(first_sp.get("locations", [])) if isinstance(first_sp.get("locations"), list) else str(first_sp.get("locations") or "Worldwide")
    t_str = ", ".join(first_sp.get("job_types", [])) if isinstance(first_sp.get("job_types"), list) else str(first_sp.get("job_types") or "")

    hours_old = int(first_sp.get("hours_old", 24))
    if hours_old == 24:
        recency_text = "Past 24 hours"
    elif hours_old == 48:
        recency_text = "Past 48 hours"
    elif hours_old == 168:
        recency_text = "Past 7 days"
    elif hours_old == 720:
        recency_text = "Past 30 days"
    elif hours_old % 24 == 0 and hours_old > 0:
        recency_text = f"Past {hours_old // 24} days"
    else:
        recency_text = f"Past {hours_old} hours"

    header_sub = f"Targeting {l_str or 'Worldwide'}"
    if t_str:
        header_sub += f" ({t_str})"
    if q_str:
        header_sub += f" &middot; Queries: {q_str}"
    header_sub += f" &middot; {recency_text}"
    
    profile_new_counts: dict[str, int] = {}
    for j in jobs:
        sid = str(j.get("search_id") or "search_1")
        is_archived = bool(j.get("is_archived") or j.get("apply_status") == "archived")
        is_applied = bool(j.get("apply_status") == "applied" or j.get("applied_at") is not None)
        is_saved = bool(j.get("is_saved"))
        is_blocked = is_company_blocked(j.get("company"), profile_blocked_map.get(sid, []))
        score_cat = get_job_score_category(j.get("fit_score"))
        allowed_scores = profile_score_map.get(sid) or ["high", "solid", "moderate"]
        if not is_archived and not is_applied and not is_saved and not is_blocked and score_cat in allowed_scores:
            profile_new_counts[sid] = profile_new_counts.get(sid, 0) + 1

    search_tabs_html = ""
    for idx_sp, sp in enumerate(search_profiles):
        sp_id = escape(sp.get("id", f"search_{idx_sp+1}"))
        sp_name = escape(sp.get("name", "Search Profile"))
        active_cls = " active" if idx_sp == 0 else ""
        cnt = profile_new_counts.get(sp.get("id", f"search_{idx_sp+1}"), 0)
        badge_cls = "has-new" if cnt > 0 else "zero"
        badge_title = f"{cnt} new job{'s' if cnt != 1 else ''} available to review" if cnt > 0 else "No new jobs to review"
        badge_html = f'<span class="tab-new-badge {badge_cls}" title="{badge_title}">{cnt}</span>'
        search_tabs_html += f'<button class="search-tab-btn{active_cls}" data-profile-id="{sp_id}" draggable="true" ondragstart="handleTabDragStart(event)" ondragover="handleTabDragOver(event)" ondragenter="handleTabDragEnter(event)" ondragleave="handleTabDragLeave(event)" ondrop="handleTabDrop(event)" ondragend="handleTabDragEnd(event)" onclick="filterSearchProfile(\'{sp_id}\')">{badge_html}{sp_name}</button>'
    search_tabs_html += '<button class="search-tab-btn new-search-tab" draggable="false" onclick="openEditProfileForm(false)">+ New Search</button>'

    source_buttons_html = render_checkbox_item('sources', 'all', 'All Sources', len(active_profile_jobs), is_all=True)
    for s in site_stats:
        raw_site = s["site"] or "?"
        site_display = format_source_name(raw_site)
        cnt = s["count"]
        source_buttons_html += render_checkbox_item('sources', raw_site, site_display, cnt)

    country_buttons_html = render_checkbox_item('countries', 'all', 'All Countries', len(active_profile_jobs), is_all=True)
    for c in country_stats:
        c_name = c["c_name"] or "Worldwide"
        cnt = c["count"]
        country_buttons_html += render_checkbox_item('countries', c_name, c_name, cnt)

    wm_counts: dict[str, int] = {"Remote": 0, "Hybrid": 0, "On-site": 0}
    for j in active_profile_jobs:
        wm_name, _ = detect_work_mode(j["title"] or "", j["location"] or "", j["description"] or "", j["full_description"] or "")
        wm_counts[wm_name] = wm_counts.get(wm_name, 0) + 1

    workmode_buttons_html = render_checkbox_item('workmodes', 'all', 'All Types', len(active_profile_jobs), is_all=True)
    for wm_name in ["Remote", "Hybrid", "On-site"]:
        cnt = wm_counts.get(wm_name, 0)
        if cnt > 0:
            workmode_buttons_html += render_checkbox_item('workmodes', wm_name, wm_name, cnt)

    apptype_counts: dict[str, int] = {"easy_apply": 0, "website": 0}
    for j in active_profile_jobs:
        raw_app_url = (j.get("application_url") or "").strip()
        raw_site = (j.get("site") or "").strip().lower()
        strategy_val = (j.get("strategy") or "").lower()
        is_ea = (raw_site == "linkedin") and (
            ("easy" in strategy_val) or ("easy_apply" in strategy_val) or bool(j.get("is_easy_apply")) or (not raw_app_url or raw_app_url == "None")
        )
        if is_ea:
            apptype_counts["easy_apply"] += 1
        else:
            apptype_counts["website"] += 1

    apptype_buttons_html = render_checkbox_item('apptypes', 'all', 'All Types', len(active_profile_jobs), is_all=True)
    apptype_buttons_html += render_checkbox_item('apptypes', 'easy_apply', 'Easy Apply', apptype_counts.get("easy_apply", 0))
    apptype_buttons_html += render_checkbox_item('apptypes', 'website', 'On Website', apptype_counts.get("website", 0))

    company_buttons_html = render_checkbox_item('companies', 'all', 'All Posters', len(active_profile_jobs), is_all=True)
    for c in company_stats:
        comp_name = c.get("company") or "Direct Employer"
        cnt = c["count"]
        company_buttons_html += render_checkbox_item('companies', comp_name, comp_name, cnt)

    job_cards_html = ""
    for idx, j in enumerate(jobs):
        sid_j = str(j.get("search_id") or "search_1")
        job_cards_html += render_job_card(
            j, idx, colors, active_search_id=first_sp_id,
            blocked_posters=profile_blocked_map.get(sid_j, []),
            score_filters=profile_score_map.get(sid_j, ["high", "solid", "moderate"])
        )

    skeleton_cards_html = ""
    for _ in range(6):
        skeleton_cards_html += '''
        <div class="skeleton-card">
          <div class="skeleton-row">
            <div class="skeleton-block" style="width: 55%; height: 22px;"></div>
            <div class="skeleton-block" style="width: 75px; height: 24px; border-radius: 12px;"></div>
          </div>
          <div class="skeleton-block" style="width: 35%; height: 16px; margin-top: 4px;"></div>
          <div class="skeleton-row" style="margin-top: 8px;">
            <div style="display:flex; gap:8px;">
              <div class="skeleton-block" style="width: 80px; height: 22px; border-radius: 12px;"></div>
              <div class="skeleton-block" style="width: 90px; height: 22px; border-radius: 12px;"></div>
              <div class="skeleton-block" style="width: 70px; height: 22px; border-radius: 12px;"></div>
            </div>
            <div class="skeleton-block" style="width: 100px; height: 28px; border-radius: 6px;"></div>
          </div>
        </div>
        '''

    css_code = load_static_css()
    js_code = load_static_js()

    has_api_key = bool(
        os.environ.get("GEMINI_API_KEY", "").strip()
        or os.environ.get("OPENAI_API_KEY", "").strip()
        or os.environ.get("OPENROUTER_API_KEY", "").strip()
        or os.environ.get("LLM_API_KEY", "").strip()
        or os.environ.get("LLM_URL", "").strip()
        or (admin_cfg.get("gemini_api_key") or "").strip()
        or (admin_cfg.get("openai_api_key") or "").strip()
        or (admin_cfg.get("openrouter_api_key") or "").strip()
        or (admin_cfg.get("llm_api_key") or "").strip()
        or (admin_cfg.get("llm_url") or "").strip()
    )

    api_warning_banner_html = ""
    if not has_api_key:
        api_warning_banner_html = """
    <!-- Missing AI API Key Warning Banner -->
    <div class="api-warning-banner" id="api-warning-banner" style="display:flex;">
      <div class="api-warning-content">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0; color:#d97706;">
          <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path>
          <line x1="12" y1="9" x2="12" y2="13"></line>
          <line x1="12" y1="17" x2="12.01" y2="17"></line>
        </svg>
        <div class="api-warning-text">
          <strong style="font-weight:700;">AI Scoring Inactive:</strong> An API key is required to compute AI fit scores and tailor resumes. Job scraping runs normally.
        </div>
      </div>
      <div class="api-warning-actions">
        <a id="api-warning-settings-link" href="/admin#db" class="btn-api-warning-settings" title="Configure AI API Key in Settings">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path></svg>
          Configure in Settings
        </a>
        <button type="button" class="btn-api-warning-remind" onclick="dismissApiWarning(true)" title="Hide notification for 24 hours">Remind Me Later</button>
        <button type="button" class="btn-api-warning-close" onclick="dismissApiWarning(false)" title="Dismiss" aria-label="Dismiss">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
        </button>
      </div>
    </div>
    <script>
      (function() {
        var b = document.getElementById('api-warning-banner');
        if (b) {
          var dis = localStorage.getItem('ddw_api_banner_dismissed') === 'true';
          var rem = parseInt(localStorage.getItem('ddw_api_banner_remind_until') || '0', 10);
          if (dis || (rem && Date.now() < rem)) {
            b.style.display = 'none';
          }
          var sid = localStorage.getItem('applypilot_session_id');
          var link = document.getElementById('api-warning-settings-link');
          if (link && sid) {
            link.href = '/s/' + sid + '/admin#db';
          }
        }
      })();
    </script>
"""

    update_available_banner_html = """
    <!-- In-App System Update Banner (version 2) -->
    <div class="update-available-banner" id="update-available-banner" style="display:none;">
      <div class="update-banner-content">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0; color:#4f46e5;">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
          <polyline points="7 10 12 15 17 10"></polyline>
          <line x1="12" y1="15" x2="12" y2="3"></line>
        </svg>
        <div class="update-banner-text">
          <strong style="font-weight:700;">Update Available:</strong> There is a new version of DuckDuckWork (<span id="update-latest-version-badge">v0.0.0</span>).
        </div>
      </div>
      <div class="update-banner-actions">
        <a id="btn-update-whats-new" href="https://github.com/jorgecerda/duckduckwork/releases" target="_blank" rel="noopener noreferrer" class="btn-update-whats-new" title="View Release Notes on GitHub">
          What's New
        </a>
        <button type="button" id="btn-update-now" class="btn-update-now" onclick="triggerSystemUpdate()" title="Update DuckDuckWork to the latest version">
          <span id="btn-update-now-text">Update Now</span>
        </button>
        <button type="button" class="btn-update-remind" onclick="dismissUpdateBanner(true)" title="Remind me in 24 hours">Remind Me Later</button>
        <button type="button" class="btn-update-close" onclick="dismissUpdateBanner(false)" title="Dismiss update notification" aria-label="Dismiss">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
        </button>
      </div>
    </div>
"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<meta name="robots" content="noindex, nofollow, noarchive">
<title>DuckDuckWork - Your Searches</title>
<link rel="icon" type="image/x-icon" href="/favicon.ico">
<link rel="icon" type="image/png" sizes="96x96" href="/favicon-96x96.png">
<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32x32.png">
<link rel="icon" type="image/png" sizes="16x16" href="/favicon-16x16.png">
<link rel="icon" type="image/png" href="/favicon.png">
<link rel="shortcut icon" type="image/png" href="/favicon.png">
<link rel="shortcut icon" href="/favicon.ico">
<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">
<link rel="mask-icon" href="/static/images/safari-pinned-tab.svg" color="#6b21a8">
<meta name="theme-color" content="#ffffff" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#0f0c1b" media="(prefers-color-scheme: dark)">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700;800&family=Plus+Jakarta+Sans:wght@500;600;700;800&display=swap" rel="stylesheet">
<script>
(function() {{
  var path = window.location.pathname;
  var search = window.location.search;
  var sidParam = new URLSearchParams(search).get('s');
  var pathSegments = path.split('/').filter(Boolean);
  var reserved = ['static', 'api', 'dashboard.html', 'index.html', 'resumes', 'logs'];
  var pathSid = null;
  if (pathSegments.length === 2 && pathSegments[0] === 's') {{
    pathSid = pathSegments[1];
  }} else if (pathSegments.length === 1 && reserved.indexOf(pathSegments[0]) === -1 && pathSegments[0] !== 's') {{
    pathSid = pathSegments[0];
  }}
  var currentSid = pathSid || sidParam;
  
  if (currentSid) {{
    localStorage.setItem('applypilot_session_id', currentSid);
  }}

  var mode = localStorage.getItem('applypilot_theme_mode') || 'system';
  var effective = mode;
  if (mode === 'system') {{
    effective = (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light';
  }}
  document.documentElement.setAttribute('data-theme', effective);
  window.allSearchProfiles = {search_profiles_json};
}})();
</script>
<style>
{css_code}
</style>
</head>
<body>

<div class="app-container">

  <!-- Left Sidebar Panel -->
  <aside class="sidebar">
    <div class="brand" style="display:flex; align-items:center; justify-content:space-between; width:100%;">
      <div style="display:flex; align-items:center; gap:0.75rem;">
        <img src="/static/images/duckduckwork_logo.png" alt="DuckDuckWork" style="width:36px; height:36px; border-radius:50%; object-fit:cover;" width="36" height="36" loading="eager" fetchpriority="high">
        <div class="brand-title" style="font-weight:700; font-size:1.2rem; color:var(--text-primary);">DuckDuckWork</div>
      </div>
      <div class="theme-switcher-wrapper mobile-theme-switcher">
        <div class="theme-segmented-control" id="theme-segmented-control-mobile">
          <button type="button" class="theme-segment-btn" data-theme-btn="system" onclick="setThemeMode('system')" title="System Theme">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2" ry="2"></rect><line x1="8" y1="21" x2="16" y2="21"></line><line x1="12" y1="17" x2="12" y2="21"></line></svg>
          </button>
          <button type="button" class="theme-segment-btn" data-theme-btn="light" onclick="setThemeMode('light')" title="Light Mode">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"></circle><line x1="12" y1="1" x2="12" y2="3"></line><line x1="12" y1="21" x2="12" y2="23"></line><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"></line><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"></line><line x1="1" y1="12" x2="3" y2="12"></line><line x1="21" y1="12" x2="23" y2="12"></line><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"></line><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"></line></svg>
          </button>
          <button type="button" class="theme-segment-btn" data-theme-btn="dark" onclick="setThemeMode('dark')" title="Dark Mode">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path></svg>
          </button>
        </div>
      </div>
    </div>

    <!-- 3 Buttons on Sidebar: Run All | Auto Search: On/Off, Settings -->
    <div class="sidebar-btn-grid" style="display:grid; grid-template-columns: 1fr 1fr; gap:0.5rem; margin-top:0.35rem; margin-bottom:0.45rem; width:100%;">
      <button id="sidebar-run-all-btn" class="btn-sm btn-primary btn-run-all" style="display:inline-flex; align-items:center; justify-content:center; gap:0.35rem; font-weight:600; padding:0.45rem 0.4rem; border-radius:8px; cursor:pointer; width:100%; white-space:nowrap;" onclick="triggerRunAllSearches()" title="Run all searches now">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>
        <span id="sidebar-run-all-text">Run All</span>
      </button>

      <div class="auto-run-sidebar-btn" id="sidebar-auto-search-wrapper" style="display:inline-flex; align-items:center; justify-content:space-between; gap:0.3rem; padding:0.42rem 0.5rem; background:{auto_run_wrapper_bg}; border:1px solid {auto_run_wrapper_border}; border-radius:8px; cursor:pointer; width:100%; user-select:none; box-sizing:border-box;" onclick="toggleAutoRunFromSidebar()" title="Toggle automatic background searches">
        <div style="display:flex; flex-direction:column; align-items:flex-start; line-height:1.15; min-width:0;">
          <span style="font-size:0.67rem; font-weight:700; color:var(--text-secondary, #94a3b8); letter-spacing:0.01em; white-space:nowrap;">Auto Search</span>
          <span id="sidebar-auto-run-status-text" style="font-size:0.62rem; font-weight:700; color:{auto_run_status_color};">{auto_run_status_text}</span>
        </div>
        <div style="display:flex; align-items:center; gap:0.25rem; flex-shrink:0;">
          <span id="auto-run-interval-badge-sidebar" class="auto-run-interval-badge" onclick="event.stopPropagation(); window.open('/admin#db', '_blank');" style="font-size:0.62rem; font-weight:700; padding:1px 5px; border-radius:9999px;" title="Configured interval. Click to open Settings.">{auto_run_interval}</span>
          <label class="auto-run-switch" style="width:26px; height:15px; margin:0; flex-shrink:0;" onclick="event.stopPropagation();">
            <input type="checkbox" id="auto-run-toggle-sidebar"{auto_run_checked} onchange="toggleAutoRun(this.checked)">
            <span class="auto-run-slider" style="border-radius:15px;"></span>
          </label>
        </div>
      </div>
    </div>

    <!-- Navigation Buttons: Analytics & Settings -->
    <div class="sidebar-nav-btn-grid" style="display:grid; grid-template-columns: 1fr 1fr; gap:0.5rem; margin-top:0; margin-bottom:0.75rem; width:100%;">
      <a id="sidebar-analytics-btn" href="/analytics" class="btn-sm btn-outline btn-analytics" style="display:inline-flex; align-items:center; justify-content:center; gap:0.4rem; font-weight:600; padding:0.5rem 0.5rem; border-radius:8px; text-decoration:none; font-size:0.82rem;">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"></line><line x1="12" y1="20" x2="12" y2="4"></line><line x1="6" y1="20" x2="6" y2="14"></line></svg>
        Analytics
      </a>
      <a id="sidebar-settings-btn" href="/admin" class="btn-sm btn-outline btn-settings" style="display:inline-flex; align-items:center; justify-content:center; gap:0.4rem; font-weight:600; padding:0.5rem 0.5rem; border-radius:8px; text-decoration:none; font-size:0.82rem;">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path></svg>
        Settings
      </a>
    </div>

    <!-- 2. Block 1: Application Status Section -->
    <div class="sidebar-section application-status-section">
      <div class="section-title-header" onclick="toggleSidebarSection(this)">
        <span class="section-title">Application Status</span>
        <span class="section-toggle-icon">+</span>
      </div>
      <div class="section-content">
        <nav class="status-nav">
          <button class="nav-item active" data-status="all" onclick="filterStatus('all', this)">
            <span>New Jobs</span>
            <span class="nav-count" id="count-all">{new_jobs_count}</span>
          </button>
          <button class="nav-item" data-status="saved" onclick="filterStatus('saved', this)">
            <span>Saved</span>
            <span class="nav-count" id="count-saved">{saved}</span>
          </button>
          <button class="nav-item" data-status="applied" onclick="filterStatus('applied', this)">
            <span>Applied</span>
            <span class="nav-count" id="count-applied">{applied}</span>
          </button>
          <button class="nav-item" data-status="archived" onclick="filterStatus('archived', this)">
            <span>Archived</span>
            <span class="nav-count" id="count-archived">{archived}</span>
          </button>
        </nav>
      </div>
    </div>

    <!-- 3. Block 2: Filters Section (Single block with sub-sections) -->
    <div class="sidebar-section main-filters-section" id="main-filters-section"{filters_block_style}>
      <div class="section-title-header" onclick="toggleSidebarSection(this)">
        <span class="section-title">Filters</span>
        <span class="section-toggle-icon">+</span>
      </div>
      <div class="section-content">
        <div class="sub-filter-container" style="display:flex; flex-direction:column; gap:0.75rem;">

          <!-- Score Sub-Section -->
          <div class="filter-sub-section"{score_section_style}>
            <div class="filter-sub-header" onclick="toggleSubSection(this)">
              <span class="filter-sub-title">Score</span>
              <div style="display:flex; align-items:center; gap:0.4rem;">
                <div style="display:flex; gap:0.3rem;" onclick="event.stopPropagation()">
                  <button class="btn-sm btn-outline" style="padding:0.1rem 0.4rem; font-size:0.7rem;" onclick="selectAllMultiFilter('scores')">All</button>
                  <button class="btn-sm btn-outline" style="padding:0.1rem 0.4rem; font-size:0.7rem;" onclick="clearMultiFilter('scores')">Clear</button>
                </div>
                <span class="sub-toggle-icon">&minus;</span>
              </div>
            </div>
            <div class="filter-sub-content">
              <div class="filter-btn-grid">
                {score_buttons_html}
              </div>
            </div>
          </div>

          <!-- Sources Sub-Section -->
          <div class="filter-sub-section"{source_section_style}>
            <div class="filter-sub-header" onclick="toggleSubSection(this)">
              <span class="filter-sub-title">Sources ({len(site_stats)})</span>
              <div style="display:flex; align-items:center; gap:0.4rem;">
                <div style="display:flex; gap:0.3rem;" onclick="event.stopPropagation()">
                  <button class="btn-sm btn-outline" style="padding:0.1rem 0.4rem; font-size:0.7rem;" onclick="selectAllMultiFilter('sources')">All</button>
                  <button class="btn-sm btn-outline" style="padding:0.1rem 0.4rem; font-size:0.7rem;" onclick="clearMultiFilter('sources')">Clear</button>
                </div>
                <span class="sub-toggle-icon">&minus;</span>
              </div>
            </div>
            <div class="filter-sub-content">
              <div class="filter-btn-grid" id="filter-grid-sources">
                {source_buttons_html}
              </div>
            </div>
          </div>

          <!-- Country Sub-Section -->
          <div class="filter-sub-section"{country_section_style}>
            <div class="filter-sub-header" onclick="toggleSubSection(this)">
              <span class="filter-sub-title">Country ({len(country_stats)})</span>
              <div style="display:flex; align-items:center; gap:0.4rem;">
                <div style="display:flex; gap:0.3rem;" onclick="event.stopPropagation()">
                  <button class="btn-sm btn-outline" style="padding:0.1rem 0.4rem; font-size:0.7rem;" onclick="selectAllMultiFilter('countries')">All</button>
                  <button class="btn-sm btn-outline" style="padding:0.1rem 0.4rem; font-size:0.7rem;" onclick="clearMultiFilter('countries')">Clear</button>
                </div>
                <span class="sub-toggle-icon">&minus;</span>
              </div>
            </div>
            <div class="filter-sub-content">
              <div class="filter-btn-grid" id="filter-grid-countries">
                {country_buttons_html}
              </div>
            </div>
          </div>

          <!-- Location Type Sub-Section -->
          <div class="filter-sub-section"{workmode_section_style}>
            <div class="filter-sub-header" onclick="toggleSubSection(this)">
              <span class="filter-sub-title">Location Type</span>
              <div style="display:flex; align-items:center; gap:0.4rem;">
                <div style="display:flex; gap:0.3rem;" onclick="event.stopPropagation()">
                  <button class="btn-sm btn-outline" style="padding:0.1rem 0.4rem; font-size:0.7rem;" onclick="selectAllMultiFilter('workmodes')">All</button>
                  <button class="btn-sm btn-outline" style="padding:0.1rem 0.4rem; font-size:0.7rem;" onclick="clearMultiFilter('workmodes')">Clear</button>
                </div>
                <span class="sub-toggle-icon">&minus;</span>
              </div>
            </div>
            <div class="filter-sub-content">
              <div class="filter-btn-grid" id="filter-grid-workmodes">
                {workmode_buttons_html}
              </div>
            </div>
          </div>

          <!-- Application Type Sub-Section -->
          <div class="filter-sub-section"{apptype_section_style}>
            <div class="filter-sub-header" onclick="toggleSubSection(this)">
              <span class="filter-sub-title">Application Type</span>
              <div style="display:flex; align-items:center; gap:0.4rem;">
                <div style="display:flex; gap:0.3rem;" onclick="event.stopPropagation()">
                  <button class="btn-sm btn-outline" style="padding:0.1rem 0.4rem; font-size:0.7rem;" onclick="selectAllMultiFilter('apptypes')">All</button>
                  <button class="btn-sm btn-outline" style="padding:0.1rem 0.4rem; font-size:0.7rem;" onclick="clearMultiFilter('apptypes')">Clear</button>
                </div>
                <span class="sub-toggle-icon">&minus;</span>
              </div>
            </div>
            <div class="filter-sub-content">
              <div class="filter-btn-grid" id="filter-grid-apptypes">
                {apptype_buttons_html}
              </div>
            </div>
          </div>

          <!-- Job Poster Sub-Section -->
          <div class="filter-sub-section"{company_section_style}>
            <div class="filter-sub-header" onclick="toggleSubSection(this)">
              <span class="filter-sub-title">Job Poster ({len(company_stats)})</span>
              <div style="display:flex; align-items:center; gap:0.4rem;">
                <div style="display:flex; gap:0.3rem;" onclick="event.stopPropagation()">
                  <button class="btn-sm btn-outline" style="padding:0.1rem 0.4rem; font-size:0.7rem;" onclick="selectAllMultiFilter('companies')">All</button>
                  <button class="btn-sm btn-outline" style="padding:0.1rem 0.4rem; font-size:0.7rem;" onclick="clearMultiFilter('companies')">Clear</button>
                </div>
                <span class="sub-toggle-icon">&minus;</span>
              </div>
            </div>
            <div class="filter-sub-content">
              <div class="filter-btn-grid" id="filter-grid-companies">
                {company_buttons_html}
              </div>
            </div>
          </div>

        </div>
      </div>
    </div>
  </aside>

  <!-- Right Main Content Panel -->
  <main class="main-content">

    {update_available_banner_html}
    {api_warning_banner_html}

    <!-- Top Horizontal Search Profile Tabs Bar -->
    <div class="top-tabs-row" style="display:flex; justify-content:space-between; align-items:center; width:100%; gap:1rem; margin-bottom:1rem; flex-wrap:wrap;">
      <div class="search-tabs-bar" id="search-tabs-bar" style="margin-bottom:0; flex:1;">
        {search_tabs_html}
      </div>
      <div style="display:flex; align-items:center; gap:0.6rem; flex-wrap:wrap;">
        <div class="desktop-theme-switcher theme-switcher-wrapper">
          <div class="theme-segmented-control" id="theme-segmented-control-desktop">
            <button type="button" class="theme-segment-btn" data-theme-btn="system" onclick="setThemeMode('system')" title="System Theme">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2" ry="2"></rect><line x1="8" y1="21" x2="16" y2="21"></line><line x1="12" y1="17" x2="12" y2="21"></line></svg>
            </button>
            <button type="button" class="theme-segment-btn" data-theme-btn="light" onclick="setThemeMode('light')" title="Light Mode">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"></circle><line x1="12" y1="1" x2="12" y2="3"></line><line x1="12" y1="21" x2="12" y2="23"></line><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"></line><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"></line><line x1="1" y1="12" x2="3" y2="12"></line><line x1="21" y1="12" x2="23" y2="12"></line><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"></line><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"></line></svg>
            </button>
            <button type="button" class="theme-segment-btn" data-theme-btn="dark" onclick="setThemeMode('dark')" title="Dark Mode">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path></svg>
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Runner Status Banner (Placed below tabs and before search title, without Dismiss button) -->
    <div class="runner-status-banner" id="runner-status-banner" style="margin-bottom: 0.85rem; display: none;">
      <div class="spinner"></div>
      <span id="runner-status-text">Searching job listings across enabled sources...</span>
      <div id="runner-progress-bar-wrap" style="display:none; margin-left:auto; width:160px; height:8px; background:rgba(79, 70, 229, 0.15); border-radius:9999px; overflow:hidden;">
        <div id="runner-progress-bar-fill" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0" style="width:0%; height:100%; background:var(--primary); transition:width 0.3s ease; border-radius:9999px;"></div>
      </div>
    </div>

    <div class="content-header" style="display:flex; flex-direction:column; gap:0.6rem; align-items:flex-start;">
      <div class="header-left" style="display:flex; flex-direction:column; gap:0.25rem; width:100%;">
        <div class="search-updated-timestamp" id="search-updated-timestamp">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>
          <span id="search-updated-timestamp-text">{header_updated_text}</span>
        </div>
        <h2 id="main-panel-title">{header_title}</h2>
        <p id="main-panel-subtitle">{header_sub}</p>
        <div style="display:flex; align-items:center; gap:0.6rem; flex-wrap:wrap; margin-top:0.35rem;">
          <button class="btn-sm btn-primary" id="header-run-search-btn" style="display:inline-flex;" onclick="triggerRunSearch()">Run Search</button>
          <button class="btn-sm btn-outline" id="header-edit-search-btn" style="display:inline-flex;" onclick="triggerEditCurrentSearch()">Edit Search</button>
          <button class="btn-sm btn-outline" id="header-archive-all-btn"{archive_all_style} onclick="triggerArchiveAllJobs()">Archive All</button>
          <div class="job-count-pill" id="job-count"{job_count_style}>Showing {new_jobs_count} jobs</div>
        </div>
      </div>
    </div>
    <!-- Full-Width Search Input Bar & Sort Dropdown inside Right Panel above cards -->
    <div class="main-search-bar-container"{search_bar_style}>
      <div class="main-search-box">
        <svg class="search-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
        <input type="text" class="main-search-input" id="main-search-input" placeholder="Search roles by title, company, skills, or location..." oninput="filterText(this.value)">
      </div>
      <div class="sort-dropdown-wrapper" id="sort-dropdown-wrapper">
        <span class="sort-label">Sort by:</span>
        <div class="sort-dropdown" id="sort-dropdown">
          <button type="button" class="sort-dropdown-trigger" id="sort-dropdown-trigger" onclick="toggleSortDropdown(event)">
            <span id="sort-current-label">Relevance</span>
            <svg id="sort-dir-icon" class="sort-dir-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" title="Toggle direction" onclick="toggleSortDirectionFromTrigger(event)"><line x1="12" y1="5" x2="12" y2="19"></line><polyline points="19 12 12 19 5 12"></polyline></svg>
            <svg class="dropdown-chevron" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"></polyline></svg>
          </button>
          <div class="sort-dropdown-menu" id="sort-dropdown-menu">
            <div class="sort-dropdown-item active" data-sort-field="score" onclick="selectSortField('score', 'Relevance')">
              <span>Relevance</span>
              <svg class="item-dir-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"></line><polyline points="19 12 12 19 5 12"></polyline></svg>
            </div>
            <div class="sort-dropdown-item" data-sort-field="date" onclick="selectSortField('date', 'Date')">
              <span>Date</span>
              <svg class="item-dir-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"></line><polyline points="19 12 12 19 5 12"></polyline></svg>
            </div>
            <div class="sort-dropdown-item" data-sort-field="company" onclick="selectSortField('company', 'Company')">
              <span>Company</span>
              <svg class="item-dir-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"></line><polyline points="19 12 12 19 5 12"></polyline></svg>
            </div>
            <div class="sort-dropdown-item" data-sort-field="title" onclick="selectSortField('title', 'Title')">
              <span>Title</span>
              <svg class="item-dir-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"></line><polyline points="19 12 12 19 5 12"></polyline></svg>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="skeleton-grid" id="skeleton-grid-container" style="display:{'flex' if len(jobs) == 0 else 'none'};">
      {skeleton_cards_html}
    </div>

    <div class="job-grid" id="job-grid-container" style="display:{'none' if new_jobs_count == 0 else 'flex'};">
      {job_cards_html}
    </div>

    <!-- Empty state when 0 jobs are visible for a search profile -->
    <div id="no-jobs-empty-state" class="empty-state-card" style="display:{'block' if (len(jobs) > 0 and new_jobs_count == 0) else 'none'}; text-align:center; padding:1.25rem 1rem; background:var(--bg-card, #ffffff); border:1px solid var(--border-color, #cbd5e1); border-radius:10px; margin-top:0.75rem; box-shadow:0 1px 2px rgba(0,0,0,0.04);">
      <h3 style="font-size:0.95rem; font-weight:700; color:var(--text-primary, #0f172a); margin-bottom:0.85rem;">No jobs found for this search profile</h3>
      <div style="display:flex; align-items:center; justify-content:center; gap:0.6rem; flex-wrap:wrap;">
        <button class="btn-sm btn-primary" onclick="triggerRunSearch()" style="display:inline-flex; align-items:center; gap:0.4rem; font-weight:600; padding:0.45rem 0.9rem;">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"></polyline><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path></svg>
          Run Search Now
        </button>
        <button class="btn-sm btn-outline" onclick="openEditProfileForm()" style="display:inline-flex; align-items:center; gap:0.4rem; font-weight:600; padding:0.45rem 0.9rem;">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
          Create New Search
        </button>
      </div>
    </div>

    <!-- Simple Empty State Card when search query / filters return 0 results -->
    <div id="no-search-results-card" class="empty-state-card" style="display:none; text-align:center; padding:1.5rem 1rem; background:var(--bg-card, #ffffff); border:1px solid var(--border-color, #cbd5e1); border-radius:10px; margin-top:0.75rem; box-shadow:0 1px 2px rgba(0,0,0,0.04);">
      <h3 style="font-size:0.95rem; font-weight:700; color:var(--text-primary, #0f172a); margin-bottom:0.4rem;" id="no-search-results-title">No jobs match your search</h3>
      <p style="font-size:0.85rem; color:var(--text-secondary, #64748b); margin-bottom:0.85rem;" id="no-search-results-sub">Try searching with a different keyword or clear your current search filter.</p>
      <div style="display:flex; align-items:center; justify-content:center;">
        <button class="btn-sm btn-outline" onclick="clearSearchInput()" style="display:inline-flex; align-items:center; gap:0.4rem; font-weight:600; padding:0.4rem 0.85rem;">
          Clear Search
        </button>
      </div>
    </div>
  </main>

</div>

{render_copilot_drawer()}
{render_search_manager_modal()}
{render_ai_config_modal()}
{render_pro_waitlist_modal()}

<script>
window.allSearchProfiles = {json.dumps(search_profiles)};
{js_code}
</script>
</body>
</html>
"""

    out_path.write_text(html, encoding="utf-8")
    return out_path


def open_dashboard(no_open=False):
    """Generate and open dashboard in browser."""
    path = generate_dashboard()
    console.print(f"[bold green]Dashboard written to {path}[/bold green]")
    if not no_open:
        webbrowser.open(path.as_uri())


def generate_onboarding_html() -> str:
    """Generate the 3-step welcome onboarding HTML for new users."""
    return """<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
  <meta http-equiv="Pragma" content="no-cache">
  <meta http-equiv="Expires" content="0">
  <title>Welcome to DuckDuckWork - AI Job Search &amp; Copilot</title>
  <link rel="icon" type="image/png" sizes="96x96" href="/favicon-96x96.png">
  <link rel="icon" type="image/png" href="/favicon.png">
  <link rel="shortcut icon" type="image/png" href="/favicon.png">
  <link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">
  <link rel="mask-icon" href="/static/images/safari-pinned-tab.svg" color="#6b21a8">
  <meta name="theme-color" content="#ffffff" media="(prefers-color-scheme: light)">
  <meta name="theme-color" content="#0f0c1b" media="(prefers-color-scheme: dark)">

  <style>
    :root {
      --bg: #f6f3fb;
      --card-bg: #ffffff;
      --card-border: #e9d5ff;
      --text: #0f172a;
      --text-muted: #64748b;
      --primary: #6b21a8;
      --primary-hover: #581c87;
      --accent-green: #10b981;
      --input-bg: #ffffff;
      --input-border: #cbd5e1;
      --radius-lg: 14px;
      --radius-md: 8px;
    }

    [data-theme="dark"] {
      --bg: #130b20;
      --card-bg: rgba(29, 18, 47, 0.92);
      --card-border: rgba(167, 139, 250, 0.25);
      --text: #f8fafc;
      --text-muted: #d8b4fe;
      --primary: #facc15;
      --primary-hover: #eab308;
      --accent-green: #10b981;
      --input-bg: rgba(46, 28, 74, 0.85);
      --input-border: rgba(167, 139, 250, 0.35);
    }

    * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
    
    html, body {
      max-width: 100vw;
      overflow-x: hidden;
    }

    body {
      background-color: var(--bg);
      color: var(--text);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: 0.75rem;
      transition: background-color 0.3s ease;
      position: relative;
      box-sizing: border-box;
    }

    .theme-switcher-wrapper {
      position: absolute;
      top: 0.75rem;
      right: 0.75rem;
      z-index: 50;
    }
    .theme-segmented-control {
      display: inline-flex;
      background: var(--input-bg);
      border: 1px solid var(--input-border);
      border-radius: 9999px;
      padding: 2px;
      gap: 2px;
    }
    .theme-segment-btn {
      background: transparent;
      border: none;
      border-radius: 9999px;
      padding: 4px 7px;
      color: var(--text-muted);
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      transition: all 0.2s ease;
    }
    .theme-segment-btn:hover {
      color: var(--text);
    }
    .theme-segment-btn.active {
      background: var(--card-bg);
      color: var(--text);
      box-shadow: 0 1px 3px rgba(0, 0, 0, 0.15);
    }

    .onboarding-container {
      width: 100%;
      max-width: 580px;
      display: flex;
      flex-direction: column;
      align-items: center;
      text-align: center;
      gap: 0.75rem;
      margin: 0.25rem auto;
      box-sizing: border-box;
    }

    .brand-logo-badge {
      display: flex;
      align-items: center;
      gap: 0.65rem;
    }

    .brand-title {
      font-size: 1.7rem;
      font-weight: 800;
      letter-spacing: -0.02em;
    }

    .hero-subtitle {
      font-size: 0.85rem;
      color: var(--text-muted);
      margin-top: -0.4rem;
      max-width: 480px;
      line-height: 1.4;
    }

    .stepper-bar {
      display: flex;
      align-items: center;
      justify-content: center;
      width: 100%;
      max-width: 420px;
      margin: 0 auto;
    }

    .step-indicator {
      display: flex;
      align-items: center;
      gap: 0.35rem;
      color: var(--text-muted);
      font-size: 0.8rem;
      font-weight: 600;
      transition: all 0.2s ease;
    }

    .step-indicator.active {
      color: var(--primary);
    }

    .step-indicator.completed {
      color: var(--accent-green, #10b981);
    }

    .step-number {
      width: 24px;
      height: 24px;
      border-radius: 50%;
      border: 1.5px solid currentColor;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 0.76rem;
      font-weight: 700;
      background: var(--card-bg);
      transition: all 0.2s ease;
    }

    .step-indicator.active .step-number {
      background: var(--primary);
      color: #ffffff;
      border-color: var(--primary);
    }

    [data-theme="dark"] .step-indicator.active .step-number {
      color: #0f172a;
    }

    .step-indicator.completed .step-number {
      background: var(--accent-green, #10b981);
      color: #ffffff;
      border-color: var(--accent-green, #10b981);
    }

    .step-connector {
      flex: 1;
      max-width: 36px;
      height: 2px;
      background: var(--input-border);
      margin: 0 0.4rem;
      transition: background 0.2s ease;
    }

    .step-connector.active {
      background: var(--primary);
    }

    .wizard-card {
      width: 100%;
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      backdrop-filter: blur(16px);
      border-radius: var(--radius-lg);
      padding: 1.25rem 1.5rem;
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.06);
      display: flex;
      flex-direction: column;
      position: relative;
    }

    [data-theme="dark"] .wizard-card {
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.3);
    }

    .step-pane {
      width: 100%;
      text-align: left;
    }

    .step-heading {
      font-size: 1.2rem;
      font-weight: 800;
      letter-spacing: -0.01em;
      color: var(--text);
      margin-bottom: 0.2rem;
    }

    .step-subheading {
      font-size: 0.82rem;
      color: var(--text-muted);
      line-height: 1.4;
      margin-bottom: 0.85rem;
    }

    .features-grid {
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
      margin-bottom: 0.9rem;
    }

    .feature-card {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      padding: 0.6rem 0.8rem;
      background: var(--input-bg);
      border: 1px solid var(--input-border);
      border-radius: var(--radius-md);
      transition: all 0.2s ease;
    }

    .feature-card:hover {
      border-color: var(--primary);
    }

    .feature-icon {
      width: 32px;
      height: 32px;
      border-radius: 6px;
      background: rgba(107, 33, 168, 0.1);
      color: var(--primary);
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
    }

    [data-theme="dark"] .feature-icon {
      background: rgba(250, 204, 21, 0.15);
      color: var(--primary);
    }

    .feature-title {
      font-size: 0.88rem;
      font-weight: 700;
      color: var(--text);
      margin-bottom: 0.1rem;
    }

    .feature-desc {
      font-size: 0.78rem;
      color: var(--text-muted);
      line-height: 1.35;
    }

    .privacy-callout {
      display: flex;
      align-items: center;
      gap: 0.65rem;
      padding: 0.55rem 0.8rem;
      background: rgba(16, 185, 129, 0.08);
      border: 1px solid rgba(16, 185, 129, 0.25);
      border-radius: var(--radius-md);
      margin-bottom: 0.85rem;
    }

    .privacy-icon {
      color: #10b981;
      flex-shrink: 0;
    }

    .privacy-text {
      font-size: 0.79rem;
      color: var(--text);
      line-height: 1.35;
    }

    .form-group {
      display: flex;
      flex-direction: column;
      gap: 0.3rem;
      margin-bottom: 0.75rem;
    }

    .form-row-dual {
      display: flex;
      gap: 0.75rem;
    }

    @media (max-width: 560px) {
      body {
        padding: 0.5rem;
      }
      .onboarding-container {
        margin: 0.15rem 0;
        gap: 0.6rem;
      }
      .brand-title {
        font-size: 1.45rem;
      }
      .hero-subtitle {
        font-size: 0.8rem;
      }
      .stepper-bar {
        max-width: 100%;
        gap: 0.2rem;
      }
      .step-indicator .step-title {
        display: none;
      }
      .step-connector {
        max-width: 24px;
        margin: 0 0.2rem;
      }
      .form-row-dual {
        flex-direction: column;
        gap: 0;
      }
      .wizard-card {
        padding: 1rem 0.85rem;
        border-radius: var(--radius-md);
      }
      .step-heading {
        font-size: 1.12rem;
      }
      .step-actions {
        flex-direction: column;
        gap: 0.55rem;
      }
      .step-actions.split, .step-actions.right {
        flex-direction: column;
        align-items: stretch;
      }
      .btn-primary-step, .btn-secondary-step {
        width: 100%;
        justify-content: center;
      }
    }

    .flex-1 {
      flex: 1;
    }

    .form-label {
      font-size: 0.82rem;
      font-weight: 700;
      color: var(--text);
      display: flex;
      align-items: center;
      gap: 0.35rem;
    }

    .required-star {
      color: #ef4444;
    }

    .optional-tag {
      font-size: 0.72rem;
      font-weight: normal;
      color: var(--text-muted);
    }

    .form-input, .form-select, .form-textarea {
      width: 100%;
      padding: 0.65rem 0.8rem;
      background: var(--input-bg);
      border: 1px solid var(--input-border);
      border-radius: var(--radius-md);
      color: var(--text);
      font-size: 0.88rem;
      outline: none;
      transition: border-color 0.2s ease, box-shadow 0.2s ease;
      font-family: inherit;
    }

    .form-input:focus, .form-select:focus, .form-textarea:focus {
      border-color: var(--primary);
      box-shadow: 0 0 0 3px rgba(107, 33, 168, 0.12);
    }

    .input-password-wrapper {
      position: relative;
      display: flex;
      align-items: center;
      width: 100%;
    }

    .input-password-wrapper .form-input {
      padding-right: 2.5rem;
    }

    .btn-toggle-pw {
      position: absolute;
      right: 0.6rem;
      background: none;
      border: none;
      color: var(--text-muted);
      cursor: pointer;
      padding: 0.25rem;
      display: flex;
      align-items: center;
      justify-content: center;
      border-radius: 4px;
    }

    .btn-toggle-pw:hover {
      color: var(--text);
    }

    .field-hint {
      font-size: 0.75rem;
      color: var(--text-muted);
      line-height: 1.35;
      margin-top: 0.25rem;
    }

    .field-hint a {
      color: var(--primary);
      text-decoration: underline;
      font-weight: 600;
    }

    .location-picker-wrapper {
      position: relative;
      display: flex;
      width: 100%;
    }

    .btn-location-picker {
      width: 100%;
      background: var(--input-bg);
      border: 1px solid var(--input-border);
      border-radius: var(--radius-md);
      padding: 0.65rem 0.8rem;
      font-size: 0.88rem;
      font-weight: 600;
      color: var(--text);
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      justify-content: space-between;
      gap: 0.5rem;
      transition: all 0.2s ease;
    }

    .btn-location-picker:hover {
      border-color: var(--primary);
    }

    .loc-flag-img {
      width: 18px;
      height: 13px;
      border-radius: 2px;
      object-fit: cover;
      display: inline-block;
      flex-shrink: 0;
    }

    .loc-flag-icon {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 18px;
      height: 16px;
      color: var(--primary);
      flex-shrink: 0;
    }

    .location-dropdown-menu {
      position: absolute;
      top: calc(100% + 4px);
      left: 0;
      right: 0;
      z-index: 100;
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: var(--radius-md);
      padding: 0.4rem;
      box-shadow: 0 8px 20px rgba(0,0,0,0.2);
      max-height: 220px;
      overflow-y: auto;
      display: none;
      flex-direction: column;
      gap: 2px;
    }

    .location-dropdown-menu.active {
      display: flex;
    }

    .location-item-btn {
      display: flex;
      align-items: center;
      gap: 0.6rem;
      padding: 0.45rem 0.65rem;
      border: none;
      background: transparent;
      color: var(--text);
      font-size: 0.84rem;
      cursor: pointer;
      border-radius: 6px;
      text-align: left;
      transition: background 0.15s ease;
      width: 100%;
    }

    .location-item-btn:hover {
      background: rgba(107, 33, 168, 0.08);
      color: var(--primary);
    }

    [data-theme="dark"] .location-item-btn:hover {
      background: rgba(250, 204, 21, 0.12);
    }

    .location-item-btn.selected {
      background: rgba(107, 33, 168, 0.12);
      font-weight: 700;
      color: var(--primary);
    }

    .step-actions {
      display: flex;
      align-items: center;
      margin-top: 0.95rem;
    }

    .step-actions.right {
      justify-content: flex-end;
    }

    .step-actions.split {
      justify-content: space-between;
    }

    .btn-primary-step {
      display: inline-flex;
      align-items: center;
      gap: 0.45rem;
      background: var(--primary);
      color: #ffffff;
      border: none;
      border-radius: var(--radius-md);
      padding: 0.65rem 1.25rem;
      font-size: 0.88rem;
      font-weight: 700;
      cursor: pointer;
      transition: all 0.2s ease;
      box-shadow: 0 2px 6px rgba(107, 33, 168, 0.2);
    }

    [data-theme="dark"] .btn-primary-step {
      color: #0f172a;
    }

    .btn-primary-step:hover:not(:disabled) {
      background: var(--primary-hover);
      transform: translateY(-1px);
    }

    .btn-primary-step:disabled {
      opacity: 0.6;
      cursor: not-allowed;
      transform: none;
    }

    .btn-secondary-step {
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
      background: transparent;
      color: var(--text);
      border: 1px solid var(--input-border);
      border-radius: var(--radius-md);
      padding: 0.6rem 1rem;
      font-size: 0.82rem;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s ease;
    }

    .btn-secondary-step:hover {
      border-color: var(--text-muted);
      background: rgba(0, 0, 0, 0.04);
    }

    .btn-link-skip {
      background: none;
      border: none;
      color: var(--text-muted);
      font-size: 0.8rem;
      text-decoration: underline;
      cursor: pointer;
      padding: 0.4rem;
    }

    .btn-link-skip:hover {
      color: var(--text);
    }

    @keyframes spin {
      100% { transform: rotate(360deg); }
    }

    .spin-icon {
      animation: spin 1s linear infinite;
      transform-origin: center;
    }
  </style>
</head>
<body>
  <div class="theme-switcher-wrapper">
    <div class="theme-segmented-control" id="theme-segmented-control">
      <button type="button" class="theme-segment-btn" data-theme-btn="system" onclick="setThemeMode('system')" title="System Theme">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2" ry="2"></rect><line x1="8" y1="21" x2="16" y2="21"></line><line x1="12" y1="17" x2="12" y2="21"></line></svg>
      </button>
      <button type="button" class="theme-segment-btn" data-theme-btn="light" onclick="setThemeMode('light')" title="Light Mode">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"></circle><line x1="12" y1="1" x2="12" y2="3"></line><line x1="12" y1="21" x2="12" y2="23"></line><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"></line><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"></line><line x1="1" y1="12" x2="3" y2="12"></line><line x1="21" y1="12" x2="23" y2="12"></line><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"></line><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"></line></svg>
      </button>
      <button type="button" class="theme-segment-btn" data-theme-btn="dark" onclick="setThemeMode('dark')" title="Dark Mode">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path></svg>
      </button>
    </div>
  </div>

  <div class="onboarding-container">
    <div class="brand-logo-badge">
      <img src="/static/images/duckduckwork_logo.png" alt="DuckDuckWork" style="width:42px; height:42px; border-radius:50%; object-fit:cover;" width="42" height="42" loading="eager" fetchpriority="high">
      <div class="brand-title">DuckDuckWork</div>
    </div>
    
    <div class="hero-subtitle">
      Private, self-hosted job search copilot and application manager.
    </div>

    <!-- Stepper Indicator -->
    <div class="stepper-bar">
      <div class="step-indicator active" id="step-ind-1">
        <div class="step-number" id="step-num-1">1</div>
        <span class="step-title">Welcome</span>
      </div>
      <div class="step-connector" id="connector-1"></div>
      <div class="step-indicator" id="step-ind-2">
        <div class="step-number" id="step-num-2">2</div>
        <span class="step-title">AI Engine</span>
      </div>
      <div class="step-connector" id="connector-2"></div>
      <div class="step-indicator" id="step-ind-3">
        <div class="step-number" id="step-num-3">3</div>
        <span class="step-title">First Search</span>
      </div>
    </div>

    <div class="wizard-card">
      <!-- STEP 1: WHAT IS DUCKDUCKWORK -->
      <div class="step-pane" id="step-pane-1">
        <h2 class="step-heading">Welcome to DuckDuckWork</h2>
        <p class="step-subheading">Automate your job search with multi-board crawling and local AI fit scoring.</p>

        <div class="features-grid">
          <div class="feature-card">
            <div class="feature-icon">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
            </div>
            <div class="feature-content">
              <div class="feature-title">Multi-Board Discovery</div>
              <div class="feature-desc">Scans LinkedIn, Indeed, Glassdoor, RemoteOK, and Get on Board simultaneously.</div>
            </div>
          </div>

          <div class="feature-card">
            <div class="feature-icon">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon></svg>
            </div>
            <div class="feature-content">
              <div class="feature-title">AI Fit Scoring &amp; Assets</div>
              <div class="feature-desc">Evaluates match compatibility and crafts tailored resumes and cover letters.</div>
            </div>
          </div>

          <div class="feature-card">
            <div class="feature-icon">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></svg>
            </div>
            <div class="feature-content">
              <div class="feature-title">100% Local &amp; Private</div>
              <div class="feature-desc">Runs entirely on your machine. Your search profiles and API keys stay local.</div>
            </div>
          </div>
        </div>

        <div class="step-actions right">
          <button type="button" class="btn-primary-step" onclick="goToStep(2)">
            <span>Get Started</span>
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"></polyline></svg>
          </button>
        </div>
      </div>

      <!-- STEP 2: CONNECT AI ENGINE -->
      <div class="step-pane" id="step-pane-2" style="display:none;">
        <h2 class="step-heading">Connect Your AI Engine</h2>
        <p class="step-subheading">Select your preferred model provider to score job compatibility and tailor materials.</p>

        <div class="privacy-callout">
          <div class="privacy-icon">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect><path d="M7 11V7a5 5 0 0 1 10 0v4"></path></svg>
          </div>
          <div class="privacy-text">
            <strong>Private &amp; Local:</strong> Your API key is stored exclusively on your device and never uploaded to remote servers.
          </div>
        </div>

        <div class="form-group">
          <label class="form-label" for="ai-provider-select">AI Provider</label>
          <select id="ai-provider-select" class="form-select" onchange="handleProviderChange(this.value)">
            <option value="gemini" selected>Google Gemini (Recommended - Free Tier Available)</option>
            <option value="openai">OpenAI (GPT-4o / GPT-4o-mini)</option>
            <option value="openrouter">OpenRouter (Multi-model)</option>
            <option value="groq">Groq (Ultra-fast Llama)</option>
          </select>
        </div>

        <div class="form-group">
          <label class="form-label" for="ai-api-key-input">API Key</label>
          <div class="input-password-wrapper">
            <input type="password" id="ai-api-key-input" class="form-input" placeholder="AIzaSy..." autocomplete="off">
            <button type="button" class="btn-toggle-pw" onclick="togglePasswordVisibility('ai-api-key-input', this)" title="Show or hide API Key">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>
            </button>
          </div>
          <div id="ai-key-test-status" style="display:none; font-size:0.78rem; margin-top:0.35rem; padding:0.4rem 0.65rem; border-radius:6px; line-height:1.35;"></div>
          <div class="field-hint" id="provider-helper-hint">
            Get a free API key at <a href="https://aistudio.google.com/app/apikey" target="_blank" rel="noreferrer noopener">Google AI Studio</a> (15 requests/min, no card needed).
          </div>
        </div>

        <div class="step-actions split">
          <button type="button" class="btn-secondary-step" onclick="goToStep(1)">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"></polyline></svg>
            <span>Back</span>
          </button>
          <div style="display:flex; align-items:center; gap:0.6rem;">
            <button type="button" class="btn-link-skip" onclick="skipApiKeyAndProceed()">Skip for now</button>
            <button type="button" id="btn-next-step2" class="btn-primary-step" onclick="validateAndGoToStep3()">
              <span>Next: First Search</span>
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"></polyline></svg>
            </button>
          </div>
        </div>
      </div>

      <!-- STEP 3: CREATE FIRST SEARCH -->
      <div class="step-pane" id="step-pane-3" style="display:none;">
        <h2 class="step-heading">Create Your First Search</h2>
        <p class="step-subheading">Define your target role and attach your resume to begin crawling and scoring.</p>

        <div class="form-group">
          <label class="form-label" for="search-title-input">Job Title or Target Keywords <span class="required-star">*</span></label>
          <input type="text" id="search-title-input" class="form-input" placeholder="e.g. Senior Software Engineer, Product Manager, Full Stack Developer..." required autofocus>
        </div>

        <div class="form-row-dual">
          <div class="form-group flex-1">
            <label class="form-label">Search Location</label>
            <div class="location-picker-wrapper" id="location-picker-wrapper">
              <button type="button" id="onboarding-location-btn" class="btn-location-picker" onclick="toggleLocationDropdown(event)" title="Search Location: Worldwide">
                <span style="display:flex; align-items:center; gap:0.45rem; overflow:hidden;">
                  <span id="onboarding-location-flag" class="loc-flag-icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="2" y1="12" x2="22" y2="12"></line><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10z"></path></svg></span>
                  <span id="location-picker-label">Worldwide</span>
                </span>
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="opacity:0.7;"><polyline points="6 9 12 15 18 9"></polyline></svg>
              </button>
              <div id="onboarding-location-menu" class="location-dropdown-menu">
                <button type="button" class="location-item-btn selected" onclick="selectOnboardingLocation('Worldwide', 'globe', this)">
                  <span class="loc-flag-icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="2" y1="12" x2="22" y2="12"></line><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10z"></path></svg></span>
                  <span>Worldwide</span>
                </button>
                <button type="button" class="location-item-btn" onclick="selectOnboardingLocation('LATAM', 'latam', this)">
                  <span class="loc-flag-icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><polygon points="16.24 7.76 14.12 14.12 7.76 16.24 9.88 9.88 16.24 7.76"></polygon></svg></span>
                  <span>LATAM</span>
                </button>
                <button type="button" class="location-item-btn" onclick="selectOnboardingLocation('United States', 'us', this)"><img src="https://flagcdn.com/20x15/us.png" width="20" height="15" alt="United States" class="loc-flag-img"><span>United States</span></button>
                <button type="button" class="location-item-btn" onclick="selectOnboardingLocation('Canada', 'ca', this)"><img src="https://flagcdn.com/20x15/ca.png" width="20" height="15" alt="Canada" class="loc-flag-img"><span>Canada</span></button>
                <button type="button" class="location-item-btn" onclick="selectOnboardingLocation('Guatemala', 'gt', this)"><img src="https://flagcdn.com/20x15/gt.png" width="20" height="15" alt="Guatemala" class="loc-flag-img"><span>Guatemala</span></button>
                <button type="button" class="location-item-btn" onclick="selectOnboardingLocation('Nicaragua', 'ni', this)"><img src="https://flagcdn.com/20x15/ni.png" width="20" height="15" alt="Nicaragua" class="loc-flag-img"><span>Nicaragua</span></button>
                <button type="button" class="location-item-btn" onclick="selectOnboardingLocation('Costa Rica', 'cr', this)"><img src="https://flagcdn.com/20x15/cr.png" width="20" height="15" alt="Costa Rica" class="loc-flag-img"><span>Costa Rica</span></button>
                <button type="button" class="location-item-btn" onclick="selectOnboardingLocation('Panama', 'pa', this)"><img src="https://flagcdn.com/20x15/pa.png" width="20" height="15" alt="Panama" class="loc-flag-img"><span>Panama</span></button>
                <button type="button" class="location-item-btn" onclick="selectOnboardingLocation('El Salvador', 'sv', this)"><img src="https://flagcdn.com/20x15/sv.png" width="20" height="15" alt="El Salvador" class="loc-flag-img"><span>El Salvador</span></button>
                <button type="button" class="location-item-btn" onclick="selectOnboardingLocation('Honduras', 'hn', this)"><img src="https://flagcdn.com/20x15/hn.png" width="20" height="15" alt="Honduras" class="loc-flag-img"><span>Honduras</span></button>
                <button type="button" class="location-item-btn" onclick="selectOnboardingLocation('Mexico', 'mx', this)"><img src="https://flagcdn.com/20x15/mx.png" width="20" height="15" alt="Mexico" class="loc-flag-img"><span>Mexico</span></button>
                <button type="button" class="location-item-btn" onclick="selectOnboardingLocation('Colombia', 'co', this)"><img src="https://flagcdn.com/20x15/co.png" width="20" height="15" alt="Colombia" class="loc-flag-img"><span>Colombia</span></button>
                <button type="button" class="location-item-btn" onclick="selectOnboardingLocation('Argentina', 'ar', this)"><img src="https://flagcdn.com/20x15/ar.png" width="20" height="15" alt="Argentina" class="loc-flag-img"><span>Argentina</span></button>
                <button type="button" class="location-item-btn" onclick="selectOnboardingLocation('Chile', 'cl', this)"><img src="https://flagcdn.com/20x15/cl.png" width="20" height="15" alt="Chile" class="loc-flag-img"><span>Chile</span></button>
                <button type="button" class="location-item-btn" onclick="selectOnboardingLocation('Peru', 'pe', this)"><img src="https://flagcdn.com/20x15/pe.png" width="20" height="15" alt="Peru" class="loc-flag-img"><span>Peru</span></button>
                <button type="button" class="location-item-btn" onclick="selectOnboardingLocation('Ecuador', 'ec', this)"><img src="https://flagcdn.com/20x15/ec.png" width="20" height="15" alt="Ecuador" class="loc-flag-img"><span>Ecuador</span></button>
                <button type="button" class="location-item-btn" onclick="selectOnboardingLocation('Uruguay', 'uy', this)"><img src="https://flagcdn.com/20x15/uy.png" width="20" height="15" alt="Uruguay" class="loc-flag-img"><span>Uruguay</span></button>
                <button type="button" class="location-item-btn" onclick="selectOnboardingLocation('Paraguay', 'py', this)"><img src="https://flagcdn.com/20x15/py.png" width="20" height="15" alt="Paraguay" class="loc-flag-img"><span>Paraguay</span></button>
                <button type="button" class="location-item-btn" onclick="selectOnboardingLocation('Bolivia', 'bo', this)"><img src="https://flagcdn.com/20x15/bo.png" width="20" height="15" alt="Bolivia" class="loc-flag-img"><span>Bolivia</span></button>
                <button type="button" class="location-item-btn" onclick="selectOnboardingLocation('Venezuela', 've', this)"><img src="https://flagcdn.com/20x15/ve.png" width="20" height="15" alt="Venezuela" class="loc-flag-img"><span>Venezuela</span></button>
                <button type="button" class="location-item-btn" onclick="selectOnboardingLocation('Brazil', 'br', this)"><img src="https://flagcdn.com/20x15/br.png" width="20" height="15" alt="Brazil" class="loc-flag-img"><span>Brazil</span></button>
                <button type="button" class="location-item-btn" onclick="selectOnboardingLocation('Dominican Republic', 'do', this)"><img src="https://flagcdn.com/20x15/do.png" width="20" height="15" alt="Dominican Republic" class="loc-flag-img"><span>Dominican Republic</span></button>
                <button type="button" class="location-item-btn" onclick="selectOnboardingLocation('Spain', 'es', this)"><img src="https://flagcdn.com/20x15/es.png" width="20" height="15" alt="Spain" class="loc-flag-img"><span>Spain</span></button>
                <button type="button" class="location-item-btn" onclick="selectOnboardingLocation('United Kingdom', 'gb', this)"><img src="https://flagcdn.com/20x15/gb.png" width="20" height="15" alt="United Kingdom" class="loc-flag-img"><span>United Kingdom</span></button>
                <button type="button" class="location-item-btn" onclick="selectOnboardingLocation('Germany', 'de', this)"><img src="https://flagcdn.com/20x15/de.png" width="20" height="15" alt="Germany" class="loc-flag-img"><span>Germany</span></button>
              </div>
            </div>
          </div>

          <div class="form-group flex-1">
            <label class="form-label" for="work-mode-select">Work Mode</label>
            <select id="work-mode-select" class="form-select">
              <option value="any" selected>Any (Remote, Hybrid &amp; On-site)</option>
              <option value="remote">Remote Only</option>
              <option value="hybrid">Hybrid</option>
              <option value="onsite">On-site</option>
            </select>
          </div>
        </div>

        <div class="form-group">
          <label class="form-label" style="display:flex; justify-content:space-between; align-items:center;">
            <span>Upload Your CV / Resume (PDF) <span class="optional-tag">(Optional)</span></span>
            <span id="cv-upload-status" style="font-size:0.75rem; color:#10b981; font-weight:600; display:none;">PDF Attached</span>
          </label>
          <div class="cv-upload-box" id="cv-drop-zone" onclick="document.getElementById('onboard-cv-file').click()" style="border:1.5px dashed var(--input-border); border-radius:8px; padding:0.85rem 0.75rem; text-align:center; cursor:pointer; background:rgba(255,255,255,0.02); transition:all 0.2s;">
            <input type="file" id="onboard-cv-file" accept=".pdf" style="display:none;" onchange="handleOnboardingCvSelected(this)">
            <div id="cv-upload-idle" style="display:flex; flex-direction:column; align-items:center; gap:0.25rem;">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color:var(--primary);"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="17 8 12 3 7 8"></polyline><line x1="12" y1="3" x2="12" y2="15"></line></svg>
              <div style="font-size:0.84rem; font-weight:600; color:var(--text);">Upload your CV in PDF format</div>
              <div style="font-size:0.74rem; color:var(--text-muted); line-height:1.35;">Optional. Automatically creates your profile and boosts AI match accuracy.</div>
            </div>
            <div id="cv-upload-selected" style="display:none; align-items:center; justify-content:space-between; gap:0.6rem;">
              <div style="display:flex; align-items:center; gap:0.5rem; text-align:left; min-width:0;">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><polyline points="10 9 9 9 8 9"></polyline></svg>
                <div style="min-width:0;">
                  <div id="cv-selected-filename" style="font-size:0.82rem; font-weight:700; color:var(--text); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">resume.pdf</div>
                  <div id="cv-selected-size" style="font-size:0.7rem; color:var(--text-muted);">124 KB</div>
                </div>
              </div>
              <button type="button" class="btn-secondary-step" onclick="clearOnboardingCv(event)" style="font-size:0.74rem; padding:0.2rem 0.5rem; flex:none;">Remove</button>
            </div>
          </div>
        </div>

        <div class="step-actions split">
          <button type="button" class="btn-secondary-step" onclick="goToStep(2)">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"></polyline></svg>
            <span>Back</span>
          </button>
          <button type="button" id="btn-launch-search" class="btn-primary-step" onclick="submitOnboarding()">
            <span>Launch DuckDuckWork</span>
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"></line><polygon points="22 2 15 22 11 13 2 9 22 2"></polygon></svg>
          </button>
        </div>
      </div>
    </div>
  </div>

  <script>
    function setThemeMode(mode) {
      var effective = mode;
      if (mode === 'system') {
        effective = (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light';
      }
      document.documentElement.setAttribute('data-theme', effective);
      document.documentElement.setAttribute('data-theme-mode', mode);
      localStorage.setItem('applypilot_theme_mode', mode);

      document.querySelectorAll('.theme-segment-btn').forEach(function(btn) {
        if (btn.getAttribute('data-theme-btn') === mode) {
          btn.classList.add('active');
        } else {
          btn.classList.remove('active');
        }
      });
    }

    (function initTheme() {
      var mode = localStorage.getItem('applypilot_theme_mode') || 'system';
      setThemeMode(mode);
    })();

    let currentStep = 1;
    let selectedLocation = 'Worldwide';
    let isTestingKey = false;
    let apiKeyVerified = false;
    let lastVerifiedKey = '';

    function goToStep(step) {
      if (step < 1 || step > 3) return;
      
      document.getElementById('step-pane-1').style.display = (step === 1 ? 'block' : 'none');
      document.getElementById('step-pane-2').style.display = (step === 2 ? 'block' : 'none');
      document.getElementById('step-pane-3').style.display = (step === 3 ? 'block' : 'none');

      for (let i = 1; i <= 3; i++) {
        const ind = document.getElementById('step-ind-' + i);
        const num = document.getElementById('step-num-' + i);
        if (ind && num) {
          ind.classList.remove('active', 'completed');
          if (i === step) {
            ind.classList.add('active');
            num.innerText = i;
          } else if (i < step) {
            ind.classList.add('completed');
            num.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>';
          } else {
            num.innerText = i;
          }
        }
      }

      const conn1 = document.getElementById('connector-1');
      const conn2 = document.getElementById('connector-2');
      if (conn1) conn1.classList.toggle('active', step >= 2);
      if (conn2) conn2.classList.toggle('active', step >= 3);

      currentStep = step;
      if (step === 3) {
        const titleInput = document.getElementById('search-title-input');
        if (titleInput) titleInput.focus();
      }
    }

    (function initStepFromUrl() {
      var params = new URLSearchParams(window.location.search);
      var s = parseInt(params.get('step') || '1', 10);
      if (s >= 1 && s <= 3) {
        goToStep(s);
      }
    })();

    function togglePasswordVisibility(inputId, btnEl) {
      const input = document.getElementById(inputId);
      if (!input) return;
      if (input.type === 'password') {
        input.type = 'text';
        btnEl.innerHTML = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path><line x1="1" y1="1" x2="23" y2="23"></line></svg>';
      } else {
        input.type = 'password';
        btnEl.innerHTML = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>';
      }
    }

    function handleProviderChange(provider) {
      const hintEl = document.getElementById('provider-helper-hint');
      const inputEl = document.getElementById('ai-api-key-input');
      const statusEl = document.getElementById('ai-key-test-status');
      apiKeyVerified = false;
      lastVerifiedKey = '';
      if (statusEl) statusEl.style.display = 'none';

      if (!hintEl || !inputEl) return;
      if (provider === 'gemini') {
        hintEl.innerHTML = 'Get a free API key at <a href="https://aistudio.google.com/app/apikey" target="_blank" rel="noreferrer noopener">Google AI Studio</a> (15 requests/min, no card needed).';
        inputEl.placeholder = 'AIzaSy...';
      } else if (provider === 'openai') {
        hintEl.innerHTML = 'Get an API key from your <a href="https://platform.openai.com/api-keys" target="_blank" rel="noreferrer noopener">OpenAI Platform Dashboard</a>.';
        inputEl.placeholder = 'sk-...';
      } else if (provider === 'openrouter') {
        hintEl.innerHTML = 'Get an API key from your <a href="https://openrouter.ai/keys" target="_blank" rel="noreferrer noopener">OpenRouter Account</a>.';
        inputEl.placeholder = 'sk-or-...';
      } else if (provider === 'groq') {
        hintEl.innerHTML = 'Get an API key from your <a href="https://console.groq.com/keys" target="_blank" rel="noreferrer noopener">Groq Console</a>.';
        inputEl.placeholder = 'gsk_...';
      }
    }

    async function testApiKey(keyOverride) {
      const keyInput = document.getElementById('ai-api-key-input');
      const key = (typeof keyOverride === 'string' ? keyOverride : (keyInput ? keyInput.value : '')).trim();
      const provider = document.getElementById('ai-provider-select').value;
      const statusEl = document.getElementById('ai-key-test-status');
      const nextBtn = document.getElementById('btn-next-step2');

      if (!key) {
        if (statusEl) statusEl.style.display = 'none';
        apiKeyVerified = false;
        lastVerifiedKey = '';
        return false;
      }

      if (apiKeyVerified && lastVerifiedKey === key) {
        return true;
      }

      isTestingKey = true;
      if (statusEl) {
        statusEl.style.display = 'block';
        statusEl.style.background = 'rgba(107, 33, 168, 0.1)';
        statusEl.style.color = 'var(--text)';
        statusEl.style.border = '1px solid var(--card-border)';
        statusEl.innerHTML = '<span style="display:inline-flex; align-items:center; gap:0.4rem;"><svg class="spin-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 12a9 9 0 1 1-6.219-8.56"></path></svg> Testing API key connection...</span>';
      }

      if (nextBtn) {
        nextBtn.disabled = true;
        nextBtn.innerHTML = '<span>Verifying Key...</span>';
      }

      try {
        const res = await fetch('/api/llm/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ provider: provider, api_key: key })
        });
        const data = await res.json();
        if (data.success) {
          apiKeyVerified = true;
          lastVerifiedKey = key;
          if (statusEl) {
            statusEl.style.background = 'rgba(16, 185, 129, 0.12)';
            statusEl.style.color = '#10b981';
            statusEl.style.border = '1px solid rgba(16, 185, 129, 0.3)';
            statusEl.innerHTML = '<span style="display:inline-flex; align-items:center; gap:0.4rem;"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg> API Key verified successfully' + (data.latency_ms ? ' (' + data.latency_ms + 'ms)' : '') + '</span>';
          }
          if (nextBtn) {
            nextBtn.disabled = false;
            nextBtn.innerHTML = '<span>Next: First Search</span><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"></polyline></svg>';
          }
          return true;
        } else {
          apiKeyVerified = false;
          lastVerifiedKey = '';
          if (statusEl) {
            statusEl.style.background = 'rgba(239, 68, 68, 0.12)';
            statusEl.style.color = '#ef4444';
            statusEl.style.border = '1px solid rgba(239, 68, 68, 0.3)';
            const msg = data.message || 'API key verification failed. Please check your key.';
            statusEl.innerHTML = '<span style="display:inline-flex; align-items:center; gap:0.4rem;"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg> ' + msg + '</span>';
          }
          if (nextBtn) {
            nextBtn.disabled = false;
            nextBtn.innerHTML = '<span>Next: First Search</span><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"></polyline></svg>';
          }
          return false;
        }
      } catch (err) {
        apiKeyVerified = false;
        if (statusEl) {
          statusEl.style.background = 'rgba(239, 68, 68, 0.12)';
          statusEl.style.color = '#ef4444';
          statusEl.style.border = '1px solid rgba(239, 68, 68, 0.3)';
          statusEl.innerHTML = '<span>Network error testing API key.</span>';
        }
        if (nextBtn) {
          nextBtn.disabled = false;
          nextBtn.innerHTML = '<span>Next: First Search</span><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"></polyline></svg>';
        }
        return false;
      } finally {
        isTestingKey = false;
      }
    }

    // Attach paste listener to test immediately when key is pasted
    const keyInputEl = document.getElementById('ai-api-key-input');
    if (keyInputEl) {
      keyInputEl.addEventListener('paste', function(e) {
        setTimeout(function() {
          const val = keyInputEl.value.trim();
          if (val) {
            testApiKey(val);
          }
        }, 60);
      });

      keyInputEl.addEventListener('input', function(e) {
        const val = e.target.value.trim();
        if (val !== lastVerifiedKey) {
          apiKeyVerified = false;
          const statusEl = document.getElementById('ai-key-test-status');
          if (statusEl && !isTestingKey) {
            statusEl.style.display = 'none';
          }
        }
      });
    }

    async function validateAndGoToStep3() {
      const keyInput = document.getElementById('ai-api-key-input');
      const key = keyInput ? keyInput.value.trim() : '';
      if (!key) {
        if (!confirm('You have not entered an AI API Key. Without a key, DuckDuckWork will scrape jobs but cannot compute AI match scores or generate resumes. Would you like to proceed anyway?')) {
          if (keyInput) keyInput.focus();
          return;
        }
        goToStep(3);
        return;
      }

      if (apiKeyVerified && lastVerifiedKey === key) {
        goToStep(3);
        return;
      }

      // Automatically test before continuing
      const ok = await testApiKey(key);
      if (ok) {
        goToStep(3);
      }
    }

    function skipApiKeyAndProceed() {
      goToStep(3);
    }

    function toggleLocationDropdown(e) {
      if (e) e.stopPropagation();
      const menu = document.getElementById('onboarding-location-menu');
      if (menu) menu.classList.toggle('active');
    }

    function selectOnboardingLocation(name, typeOrCode, btnEl) {
      selectedLocation = name;
      const flagEl = document.getElementById('onboarding-location-flag');
      const labelEl = document.getElementById('location-picker-label');
      const btnElParent = document.getElementById('onboarding-location-btn');

      if (labelEl) labelEl.innerText = name;

      if (flagEl) {
        if (typeOrCode === 'globe') {
          flagEl.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="2" y1="12" x2="22" y2="12"></line><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10z"></path></svg>';
        } else if (typeOrCode === 'latam') {
          flagEl.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><polygon points="16.24 7.76 14.12 14.12 7.76 16.24 9.88 9.88 16.24 7.76"></polygon></svg>';
        } else {
          flagEl.innerHTML = '<img src="https://flagcdn.com/20x15/' + typeOrCode.toLowerCase() + '.png" width="20" height="15" alt="' + name + '" class="loc-flag-img">';
        }
      }

      if (btnElParent) {
        btnElParent.setAttribute('title', 'Search Location: ' + name);
      }

      document.querySelectorAll('.location-item-btn').forEach(b => b.classList.remove('selected'));
      if (btnEl) btnEl.classList.add('selected');

      const menu = document.getElementById('onboarding-location-menu');
      if (menu) menu.classList.remove('active');
    }

    document.addEventListener('click', function(e) {
      const wrapper = document.getElementById('location-picker-wrapper');
      if (wrapper && !wrapper.contains(e.target)) {
        const menu = document.getElementById('onboarding-location-menu');
        if (menu) menu.classList.remove('active');
      }
    });

    function handleOnboardingCvSelected(input) {
      if (input.files && input.files[0]) {
        const file = input.files[0];
        document.getElementById('cv-upload-idle').style.display = 'none';
        const sel = document.getElementById('cv-upload-selected');
        sel.style.display = 'flex';
        document.getElementById('cv-upload-status').style.display = 'inline';
        document.getElementById('cv-selected-filename').textContent = file.name;
        document.getElementById('cv-selected-size').textContent = (file.size < 1048576) ? (Math.round(file.size / 1024) + ' KB') : ((file.size / (1024 * 1024)).toFixed(1) + ' MB');
      }
    }

    function clearOnboardingCv(event) {
      if (event) event.stopPropagation();
      const input = document.getElementById('onboard-cv-file');
      if (input) input.value = '';
      document.getElementById('cv-upload-idle').style.display = 'flex';
      document.getElementById('cv-upload-selected').style.display = 'none';
      document.getElementById('cv-upload-status').style.display = 'none';
    }

    async function submitOnboarding() {
      const titleInput = document.getElementById('search-title-input');
      const query = titleInput.value.trim();
      if (!query) {
        alert('Please enter a job title or target keywords for your first search.');
        titleInput.focus();
        return;
      }

      const provider = document.getElementById('ai-provider-select').value;
      const apiKey = document.getElementById('ai-api-key-input').value.trim();
      const location = selectedLocation || 'Worldwide';
      const workMode = document.getElementById('work-mode-select').value;

      const launchBtn = document.getElementById('btn-launch-search');
      launchBtn.disabled = true;
      launchBtn.innerHTML = '<span>Starting DuckDuckWork...</span>';

      let resumeB64 = null;
      let resumeFilename = null;
      const cvInput = document.getElementById('onboard-cv-file');
      if (cvInput && cvInput.files && cvInput.files[0]) {
        const file = cvInput.files[0];
        resumeFilename = file.name;
        try {
          resumeB64 = await new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result.split(',')[1]);
            reader.onerror = reject;
            reader.readAsDataURL(file);
          });
        } catch (readErr) {
          console.warn('Could not read CV file:', readErr);
        }
      }

      try {
        const res = await fetch('/api/onboarding/setup', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            provider: provider,
            api_key: apiKey,
            query: query,
            location: location,
            work_mode: workMode,
            candidate_summary: "",
            resume_b64: resumeB64,
            resume_filename: resumeFilename
          })
        });
        const data = await res.json();
        if (data.success) {
          window.location.href = data.redirect || '/';
        } else {
          alert('Error setting up search: ' + (data.error || data.message || 'Unknown error'));
          launchBtn.disabled = false;
          launchBtn.innerHTML = '<span>Launch DuckDuckWork</span><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"></line><polygon points="22 2 15 22 11 13 2 9 22 2"></polygon></svg>';
        }
      } catch (err) {
        console.error('Onboarding submission error:', err);
        alert('Network error submitting setup.');
        launchBtn.disabled = false;
        launchBtn.innerHTML = '<span>Launch DuckDuckWork</span><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"></line><polygon points="22 2 15 22 11 13 2 9 22 2"></polygon></svg>';
      }
    }
  </script>
</body>
</html>"""



def generate_admin_html() -> str:
    """Generate the full Admin Control Panel HTML for /admin."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="noindex, nofollow, noarchive">
<title>DuckDuckWork - Admin Cockpit &amp; Settings</title>
<link rel="icon" type="image/x-icon" href="/favicon.ico">
<link rel="icon" type="image/png" sizes="96x96" href="/favicon-96x96.png">
<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32x32.png">
<link rel="icon" type="image/png" sizes="16x16" href="/favicon-16x16.png">
<link rel="icon" type="image/png" href="/favicon.png">
<link rel="shortcut icon" type="image/png" href="/favicon.png">
<link rel="shortcut icon" href="/favicon.ico">
<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">
<link rel="mask-icon" href="/static/images/safari-pinned-tab.svg" color="#6b21a8">
<meta name="theme-color" content="#ffffff" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#0f0c1b" media="(prefers-color-scheme: dark)">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Plus+Jakarta+Sans:wght@500;600;700;800&display=swap" rel="stylesheet">
<script>
(function() {
  var mode = localStorage.getItem('applypilot_theme_mode') || 'system';
  var effective = mode;
  if (mode === 'system') {
    effective = (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light';
  }
  document.documentElement.setAttribute('data-theme', effective);
  document.documentElement.setAttribute('data-theme-mode', mode);
})();
</script>
<style>
  :root {
    --bg-main: #0f0c1b;
    --bg-header: #170e26;
    --bg-card: #1d122f;
    --bg-card-hover: #2d1a4d;
    --border-color: #2e1c4a;
    --text-main: #f8fafc;
    --text-muted: #a78bfa;
    --accent-purple: #9333ea;
    --accent-purple-dark: #6b21a8;
    --accent-yellow: #facc15;
    --accent-green: #10b981;
    --accent-red: #ef4444;
    --radius-lg: 16px;
    --radius-md: 10px;
    --radius-sm: 6px;
    --shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5), 0 8px 10px -6px rgba(0, 0, 0, 0.5);
  }

  html[data-theme="light"] {
    --bg-main: #f6f3fb;
    --bg-header: #ffffff;
    --bg-card: #ffffff;
    --bg-card-hover: #f5f3ff;
    --border-color: #e9d5ff;
    --text-main: #0f172a;
    --text-muted: #64748b;
    --accent-purple: #6b21a8;
    --accent-purple-dark: #581c87;
    --accent-yellow: #b45309;
    --accent-green: #059669;
    --accent-red: #dc2626;
    --shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
  }

  html[data-theme="light"] .auth-input {
    background: #ffffff;
    border-color: #e9d5ff;
    color: #0f172a;
  }

  html[data-theme="light"] .terminal-box {
    background: #0f172a;
    border-color: #334155;
    color: #38bdf8;
  }

  html[data-theme="light"] .theme-segmented-control {
    background: #ffffff;
    border-color: #e9d5ff;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, sans-serif;
    background-color: var(--bg-main);
    color: var(--text-main);
    min-height: 100vh;
    display: flex;
    flex-direction: column;
  }

  /* Admin Header */
  .admin-header {
    background: var(--bg-header);
    border-bottom: 1px solid var(--border-color);
    padding: 1rem 2rem;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    flex-wrap: wrap;
  }

  .brand-group {
    display: flex;
    align-items: center;
    gap: 0.85rem;
  }

  .brand-logo {
    width: 36px;
    height: 36px;
    border-radius: 50%;
  }

  .brand-title {
    font-size: 1.25rem;
    font-weight: 800;
  }

  .brand-badge {
    background: rgba(250, 204, 21, 0.15);
    color: var(--accent-yellow);
    border: 1px solid rgba(250, 204, 21, 0.4);
    font-size: 0.7rem;
    font-weight: 800;
    padding: 0.25rem 0.6rem;
    border-radius: 20px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }

  .header-actions {
    display: flex;
    align-items: center;
    gap: 0.85rem;
    flex-wrap: wrap;
  }

  /* Theme Switcher */
  .theme-segmented-control {
    display: flex;
    align-items: center;
    background: #170e26;
    border: 1px solid var(--border-color);
    border-radius: 8px;
    padding: 2px;
  }

  .theme-segment-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 0.35rem 0.55rem;
    border: none;
    border-radius: 6px;
    background: transparent;
    color: var(--text-muted);
    cursor: pointer;
    transition: all 0.15s ease;
  }

  .theme-segment-btn:hover {
    color: var(--text-main);
  }

  .theme-segment-btn.active {
    background: var(--accent-purple);
    color: #ffffff;
  }

  .btn-outline {
    background: transparent;
    border: 1px solid var(--border-color);
    color: var(--text-main);
    padding: 0.5rem 1rem;
    border-radius: var(--radius-md);
    font-weight: 600;
    font-size: 0.85rem;
    cursor: pointer;
    transition: all 0.2s ease;
  }

  .btn-outline:hover {
    background: var(--bg-card-hover);
    border-color: var(--text-muted);
  }

  /* Admin Layout */
  .admin-container {
    max-width: 1400px;
    width: 100%;
    margin: 0 auto;
    padding: 2rem;
    flex: 1;
  }

  /* Top Stat Grid */
  .stats-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 1.25rem;
    margin-bottom: 2rem;
  }

  .stat-card {
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: var(--radius-md);
    padding: 1.25rem 1.5rem;
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }

  .stat-label {
    font-size: 0.8rem;
    font-weight: 700;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }

  .stat-value {
    font-size: 1.85rem;
    font-weight: 800;
    color: var(--text-main);
    font-family: 'JetBrains Mono', monospace;
  }

  .stat-sub {
    font-size: 0.75rem;
    color: var(--text-muted);
  }

  /* Tabs Nav */
  .nav-tabs {
    display: flex;
    gap: 0.5rem;
    border-bottom: 1px solid var(--border-color);
    margin-bottom: 2rem;
    overflow-x: auto;
  }

  .tab-btn {
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    color: var(--text-muted);
    font-weight: 700;
    font-size: 0.95rem;
    padding: 0.85rem 1.25rem;
    cursor: pointer;
    transition: all 0.2s ease;
    white-space: nowrap;
  }

  .tab-btn:hover {
    color: var(--text-main);
  }

  .tab-btn.active {
    color: var(--accent-purple);
    border-bottom-color: var(--accent-purple);
  }

  /* Tab Content Panels */
  .tab-panel {
    display: none;
  }
  .tab-panel.active {
    display: block;
  }

  .badge-status {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    padding: 0.25rem 0.6rem;
    border-radius: 20px;
    font-size: 0.75rem;
    font-weight: 700;
  }

  .status-idle { background: rgba(148, 163, 184, 0.15); color: #cbd5e1; }
  .status-running { background: rgba(16, 185, 129, 0.15); color: var(--accent-green); }

  /* Controls & Cards */
  .grid-2 {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
    gap: 1.5rem;
  }

  .section-card {
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: var(--radius-md);
    padding: 1.5rem;
  }

  .card-header {
    font-size: 1.1rem;
    font-weight: 700;
    margin-bottom: 1.25rem;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  .source-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0.85rem 0;
    border-bottom: 1px solid var(--border-color);
  }

  .source-item:last-child { border-bottom: none; }

  .source-info {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
  }

  .source-name {
    font-weight: 700;
    font-size: 0.95rem;
  }

  .source-desc {
    font-size: 0.75rem;
    color: var(--text-muted);
  }

  .auth-input-group {
    margin-bottom: 1.25rem;
    text-align: left;
  }

  .auth-input-label {
    display: block;
    font-size: 0.75rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-muted);
    margin-bottom: 0.5rem;
  }

  .auth-input {
    width: 100%;
    padding: 0.85rem 1rem;
    background: #0f0c1b;
    border: 1px solid var(--border-color);
    border-radius: var(--radius-md);
    color: var(--text-main);
    font-size: 0.95rem;
    font-family: inherit;
    outline: none;
    transition: all 0.2s ease;
  }

  .auth-input:focus {
    border-color: var(--accent-purple);
    box-shadow: 0 0 0 3px rgba(147, 51, 234, 0.25);
  }

  .auth-btn {
    width: 100%;
    padding: 0.85rem 1rem;
    background: linear-gradient(135deg, var(--accent-purple) 0%, var(--accent-purple-dark) 100%);
    color: #fff;
    font-weight: 700;
    font-size: 0.95rem;
    border: none;
    border-radius: var(--radius-md);
    cursor: pointer;
    transition: all 0.2s ease;
    box-shadow: 0 4px 12px rgba(107, 33, 168, 0.3);
  }

  .auth-btn:hover {
    transform: translateY(-1px);
    box-shadow: 0 6px 16px rgba(107, 33, 168, 0.5);
  }

  /* Switch Toggle */
  .switch {
    position: relative;
    display: inline-block;
    width: 44px;
    height: 24px;
  }
  .switch input { opacity: 0; width: 0; height: 0; }
  .slider {
    position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0;
    background-color: #334155; transition: .3s; border-radius: 24px;
  }
  .slider:before {
    position: absolute; content: ""; height: 18px; width: 18px; left: 3px; bottom: 3px;
    background-color: white; transition: .3s; border-radius: 50%;
  }
  input:checked + .slider { background-color: var(--accent-green); }
  input:checked + .slider:before { transform: translateX(20px); }

  /* Auto Run Toggle & Badge */
  .auto-run-control-wrapper {
    display: inline-flex;
    align-items: center;
    gap: 0.5rem;
    background: #170e26;
    border: 1px solid var(--border-color);
    border-radius: 9999px;
    padding: 3px 10px 3px 10px;
    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05);
    user-select: none;
  }
  .auto-run-control-wrapper .auto-run-label {
    font-size: 0.75rem;
    font-weight: 600;
    color: var(--text-muted);
  }
  .auto-run-switch {
    position: relative;
    display: inline-block;
    width: 32px;
    height: 18px;
    margin: 0;
    cursor: pointer;
  }
  .auto-run-switch input { opacity: 0; width: 0; height: 0; }
  .auto-run-slider {
    position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0;
    background-color: #334155; transition: .25s; border-radius: 18px;
  }
  .auto-run-slider:before {
    position: absolute; content: ""; height: 14px; width: 14px; left: 2px; bottom: 2px;
    background-color: white; transition: .25s; border-radius: 50%;
  }
  .auto-run-switch input:checked + .auto-run-slider { background-color: var(--accent-green); }
  .auto-run-switch input:checked + .auto-run-slider:before { transform: translateX(14px); }
  .auto-run-interval-badge {
    font-size: 0.68rem;
    font-weight: 700;
    color: var(--accent-purple);
    background: rgba(168, 85, 247, 0.15);
    padding: 1px 7px;
    border-radius: 9999px;
    cursor: pointer;
  }

  /* Log Terminal */
  .terminal-box {
    background: #090d16;
    border: 1px solid var(--border-color);
    border-radius: var(--radius-md);
    padding: 1.25rem;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.85rem;
    color: #38bdf8;
    line-height: 1.6;
    max-height: 500px;
    overflow-y: auto;
    white-space: pre-wrap;
    word-break: break-all;
  }

  .log-line-error { color: #f87171; }
  .log-line-info { color: #34d399; }
  .log-line-warn { color: #fbbf24; }

  .hidden { display: none !important; }
</style>
</head>
<body>

<!-- Single User Admin / Settings Cockpit -->
<div id="admin-main">
  <header class="admin-header">
    <div class="brand-group">
      <img src="/static/images/duckduckwork_logo.png" alt="DuckDuckWork" style="width:36px; height:36px; border-radius:50%; object-fit:cover;">
      <span class="brand-title">DuckDuckWork</span>
      <span class="brand-badge" style="background:rgba(99,102,241,0.2); color:#818cf8; border:1px solid rgba(99,102,241,0.4);">SETTINGS &amp; COCKPIT</span>
    </div>
    <div class="header-actions">
      <!-- Auto Run Toggle -->
      <div class="auto-run-control-wrapper" style="display:inline-flex;" title="Automatic Run All Searches">
        <span class="auto-run-label">Auto Run</span>
        <label class="auto-run-switch">
          <input type="checkbox" id="auto-run-toggle-admin-header" onchange="toggleAdminAutoRun(this.checked)">
          <span class="auto-run-slider"></span>
        </label>
        <span id="auto-run-interval-badge-admin-header" class="auto-run-interval-badge" onclick="switchAdminTab('db')" title="Configured interval. Click to open Settings.">1h</span>
      </div>
      <!-- Theme Segmented Control -->
      <div class="theme-segmented-control" id="theme-segmented-control-admin">
        <button type="button" class="theme-segment-btn" data-theme-btn="system" onclick="setAdminTheme('system')" title="System Theme">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2" ry="2"></rect><line x1="8" y1="21" x2="16" y2="21"></line><line x1="12" y1="17" x2="12" y2="21"></line></svg>
        </button>
        <button type="button" class="theme-segment-btn" data-theme-btn="light" onclick="setAdminTheme('light')" title="Light Mode">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"></circle><line x1="12" y1="1" x2="12" y2="3"></line><line x1="12" y1="21" x2="12" y2="23"></line><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"></line><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"></line><line x1="1" y1="12" x2="3" y2="12"></line><line x1="21" y1="12" x2="23" y2="12"></line><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"></line><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"></line></svg>
        </button>
        <button type="button" class="theme-segment-btn" data-theme-btn="dark" onclick="setAdminTheme('dark')" title="Dark Mode">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path></svg>
        </button>
      </div>
      <a href="/analytics" class="btn-outline" style="text-decoration:none; display:inline-flex; align-items:center; gap:0.4rem; color:var(--text-main); font-weight:600;">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"></line><line x1="12" y1="20" x2="12" y2="4"></line><line x1="6" y1="20" x2="6" y2="14"></line></svg>
        Analytics
      </a>
      <a href="/" class="btn-outline" style="text-decoration:none; display:inline-flex; align-items:center; gap:0.4rem; color:var(--text-main); font-weight:600;">
        &larr; Back to My Searches
      </a>
      <button class="btn-outline" onclick="loadAdminData()">Refresh Status</button>
    </div>
  </header>

  <main class="admin-container">
    <!-- Top Stats -->
    <div class="stats-grid">
      <div class="stat-card">
        <span class="stat-label">Active Searches</span>
        <span class="stat-value" id="stat-session-count">0</span>
        <span class="stat-sub">Configured local searches</span>
      </div>
      <div class="stat-card">
        <span class="stat-label">Discovered Jobs</span>
        <span class="stat-value" id="stat-total-jobs" style="color:var(--accent-purple)">0</span>
        <span class="stat-sub">Saved in SQLite database</span>
      </div>
      <div class="stat-card">
        <span class="stat-label">Scored Jobs</span>
        <span class="stat-value" id="stat-scored-jobs" style="color:var(--accent-green)">0</span>
        <span class="stat-sub">Evaluated by AI with fit scoring</span>
      </div>
      <div class="stat-card">
        <span class="stat-label">AI Engine</span>
        <span class="stat-value" id="stat-ai-status" style="font-size:1.15rem; color:var(--accent-green)">Ready</span>
        <span class="stat-sub" id="stat-ai-key-masked">Configured</span>
      </div>
      <div class="stat-card">
        <span class="stat-label">Local Storage</span>
        <span class="stat-value" id="stat-storage-size">0 MB</span>
        <span class="stat-sub">In .data/ directory</span>
      </div>
    </div>

    <!-- Navigation Tabs -->
    <nav class="nav-tabs">
      <button class="tab-btn active" id="tab-btn-ai" onclick="switchAdminTab('ai')">AI Engine (BYOK)</button>
      <button class="tab-btn" id="tab-btn-sources" onclick="switchAdminTab('sources')">Job Sources</button>
      <button class="tab-btn" id="tab-btn-logs" onclick="switchAdminTab('logs')">Live Logs</button>
      <button class="tab-btn" id="tab-btn-db" onclick="switchAdminTab('db')">Database &amp; Maintenance</button>
      <button class="tab-btn" id="tab-btn-growth" onclick="switchAdminTab('growth')">Pro Waitlist &amp; Demand</button>
    </nav>

    <!-- Tab 1: AI Engine (BYOK) -->
    <section id="panel-ai" class="tab-panel active">
      <div class="grid-2">
        <div class="section-card">
          <div class="card-header">
            <span>AI Provider Configuration</span>
            <span class="badge-status status-running" id="badge-ai-provider-status">Configured</span>
          </div>
          <p style="font-size:0.85rem; color:var(--text-muted); margin-bottom:1.25rem;">
            DuckDuckWork supports any AI provider via Bring Your Own Key (BYOK) or 100% free offline local models.
          </p>

          <div class="auth-input-group">
            <label class="auth-input-label">AI Provider</label>
            <select id="select-ai-provider" class="auth-input" style="cursor:pointer;" onchange="selectAdminAiProvider(this.value)">
              <option value="gemini">Google Gemini (Recommended - Free Tier)</option>
              <option value="ollama">Ollama Local (100% Free &amp; Private Offline)</option>
              <option value="openai">OpenAI (GPT-4o / GPT-4o-mini)</option>
              <option value="anthropic">Anthropic Claude (Claude 3.5 Sonnet / Haiku)</option>
              <option value="openrouter">OpenRouter (Multi-provider / DeepSeek / Llama 3)</option>
              <option value="groq">Groq Cloud (Llama 3.3 70B - Ultra Fast &amp; Free)</option>
            </select>
          </div>

          <div class="auth-input-group" id="group-ai-key">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.4rem;">
              <label class="auth-input-label" id="label-ai-key" style="margin-bottom:0;">API Key</label>
              <button type="button" onclick="toggleAdminAiKeyVisibility()" style="background:none; border:none; color:var(--text-muted); cursor:pointer; font-size:0.75rem; font-weight:600; padding:0; display:inline-flex; align-items:center; gap:0.25rem;">
                <span id="admin-ai-key-toggle-text">Show Key</span>
              </button>
            </div>
            <input type="password" id="input-admin-ai-key" class="auth-input" placeholder="AIzaSy... or sk-..." autocomplete="off">
            <span class="source-desc" id="help-ai-key" style="display:block; margin-top:0.4rem;">
              Your key is stored strictly on your local machine.
            </span>
          </div>

          <div class="auth-input-group" id="group-ai-model">
            <label class="auth-input-label">Model Name</label>
            <input type="text" id="input-admin-ai-model" class="auth-input" value="gemini-2.0-flash" placeholder="gemini-2.0-flash, llama3.2, gpt-4o-mini...">
          </div>

          <div class="auth-input-group" id="group-ai-endpoint" style="display:none;">
            <label class="auth-input-label">Endpoint URL (Local / Custom)</label>
            <input type="text" id="input-admin-ai-endpoint" class="auth-input" value="http://localhost:11434" placeholder="http://localhost:11434">
          </div>

          <div style="display:flex; gap:0.75rem; margin-top:1.5rem;">
            <button class="btn-outline" style="flex:1;" onclick="testAdminAiConnection()">Test Connection</button>
            <button class="auth-btn" style="flex:1;" onclick="saveAdminAiConfig()">Save Settings</button>
          </div>
          <div id="ai-test-result" style="margin-top:1rem; font-size:0.85rem; display:none; padding:0.75rem; border-radius:6px;"></div>
        </div>

        <div class="section-card" id="card-ai-usage-meter" style="display:flex; flex-direction:column; justify-content:space-between;">
          <div>
            <div class="card-header">
              <span>AI Usage &amp; Quota Monitor</span>
              <span class="badge-status status-running" id="badge-ai-quota-tier">Free Tier Active</span>
            </div>
            <p style="font-size:0.85rem; color:var(--text-muted); margin-bottom:1.25rem;">
              Live tracking of your local AI evaluations and rate limits.
            </p>
            <div style="margin-top:0.5rem;">
              <div style="display:flex; justify-content:space-between; font-size:0.85rem; margin-bottom:0.4rem; color:var(--text-main);">
                <span>Daily Evaluations</span>
                <strong id="ai-usage-counter">0 / 1,500 requests</strong>
              </div>
              <div style="width:100%; height:8px; background:rgba(255,255,255,0.08); border-radius:4px; overflow:hidden;">
                <div id="ai-usage-bar" style="width:0%; height:100%; background:var(--accent-purple); border-radius:4px; transition:width 0.4s ease;"></div>
              </div>
              <div style="display:grid; grid-template-columns:repeat(3, 1fr); gap:0.6rem; margin-top:1.25rem;">
                <div style="background:rgba(255,255,255,0.03); border:1px solid var(--border-color); border-radius:8px; padding:0.6rem 0.5rem; text-align:center;">
                  <div style="font-size:0.7rem; color:var(--text-muted); text-transform:uppercase; font-weight:700;">Rate Limit</div>
                  <div id="ai-usage-rpm" style="font-size:0.95rem; font-weight:800; color:var(--text-main); margin-top:0.25rem;">15 RPM</div>
                </div>
                <div style="background:rgba(255,255,255,0.03); border:1px solid var(--border-color); border-radius:8px; padding:0.6rem 0.5rem; text-align:center;">
                  <div style="font-size:0.7rem; color:var(--text-muted); text-transform:uppercase; font-weight:700;">Daily Quota</div>
                  <div id="ai-usage-rpd" style="font-size:0.95rem; font-weight:800; color:var(--text-main); margin-top:0.25rem;">1,500 RPD</div>
                </div>
                <div style="background:rgba(255,255,255,0.03); border:1px solid var(--border-color); border-radius:8px; padding:0.6rem 0.5rem; text-align:center;">
                  <div style="font-size:0.7rem; color:var(--text-muted); text-transform:uppercase; font-weight:700;">Estimated Cost</div>
                  <div id="ai-usage-cost" style="font-size:0.95rem; font-weight:800; color:var(--accent-green); margin-top:0.25rem;">$0.00</div>
                </div>
              </div>
            </div>
          </div>
          <div style="margin-top:1.25rem; padding-top:0.85rem; border-top:1px solid var(--border-color); font-size:0.8rem; color:var(--text-muted); display:flex; justify-content:space-between; align-items:center;">
            <span>Engine Status: <strong id="ai-usage-status-text" style="color:var(--accent-green);">Connected</strong></span>
            <span id="ai-usage-latency-text" style="font-family:'JetBrains Mono',monospace; font-size:0.75rem;">Ready</span>
          </div>
        </div>
      </div>

      <div class="section-card" style="margin-top:1.25rem;">
        <div class="card-header">
          <span>Free Providers &amp; Usage Guide</span>
        </div>
        <div style="font-size:0.85rem; color:var(--text-muted); line-height:1.6; display:grid; grid-template-columns:repeat(auto-fit, minmax(260px, 1fr)); gap:1rem;">
          <div style="padding:0.5rem 0;">
            <strong style="color:var(--text-main);">Google Gemini (Google AI Studio)</strong>
            <p style="margin:0.25rem 0;">Generous free tier: <strong>15 requests per minute (RPM)</strong> and 1,500 daily requests free with Gemini 2.0/2.5 Flash. Free tier requires no credit card.</p>
            <a href="https://aistudio.google.com/app/apikey" target="_blank" rel="noopener noreferrer" style="color:var(--accent-purple); font-size:0.8rem; text-decoration:none;">Get Gemini API Key &rarr;</a>
          </div>

          <div style="padding:0.5rem 0;">
            <strong style="color:var(--text-main);">Ollama Local (Offline)</strong>
            <p style="margin:0.25rem 0;">Completely free and unlimited. Runs directly on your machine CPU/GPU (e.g. <code>ollama run llama3.2</code>). No key required.</p>
            <a href="https://ollama.com/" target="_blank" rel="noopener noreferrer" style="color:var(--accent-purple); font-size:0.8rem; text-decoration:none;">Download Ollama &rarr;</a>
          </div>

          <div style="padding:0.5rem 0;">
            <strong style="color:var(--text-main);">Groq Cloud</strong>
            <p style="margin:0.25rem 0;">Ultra-fast inference with Llama 3.3 70B. Free tier includes up to 30 requests per minute.</p>
            <a href="https://console.groq.com/keys" target="_blank" rel="noopener noreferrer" style="color:var(--accent-purple); font-size:0.8rem; text-decoration:none;">Get Groq API Key &rarr;</a>
          </div>

          <div style="padding:0.5rem 0;">
            <strong style="color:var(--text-main);">OpenAI &amp; Anthropic Claude</strong>
            <p style="margin:0.25rem 0;">Standard commercial models. You can bring your own prepaid API keys (<code>gpt-4o-mini</code> or <code>claude-3-5-haiku</code>).</p>
          </div>
        </div>
      </div>
    </section>

    <!-- Tab 2: Job Sources -->
    <section id="panel-sources" class="tab-panel">
      <div class="grid-2">
        <div class="section-card">
          <div class="card-header">
            <span>Job Portals &amp; Scrapers</span>
            <span class="stat-sub">Local Configuration</span>
          </div>
          <div class="source-item">
            <div class="source-info">
              <span class="source-name">LinkedIn Job Search</span>
              <span class="source-desc">Direct job feed and Easy Apply listings</span>
            </div>
            <label class="switch">
              <input type="checkbox" id="source-toggle-linkedin" checked onchange="saveSourcesConfig()">
              <span class="slider"></span>
            </label>
          </div>
          <div class="source-item">
            <div class="source-info">
              <span class="source-name">Indeed</span>
              <span class="source-desc">Global multi-country job search listings</span>
            </div>
            <label class="switch">
              <input type="checkbox" id="source-toggle-indeed" checked onchange="saveSourcesConfig()">
              <span class="slider"></span>
            </label>
          </div>
          <div class="source-item">
            <div class="source-info">
              <span class="source-name">Get on Board</span>
              <span class="source-desc">Tech, startup, and remote roles in Latin America</span>
            </div>
            <label class="switch">
              <input type="checkbox" id="source-toggle-getonboard" checked onchange="saveSourcesConfig()">
              <span class="slider"></span>
            </label>
          </div>
          <div class="source-item">
            <div class="source-info">
              <span class="source-name">WeWorkRemotely</span>
              <span class="source-desc">100% remote job positions worldwide</span>
            </div>
            <label class="switch">
              <input type="checkbox" id="source-toggle-weworkremotely" checked onchange="saveSourcesConfig()">
              <span class="slider"></span>
            </label>
          </div>
          <div class="source-item">
            <div class="source-info">
              <span class="source-name">Remotive</span>
              <span class="source-desc">Verified technology and product roles</span>
            </div>
            <label class="switch">
              <input type="checkbox" id="source-toggle-remotive" checked onchange="saveSourcesConfig()">
              <span class="slider"></span>
            </label>
          </div>
          <div class="source-item">
            <div class="source-info">
              <span class="source-name">Hire With Near</span>
              <span class="source-desc">Top remote roles at US companies for Latin American talent</span>
            </div>
            <label class="switch">
              <input type="checkbox" id="source-toggle-hirewithnear" checked onchange="saveSourcesConfig()">
              <span class="slider"></span>
            </label>
          </div>
        </div>

        <div class="section-card">
          <div class="card-header">
            <span>Scraping &amp; Proxy Parameters</span>
          </div>
          <div class="auth-input-group">
            <label class="auth-input-label">Maximum Jobs per Source</label>
            <input type="number" id="input-results-per-site" class="auth-input" value="50" min="10" max="200" onchange="saveSourcesConfig()">
            <span class="source-desc" style="display:block; margin-top:0.5rem">Job count limit fetched per source in each search execution.</span>
          </div>
          
          <div style="margin-top:1.25rem; padding-top:1.25rem; border-top:1px solid var(--border-color);">
            <div class="card-header" style="padding:0; margin-bottom:0.75rem;">
              <span style="font-size:0.95rem;">Webshare.io Proxy (Optional)</span>
              <span class="badge-status status-idle" id="badge-proxy-status">Not Configured</span>
            </div>
            <p style="font-size:0.8rem; color:var(--text-muted); margin-bottom:0.75rem;">
              Rotates residential IPs to prevent rate limiting during extensive crawling rounds.
            </p>
            <div class="auth-input-group">
              <label class="auth-input-label">Webshare API Key or Proxy URL</label>
              <input type="password" id="input-proxy-url" class="auth-input" placeholder="Webshare key or http://user:pass@host:port">
            </div>
            <button class="btn-outline" style="width:100%; margin-top:0.5rem;" onclick="testProxyHealth()">Test Proxy Connection</button>
            <div id="proxy-status-desc" style="font-size:0.8rem; color:var(--text-muted); margin-top:0.4rem;"></div>
          </div>

          <div style="margin-top:1.25rem;">
            <button class="auth-btn" style="width:100%;" onclick="saveSourcesConfig()">Save Sources Settings</button>
          </div>
        </div>
      </div>
    </section>

    <!-- Tab 3: Live Logs -->
    <section id="panel-logs" class="tab-panel">
      <div class="section-card">
        <div class="card-header">
          <span>Real-Time Terminal Execution</span>
          <button class="btn-outline" onclick="fetchLogs()">Refresh Terminal</button>
        </div>
        <div class="terminal-box" id="terminal-content">Loading system logs...</div>
      </div>
    </section>

    <!-- Tab 4: Database & Maintenance -->
    <section id="panel-db" class="tab-panel">
      <div class="grid-2">
        <div class="section-card">
          <div class="card-header">
            <span>SQLite Local Database</span>
            <span class="badge-status status-running">Active</span>
          </div>
          <div class="source-item">
            <div class="source-info">
              <span class="source-name">Database File Location</span>
              <span class="source-desc" style="font-family:monospace;">.data/applypilot.db</span>
            </div>
          </div>
          <div class="source-item">
            <div class="source-info">
              <span class="source-name">Total Saved Jobs</span>
              <span class="source-desc" id="db-total-jobs-detail">442 jobs</span>
            </div>
          </div>
          <div class="source-item">
            <div class="source-info">
              <span class="source-name">AI-Scored Jobs</span>
              <span class="source-desc" id="db-scored-jobs-detail">244 jobs evaluated</span>
            </div>
          </div>
          <div class="source-item">
            <div class="source-info">
              <span class="source-name">Profiles Directory</span>
              <span class="source-desc" style="font-family:monospace;">.data/searches.yaml</span>
            </div>
          </div>
          <div style="margin-top:1.5rem; display:flex; gap:0.75rem;">
            <button class="btn-outline" style="flex:1;" onclick="vacuumDatabase()">Optimize DB (VACUUM)</button>
            <button class="btn-outline" style="flex:1;" onclick="createLocalBackup()">Create Local Backup</button>
          </div>
          <div id="db-action-msg" style="margin-top:0.75rem; font-size:0.85rem; display:none; padding:0.5rem; border-radius:4px;"></div>
        </div>

        <div class="section-card">
          <div class="card-header">
            <span>Environment &amp; Server Control</span>
          </div>
          <div class="source-item">
            <div class="source-info">
              <span class="source-name">Runtime Mode</span>
              <span class="source-desc">1 Admin / 1 User Local (Open Source)</span>
            </div>
          </div>
          <div class="source-item">
            <div class="source-info">
              <span class="source-name">Server Port</span>
              <span class="source-desc">5050 (HTTP Localhost)</span>
            </div>
          </div>
          <div class="source-item">
            <div class="source-info">
              <span class="source-name">Search Runner</span>
              <span class="source-desc" id="health-runner-status">Ready / Idle</span>
            </div>
          </div>
          <div class="source-item">
            <div class="source-info">
              <span class="source-name">Maintenance Mode</span>
              <span class="source-desc">Temporarily pause public search feed</span>
            </div>
            <label class="switch">
              <input type="checkbox" id="config-toggle-maintenance" onchange="saveMaintenanceConfig()">
              <span class="slider"></span>
            </label>
          </div>
          <div class="source-item" style="flex-direction:column; align-items:flex-start; gap:0.4rem; border-top:1px solid var(--border-color); padding-top:0.75rem; margin-top:0.25rem;">
            <div class="source-info" style="width:100%;">
              <span class="source-name">External Access URL (Cloudflare Tunnel)</span>
              <span class="source-desc">Used in push notifications for 1-tap mobile opening when away on cellular data.</span>
            </div>
            <div style="display:flex; width:100%; gap:0.5rem; margin-top:0.25rem;">
              <input type="text" id="config-server-external-url" class="auth-input" placeholder="e.g. https://your-tunnel.trycloudflare.com" style="flex:1;">
              <button class="btn-outline" type="button" onclick="saveExternalUrl()" style="white-space:nowrap; padding:0 0.75rem; font-size:0.8rem;">Save</button>
            </div>
          </div>
        </div>
      </div>

      <div class="grid-2" style="margin-top:1.5rem;">
        <div class="section-card" id="card-auto-run-schedule">
          <div class="card-header">
            <span>Automated Search Schedule</span>
            <span class="badge-status status-idle" id="auto-run-admin-status-badge">Disabled</span>
          </div>
          <p style="font-size:0.85rem; color:var(--text-muted); margin-bottom:1.25rem;">
            Automatically execute all configured job searches in the background at regular intervals without manual intervention.
          </p>
          <div class="source-item">
            <div class="source-info">
              <span class="source-name">Automatic Run All Searches</span>
              <span class="source-desc">Toggle background automatic execution</span>
            </div>
            <label class="switch">
              <input type="checkbox" id="admin-auto-run-toggle" onchange="toggleAdminAutoRun(this.checked)">
              <span class="slider"></span>
            </label>
          </div>
          <div class="auth-input-group" style="margin-top:1.25rem;">
            <label class="auth-input-label">Automation Interval</label>
            <select id="admin-auto-run-interval" class="auth-input" style="cursor:pointer;" onchange="saveAdminAutoRunInterval(this.value)">
              <option value="15m">Every 15 mins</option>
              <option value="30m">Every 30 mins</option>
              <option value="1h" selected>Every 1 hour (Default)</option>
              <option value="2h">Every 2 hours</option>
            </select>
            <span class="source-desc" id="auto-run-next-run-text" style="display:block; margin-top:0.4rem;">
              Next scheduled execution: Idle (Disabled)
            </span>
          </div>
        </div>
      </div>

      <div class="grid-2" style="margin-top:1.5rem;">
        <!-- Telegram Push Alerts -->
        <div class="section-card" id="card-telegram-notifications">
          <div class="card-header">
            <span>Telegram Push Alerts</span>
            <span class="badge-status status-idle" id="telegram-admin-status-badge">Disabled</span>
          </div>
          <p style="font-size:0.85rem; color:var(--text-muted); margin-bottom:1.25rem;">
            Receive instant push notifications on your phone via Telegram when automated searches find new matching jobs.
          </p>
          <div class="source-item">
            <div class="source-info">
              <span class="source-name">Telegram Notifications</span>
              <span class="source-desc">Toggle push alerts to Telegram</span>
            </div>
            <label class="switch">
              <input type="checkbox" id="telegram-enabled-toggle" onchange="toggleTelegramNotifications(this.checked)">
              <span class="slider"></span>
            </label>
          </div>
          <div class="auth-input-group" style="margin-top:1rem;">
            <label class="auth-input-label" for="telegram-bot-token">Bot Token</label>
            <input type="text" id="telegram-bot-token" class="auth-input" placeholder="e.g. 123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ" autocomplete="off" spellcheck="false">
            <span class="source-desc" style="display:block; margin-top:0.3rem;">From @BotFather on Telegram.</span>
          </div>
          <div class="auth-input-group" style="margin-top:0.75rem;">
            <label class="auth-input-label" for="telegram-chat-id">Chat ID</label>
            <div style="display:flex; gap:0.5rem;">
              <input type="text" id="telegram-chat-id" class="auth-input" placeholder="e.g. 123456789" style="flex:1;">
              <button class="btn-outline" type="button" onclick="detectTelegramChatId()" style="white-space:nowrap; padding:0 0.75rem; font-size:0.8rem;">Detect ID</button>
            </div>
            <span class="source-desc" style="display:block; margin-top:0.3rem;">Send /start or any text to your bot, then click Detect ID.</span>
          </div>
          <div class="auth-input-group" style="margin-top:0.75rem;">
            <label class="auth-input-label" for="telegram-min-score">Minimum Fit Score Threshold</label>
            <input type="number" id="telegram-min-score" class="auth-input" placeholder="70" min="0" max="100" value="0">
            <span class="source-desc" style="display:block; margin-top:0.3rem;">Only send alerts for jobs scoring at or above this percentage (0 to notify on all new jobs).</span>
          </div>
          <div style="margin-top:1.25rem; display:flex; gap:0.75rem;">
            <button class="btn-primary" style="flex:1;" onclick="saveTelegramConfig(true)">Save Telegram Config</button>
            <button class="btn-outline" style="flex:1;" onclick="sendTestTelegramNotification()">Send Test Alert</button>
          </div>
          <div id="telegram-action-msg" style="margin-top:0.75rem; font-size:0.85rem; display:none; padding:0.5rem; border-radius:4px;"></div>
        </div>

        <!-- WhatsApp Push Alerts (CallMeBot) -->
        <div class="section-card" id="card-whatsapp-notifications">
          <div class="card-header">
            <span>WhatsApp Push Alerts</span>
            <span class="badge-status status-idle" id="whatsapp-admin-status-badge">Disabled</span>
          </div>
          <p style="font-size:0.85rem; color:var(--text-muted); margin-bottom:1.25rem;">
            Receive free instant push notifications on WhatsApp via CallMeBot gateway with direct Wi-Fi links to open on your phone.
          </p>
          <div class="source-item">
            <div class="source-info">
              <span class="source-name">WhatsApp Notifications</span>
              <span class="source-desc">Toggle push alerts to WhatsApp</span>
            </div>
            <label class="switch">
              <input type="checkbox" id="whatsapp-enabled-toggle" onchange="toggleWhatsAppNotifications(this.checked)">
              <span class="slider"></span>
            </label>
          </div>
          <div class="auth-input-group" style="margin-top:1rem;">
            <label class="auth-input-label" for="whatsapp-phone">Phone Number (with Country Code)</label>
            <input type="text" id="whatsapp-phone" class="auth-input" placeholder="e.g. 521999999999 or 14155552671" autocomplete="off" spellcheck="false">
            <span class="source-desc" style="display:block; margin-top:0.3rem;">Digits only including country prefix (e.g. 521... for Mexico, 1... for US).</span>
          </div>
          <div class="auth-input-group" style="margin-top:0.75rem;">
            <label class="auth-input-label" for="whatsapp-api-key">CallMeBot API Key</label>
            <input type="text" id="whatsapp-api-key" class="auth-input" placeholder="e.g. 123456" autocomplete="off" spellcheck="false">
            <span class="source-desc" style="display:block; margin-top:0.3rem;">
              Send <code>I allow callmebot to send me messages</code> to the active CallMeBot number (see <a href="https://www.callmebot.com/blog/free-api-whatsapp-messages/" target="_blank" rel="noopener" style="color:var(--accent-blue); text-decoration:underline;">CallMeBot Gateway</a>) to receive your API key once slots open.
            </span>
          </div>
          <div style="margin-top:1.25rem; display:flex; gap:0.75rem;">
            <button class="btn-primary" style="flex:1;" onclick="saveWhatsAppConfig(true)">Save WhatsApp Config</button>
            <button class="btn-outline" style="flex:1;" onclick="sendTestWhatsAppNotification()">Send Test Alert</button>
          </div>
          <div id="whatsapp-action-msg" style="margin-top:0.75rem; font-size:0.85rem; display:none; padding:0.5rem; border-radius:4px;"></div>
        </div>
      </div>
    </section>

    <!-- Tab 5: Pro Waitlist & Demand Telemetry -->
    <section id="panel-growth" class="tab-panel">
      <div class="stats-grid" style="margin-bottom:1.5rem;">
        <div class="stat-card">
          <span class="stat-label">Pro Waitlist Signups</span>
          <span class="stat-value" id="stat-waitlist-signups" style="color:var(--accent-green);">0</span>
          <span class="stat-sub">Early Access email leads</span>
        </div>
        <div class="stat-card">
          <span class="stat-label">Quota Gate Views</span>
          <span class="stat-value" id="stat-paywall-views" style="color:var(--accent-purple);">0</span>
          <span class="stat-sub">Users hitting free cloud limit</span>
        </div>
        <div class="stat-card">
          <span class="stat-label">BYOK Connected</span>
          <span class="stat-value" id="stat-byok-connected">0</span>
          <span class="stat-sub">Users connecting own API key</span>
        </div>
        <div class="stat-card">
          <span class="stat-label">GitHub Repo Clicks</span>
          <span class="stat-value" id="stat-github-clicks">0</span>
          <span class="stat-sub">Local open-source downloads</span>
        </div>
        <div class="stat-card">
          <span class="stat-label">Cloud Searches Run</span>
          <span class="stat-value" id="stat-cloud-searches">0</span>
          <span class="stat-sub">Total public cloud searches</span>
        </div>
      </div>

      <div class="section-card">
        <div class="card-header">
          <div>
            <span>DuckDuckWork Pro &mdash; Early Access Waitlist Leads</span>
            <p style="font-size:0.78rem; color:var(--text-muted); font-weight:500; margin-top:0.2rem;">
              Synced automatically with Neon PostgreSQL (<code>duckduckwork_waitlist</code>) and local fallback storage.
            </p>
          </div>
          <button type="button" class="btn-outline" onclick="exportWaitlistCsv()">Export Waitlist CSV</button>
        </div>

        <div style="overflow-x:auto;">
          <table style="width:100%; border-collapse:collapse; font-size:0.86rem; text-align:left;">
            <thead>
              <tr style="border-bottom:1px solid var(--border-color); color:var(--text-muted); font-size:0.74rem; text-transform:uppercase; letter-spacing:0.05em;">
                <th style="padding:0.75rem 0.6rem;">Email</th>
                <th style="padding:0.75rem 0.6rem;">Target Role</th>
                <th style="padding:0.75rem 0.6rem;">Session ID</th>
                <th style="padding:0.75rem 0.6rem;">Source</th>
                <th style="padding:0.75rem 0.6rem;">Joined At</th>
              </tr>
            </thead>
            <tbody id="waitlist-table-body">
              <tr>
                <td colspan="5" style="padding:1.25rem 0.6rem; color:var(--text-muted); text-align:center;">No early access signups recorded yet.</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>
  </main>
</div>

<script>
  let adminCsrfToken = '';

  function checkUrlHash() {
    const hash = window.location.hash.replace('#', '');
    if (hash) {
      switchAdminTab(hash);
    }
  }

  window.addEventListener('hashchange', checkUrlHash);

  document.addEventListener('DOMContentLoaded', () => {
    initAdminTheme();
    loadAdminData();
    loadAiConfig();
    checkUrlHash();
    setTimeout(checkUrlHash, 100);
  });

  function initAdminTheme() {
    const mode = localStorage.getItem('applypilot_theme_mode') || 'system';
    applyAdminTheme(mode);
    if (window.matchMedia) {
      window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
        const cur = localStorage.getItem('applypilot_theme_mode') || 'system';
        if (cur === 'system') applyAdminTheme('system');
      });
    }
  }

  function setAdminTheme(mode) {
    if (!['system', 'light', 'dark'].includes(mode)) return;
    localStorage.setItem('applypilot_theme_mode', mode);
    applyAdminTheme(mode);
  }

  function applyAdminTheme(mode) {
    let effective = mode;
    if (mode === 'system') {
      effective = (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light';
    }
    document.documentElement.setAttribute('data-theme', effective);
    document.documentElement.setAttribute('data-theme-mode', mode);

    document.querySelectorAll('.theme-segment-btn').forEach(btn => {
      if (btn.dataset.themeBtn === mode) {
        btn.classList.add('active');
      } else {
        btn.classList.remove('active');
      }
    });
  }

  function switchAdminTab(tabName) {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));

    const btn = document.getElementById('tab-btn-' + tabName);
    const panel = document.getElementById('panel-' + tabName);
    if (btn) btn.classList.add('active');
    if (panel) panel.classList.add('active');

    if (tabName === 'logs') fetchLogs();
  }

  function selectAdminAiProvider(prov) {
    const keyGroup = document.getElementById('group-ai-key');
    const keyLabel = document.getElementById('label-ai-key');
    const keyInput = document.getElementById('input-admin-ai-key');
    const modelInput = document.getElementById('input-admin-ai-model');
    const endpointGroup = document.getElementById('group-ai-endpoint');
    const endpointInput = document.getElementById('input-admin-ai-endpoint');
    const helpKey = document.getElementById('help-ai-key');

    if (prov === 'gemini') {
      keyGroup.style.display = 'block';
      keyLabel.textContent = 'Google Gemini API Key';
      keyInput.placeholder = 'AIzaSy...';
      modelInput.value = 'gemini-2.5-flash';
      endpointGroup.style.display = 'none';
      helpKey.textContent = 'Free tier key from Google AI Studio (15 RPM / 1,500 daily requests).';
    } else if (prov === 'ollama') {
      keyGroup.style.display = 'none';
      modelInput.value = 'llama3.2';
      endpointGroup.style.display = 'block';
      endpointInput.value = 'http://localhost:11434';
      helpKey.textContent = 'Ollama runs locally on your machine with zero API keys required.';
    } else if (prov === 'openai') {
      keyGroup.style.display = 'block';
      keyLabel.textContent = 'OpenAI API Key';
      keyInput.placeholder = 'sk-...';
      modelInput.value = 'gpt-4o-mini';
      endpointGroup.style.display = 'none';
      helpKey.textContent = 'API key from your OpenAI platform account. gpt-4o-mini is cost-effective.';
    } else if (prov === 'anthropic') {
      keyGroup.style.display = 'block';
      keyLabel.textContent = 'Anthropic Claude API Key';
      keyInput.placeholder = 'sk-ant-...';
      modelInput.value = 'claude-3-5-sonnet-20241022';
      endpointGroup.style.display = 'none';
      helpKey.textContent = 'API key from your Anthropic Console.';
    } else if (prov === 'openrouter') {
      keyGroup.style.display = 'block';
      keyLabel.textContent = 'OpenRouter API Key';
      keyInput.placeholder = 'sk-or-...';
      modelInput.value = 'meta-llama/llama-3.3-70b-instruct';
      endpointGroup.style.display = 'block';
      endpointInput.value = 'https://openrouter.ai/api/v1';
      helpKey.textContent = 'Unified API access to open source and commercial models.';
    } else if (prov === 'groq') {
      keyGroup.style.display = 'block';
      keyLabel.textContent = 'Groq Cloud API Key';
      keyInput.placeholder = 'gsk_...';
      modelInput.value = 'llama-3.3-70b-versatile';
      endpointGroup.style.display = 'none';
      helpKey.textContent = 'Ultra-fast free inference with Llama 3.3.';
    }
  }

  function toggleAdminAiKeyVisibility() {
    const input = document.getElementById('input-admin-ai-key');
    const label = document.getElementById('admin-ai-key-toggle-text');
    if (!input) return;
    if (input.type === 'password') {
      input.type = 'text';
      if (label) label.textContent = 'Hide Key';
    } else {
      input.type = 'password';
      if (label) label.textContent = 'Show Key';
    }
  }

  async function loadAiConfig() {
    try {
      const resp = await fetch('/api/llm/status');
      if (resp.ok) {
        const data = await resp.json();
        const sel = document.getElementById('select-ai-provider');
        if (sel && data.provider) {
          sel.value = data.provider;
          selectAdminAiProvider(data.provider);
        }
        if (data.model) {
          document.getElementById('input-admin-ai-model').value = data.model;
        }
        if (data.endpoint) {
          document.getElementById('input-admin-ai-endpoint').value = data.endpoint;
        }
        if (data.api_key) {
          const ki = document.getElementById('input-admin-ai-key');
          if (ki && !ki.value) ki.value = data.api_key;
        }
        const b = document.getElementById('badge-ai-provider-status');
        if (b) {
          b.textContent = data.configured ? 'Configured' : 'Pending';
          b.className = 'badge-status ' + (data.configured ? 'status-running' : 'status-idle');
        }
      }
    } catch (e) {
      console.warn('Error loading AI config:', e);
    }
  }

  async function testAdminAiConnection() {
    const resEl = document.getElementById('ai-test-result');
    resEl.style.display = 'block';
    resEl.style.background = 'rgba(99,102,241,0.15)';
    resEl.style.color = '#818cf8';
    resEl.style.border = '1px solid rgba(99,102,241,0.3)';
    resEl.textContent = 'Sending test prompt to AI provider...';

    const provider = document.getElementById('select-ai-provider').value;
    const apiKey = document.getElementById('input-admin-ai-key').value;
    const model = document.getElementById('input-admin-ai-model').value;
    const endpoint = document.getElementById('input-admin-ai-endpoint').value;

    try {
      const resp = await fetch('/api/llm/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider, api_key: apiKey, model, endpoint })
      });
      const data = await resp.json();
      if (data.success) {
        resEl.style.background = 'rgba(16,185,129,0.15)';
        resEl.style.color = '#34d399';
        resEl.style.border = '1px solid rgba(16,185,129,0.3)';
        resEl.textContent = 'Connection Successful (' + (data.latency_ms || 0) + 'ms): ' + (data.message || data.reply || 'Response received.');
        const latEl = document.getElementById('ai-usage-latency-text');
        if (latEl) latEl.textContent = (data.latency_ms || 0) + ' ms latency';
      } else {
        resEl.style.background = 'rgba(239,68,68,0.15)';
        resEl.style.color = '#f87171';
        resEl.style.border = '1px solid rgba(239,68,68,0.3)';
        resEl.textContent = 'Connection failed: ' + (data.error || data.message || 'Could not connect to provider.');
      }
    } catch (e) {
      resEl.style.background = 'rgba(239,68,68,0.15)';
      resEl.style.color = '#f87171';
      resEl.style.border = '1px solid rgba(239,68,68,0.3)';
      resEl.textContent = 'Network error while connecting: ' + e;
    }
  }

  async function saveAdminAiConfig() {
    const provider = document.getElementById('select-ai-provider').value;
    const apiKey = document.getElementById('input-admin-ai-key').value;
    const model = document.getElementById('input-admin-ai-model').value;
    const endpoint = document.getElementById('input-admin-ai-endpoint').value;

    try {
      const resp = await fetch('/api/llm/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider, api_key: apiKey, model, endpoint })
      });
      const data = await resp.json();
      if (data.success) {
        alert('AI configuration saved locally.');
        loadAdminData();
      } else {
        alert('Error saving configuration: ' + (data.error || 'Unknown'));
      }
    } catch (e) {
      alert('Error saving AI configuration: ' + e);
    }
  }

  async function loadAdminData() {
    try {
      const resp = await fetch('/api/admin/data?_t=' + Date.now(), { cache: 'no-store' });
      if (!resp.ok) return;
      const data = await resp.json();
      if (data.csrf_token) adminCsrfToken = data.csrf_token;

      // Stats
      const userSearches = data.user_searches_count || (data.sessions ? data.sessions.length : 0);
      const userJobs = data.user_jobs_count !== undefined ? data.user_jobs_count : data.total_jobs;
      const userScored = data.user_scored_jobs !== undefined ? data.user_scored_jobs : 0;

      document.getElementById('stat-session-count').textContent = userSearches;
      document.getElementById('stat-total-jobs').textContent = userJobs;
      document.getElementById('stat-scored-jobs').textContent = userScored;
      document.getElementById('stat-storage-size').textContent = (data.system.storage_size_mb || 0) + ' MB';

      document.getElementById('db-total-jobs-detail').textContent = userJobs + ' jobs';
      document.getElementById('db-scored-jobs-detail').textContent = userScored + ' jobs evaluated with fit score';

      const aiStatus = data.system.gemini_key_configured ? 'Ready' : 'Pending';
      document.getElementById('stat-ai-status').textContent = aiStatus;
      document.getElementById('stat-ai-key-masked').textContent = data.system.gemini_key_masked || 'Configured';

      // Set input key if currently empty
      if (data.system && (data.system.ai_key || data.system.gemini_api_key)) {
        const keyVal = data.system.ai_key || data.system.gemini_api_key;
        const ki = document.getElementById('input-admin-ai-key');
        if (ki && !ki.value) ki.value = keyVal;
      }
      if (data.system && data.system.ai_provider) {
        const sel = document.getElementById('select-ai-provider');
        if (sel) {
          sel.value = data.system.ai_provider;
          selectAdminAiProvider(data.system.ai_provider);
        }
      }
      if (data.system && data.system.gemini_model) {
        const mi = document.getElementById('input-admin-ai-model');
        if (mi && !mi.value) mi.value = data.system.gemini_model;
      }

      // Populate AI usage meter
      if (data.system && data.system.ai_usage) {
        const u = data.system.ai_usage;
        const reqs = u.today_requests || 0;
        const limit = u.daily_limit || 1500;
        const pct = limit > 0 ? Math.min(100, Math.round((reqs / limit) * 100)) : 0;
        const counterEl = document.getElementById('ai-usage-counter');
        if (counterEl) counterEl.textContent = reqs + ' / ' + (limit ? limit.toLocaleString() : 'Unlimited') + ' requests';
        const barEl = document.getElementById('ai-usage-bar');
        if (barEl) barEl.style.width = pct + '%';
        const rpmEl = document.getElementById('ai-usage-rpm');
        if (rpmEl) rpmEl.textContent = (u.rpm_limit || 15) + ' RPM';
        const rpdEl = document.getElementById('ai-usage-rpd');
        if (rpdEl) rpdEl.textContent = limit ? (limit.toLocaleString() + ' RPD') : 'Unlimited';
        const costEl = document.getElementById('ai-usage-cost');
        if (costEl) costEl.textContent = u.cost_est || '$0.00';
        const tierBadge = document.getElementById('badge-ai-quota-tier');
        if (tierBadge) tierBadge.textContent = u.tier || 'Free Tier';
        const stText = document.getElementById('ai-usage-status-text');
        if (stText) stText.textContent = u.status || 'Ready';
      }

      // Proxy state
      const proxyBadge = document.getElementById('badge-proxy-status');
      if (proxyBadge) {
        proxyBadge.textContent = data.system.proxy_configured ? 'Active' : 'Not Configured';
        proxyBadge.className = 'badge-status ' + (data.system.proxy_configured ? 'status-running' : 'status-idle');
      }

      // Maintenance Mode State
      const isMaint = !!data.system.maintenance_mode;
      document.getElementById('config-toggle-maintenance').checked = isMaint;

      // Server External URL (Cloudflare Tunnel)
      const extUrl = (data.system && data.system.server_external_url) || (data.notifications && data.notifications.server_external_url) || '';
      const extInput = document.getElementById('config-server-external-url');
      if (extInput && !extInput.value) extInput.value = extUrl;

      // Auto-run State
      if (data.system && data.system.auto_run) {
        const ar = data.system.auto_run;
        const arToggle = document.getElementById('admin-auto-run-toggle');
        if (arToggle) arToggle.checked = !!ar.enabled;
        const arHeaderToggle = document.getElementById('auto-run-toggle-admin-header');
        if (arHeaderToggle) arHeaderToggle.checked = !!ar.enabled;

        const arInterval = document.getElementById('admin-auto-run-interval');
        if (arInterval) arInterval.value = ar.interval || '1h';

        const arBadge = document.getElementById('auto-run-admin-status-badge');
        if (arBadge) {
          arBadge.textContent = ar.enabled ? 'Active (' + (ar.interval || '1h') + ')' : 'Disabled';
          arBadge.className = 'badge-status ' + (ar.enabled ? 'status-running' : 'status-idle');
        }

        const arHeaderBadge = document.getElementById('auto-run-interval-badge-admin-header');
        if (arHeaderBadge) arHeaderBadge.textContent = ar.interval || '1h';

        const arNextText = document.getElementById('auto-run-next-run-text');
        if (arNextText) {
          if (ar.enabled && ar.next_run > 0) {
            const nextDate = new Date(ar.next_run * 1000);
            arNextText.textContent = 'Next scheduled execution: ' + nextDate.toLocaleTimeString();
          } else {
            arNextText.textContent = 'Next scheduled execution: Idle (Disabled)';
          }
        }
      }

      // Notifications State (Telegram & WhatsApp)
      const notif = data.notifications || (data.system && data.system.notifications);
      if (notif) {
        const tgToggle = document.getElementById('telegram-enabled-toggle');
        if (tgToggle) tgToggle.checked = !!notif.telegram_enabled;

        const tgTokenInput = document.getElementById('telegram-bot-token');
        if (tgTokenInput && !tgTokenInput.value) {
          tgTokenInput.value = notif.telegram_bot_token_masked || notif.telegram_bot_token || '';
        }

        const tgChatInput = document.getElementById('telegram-chat-id');
        if (tgChatInput && !tgChatInput.value) {
          tgChatInput.value = notif.telegram_chat_id || '';
        }

        const tgMinScoreInput = document.getElementById('telegram-min-score');
        if (tgMinScoreInput) {
          tgMinScoreInput.value = notif.telegram_min_score || 0;
        }

        const tgBadge = document.getElementById('telegram-admin-status-badge');
        if (tgBadge) {
          if (!notif.telegram_configured) {
            tgBadge.textContent = 'Not Configured';
            tgBadge.className = 'badge-status status-idle';
          } else if (notif.telegram_enabled) {
            tgBadge.textContent = 'Active';
            tgBadge.className = 'badge-status status-running';
          } else {
            tgBadge.textContent = 'Disabled';
            tgBadge.className = 'badge-status status-idle';
          }
        }

        const waToggle = document.getElementById('whatsapp-enabled-toggle');
        if (waToggle) waToggle.checked = !!notif.whatsapp_enabled;

        const waPhoneInput = document.getElementById('whatsapp-phone');
        if (waPhoneInput && !waPhoneInput.value) {
          waPhoneInput.value = notif.whatsapp_phone || '';
        }

        const waKeyInput = document.getElementById('whatsapp-api-key');
        if (waKeyInput && !waKeyInput.value) {
          waKeyInput.value = notif.whatsapp_api_key_masked || notif.whatsapp_api_key || '';
        }

        const waBadge = document.getElementById('whatsapp-admin-status-badge');
        if (waBadge) {
          if (!notif.whatsapp_configured) {
            waBadge.textContent = 'Not Configured';
            waBadge.className = 'badge-status status-idle';
          } else if (notif.whatsapp_enabled) {
            waBadge.textContent = 'Active';
            waBadge.className = 'badge-status status-running';
          } else {
            waBadge.textContent = 'Disabled';
            waBadge.className = 'badge-status status-idle';
          }
        }
      }

      // Pro Waitlist & Demand Telemetry
      const tel = data.telemetry || {};
      const wl = Array.isArray(data.waitlist) ? data.waitlist : [];
      window._adminWaitlistLeads = wl;
      const elSignups = document.getElementById('stat-waitlist-signups');
      if (elSignups) elSignups.textContent = Math.max(tel.waitlist_signups || 0, wl.length);
      const elPaywall = document.getElementById('stat-paywall-views');
      if (elPaywall) elPaywall.textContent = tel.paywall_views || 0;
      const elByok = document.getElementById('stat-byok-connected');
      if (elByok) elByok.textContent = tel.byok_connected || 0;
      const elGh = document.getElementById('stat-github-clicks');
      if (elGh) elGh.textContent = tel.github_clicks || 0;
      const elCloudSearches = document.getElementById('stat-cloud-searches');
      if (elCloudSearches) elCloudSearches.textContent = tel.cloud_searches_run || 0;

      const tbody = document.getElementById('waitlist-table-body');
      if (tbody) {
        if (wl.length === 0) {
          tbody.innerHTML = '<tr><td colspan="5" style="padding:1.25rem 0.6rem; color:var(--text-muted); text-align:center;">No early access signups recorded yet.</td></tr>';
        } else {
          tbody.innerHTML = wl.map(item => {
            const em = String(item.email || '').replace(/</g, '&lt;');
            const rl = String(item.target_role || '-').replace(/</g, '&lt;');
            const sid = String(item.session_id || 'local').replace(/</g, '&lt;');
            const src = String(item.source || 'pro_modal').replace(/</g, '&lt;');
            const dt = String(item.created_at || '-').replace(/</g, '&lt;');
            return `<tr style="border-bottom:1px solid var(--border-color);">
              <td style="padding:0.75rem 0.6rem; font-weight:700; color:var(--text-main);">${em}</td>
              <td style="padding:0.75rem 0.6rem; color:var(--text-main);">${rl}</td>
              <td style="padding:0.75rem 0.6rem; font-family:'JetBrains Mono',monospace; font-size:0.78rem; color:var(--text-muted);">${sid}</td>
              <td style="padding:0.75rem 0.6rem; color:var(--text-muted);">${src}</td>
              <td style="padding:0.75rem 0.6rem; font-family:'JetBrains Mono',monospace; font-size:0.78rem; color:var(--text-muted);">${dt}</td>
            </tr>`;
          }).join('');
        }
      }

      // Terminal logs
      document.getElementById('terminal-content').textContent = data.logs || 'No log entries found.';
    } catch (err) {
      console.warn('Error loading admin data:', err);
    }
  }

  function exportWaitlistCsv() {
    const wl = window._adminWaitlistLeads || [];
    if (!wl.length) {
      alert('No waitlist leads to export yet.');
      return;
    }
    const header = ['email', 'target_role', 'session_id', 'source', 'created_at'];
    const rows = wl.map(item => header.map(k => '"' + String(item[k] || '').replace(/"/g, '""') + '"').join(','));
    const csvContent = [header.join(','), ...rows].join('\\n');
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'duckduckwork_pro_waitlist.csv';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  async function saveSourcesConfig() {
    const config = {
      linkedin: document.getElementById('source-toggle-linkedin').checked,
      indeed: document.getElementById('source-toggle-indeed').checked,
      getonboard: document.getElementById('source-toggle-getonboard').checked,
      weworkremotely: document.getElementById('source-toggle-weworkremotely').checked,
      remotive: document.getElementById('source-toggle-remotive').checked,
      hirewithnear: document.getElementById('source-toggle-hirewithnear') ? document.getElementById('source-toggle-hirewithnear').checked : true,
      results_per_site: parseInt(document.getElementById('input-results-per-site').value, 10) || 50
    };

    try {
      const resp = await fetch('/api/admin/sources/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': adminCsrfToken },
        body: JSON.stringify(config)
      });
      const data = await resp.json();
      if (data.success) {
        alert('Sources configuration saved successfully.');
      }
    } catch (e) {
      alert('Error saving sources: ' + e);
    }
  }

  async function fetchLogs() {
    try {
      const resp = await fetch('/api/admin/data?_t=' + Date.now(), { cache: 'no-store' });
      const data = await resp.json();
      document.getElementById('terminal-content').textContent = data.logs || 'No log entries found.';
    } catch (e) {
      console.error(e);
    }
  }

  async function testProxyHealth() {
    const statusEl = document.getElementById('badge-proxy-status');
    const descEl = document.getElementById('proxy-status-desc');
    if (statusEl) {
      statusEl.textContent = 'Testing...';
      statusEl.className = 'badge-status status-idle';
    }
    try {
      const resp = await fetch('/api/admin/proxy/test?_t=' + Date.now(), { cache: 'no-store' });
      const data = await resp.json();
      if (statusEl) {
        statusEl.textContent = data.success ? 'Active' : 'Failed';
        statusEl.className = 'badge-status ' + (data.success ? 'status-running' : 'status-idle');
      }
      if (descEl) {
        if (data.ip) {
          descEl.textContent = `Connected IP: ${data.ip} (${data.latency_ms}ms latency)`;
        } else if (data.message) {
          descEl.textContent = data.message;
        }
      }
      alert(data.message || 'Proxy test completed.');
    } catch (e) {
      if (statusEl) {
        statusEl.textContent = 'Failed';
        statusEl.className = 'badge-status status-idle';
      }
      alert('Error testing proxy connection: ' + e);
    }
  }

  async function saveMaintenanceConfig() {
    const isMaintenance = document.getElementById('config-toggle-maintenance').checked;
    try {
      const resp = await fetch('/api/admin/maintenance/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': adminCsrfToken },
        body: JSON.stringify({ maintenance_mode: isMaintenance })
      });
      const data = await resp.json();
      if (data.success) {
        alert('Maintenance mode updated.');
      }
    } catch (e) {
      alert('Error updating maintenance mode.');
    }
  }

  async function saveExternalUrl() {
    const val = (document.getElementById('config-server-external-url').value || '').trim();
    try {
      const resp = await fetch('/api/settings/notifications', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ server_external_url: val })
      });
      const data = await resp.json();
      if (data.success) {
        alert('External Access URL saved successfully.');
        loadAdminData();
      }
    } catch (e) {
      alert('Error saving external URL: ' + e);
    }
  }

  async function toggleAdminAutoRun(enabled) {
    try {
      const resp = await fetch('/api/settings/auto-run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': adminCsrfToken },
        body: JSON.stringify({ enabled: enabled })
      });
      const data = await resp.json();
      if (data.success) {
        loadAdminData();
      }
    } catch (e) {
      alert('Error updating auto-run setting: ' + e);
    }
  }

  async function saveAdminAutoRunInterval(val) {
    try {
      const resp = await fetch('/api/settings/auto-run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': adminCsrfToken },
        body: JSON.stringify({ interval: val })
      });
      const data = await resp.json();
      if (data.success) {
        loadAdminData();
      }
    } catch (e) {
      alert('Error updating auto-run interval: ' + e);
    }
  }

  async function toggleTelegramNotifications(enabled) {
    try {
      const resp = await fetch('/api/settings/notifications', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ telegram_enabled: !!enabled })
      });
      const data = await resp.json();
      if (data.success && data.notifications) {
        const notif = data.notifications;
        const tgBadge = document.getElementById('telegram-admin-status-badge');
        if (tgBadge) {
          if (!notif.telegram_configured) {
            tgBadge.textContent = 'Not Configured';
            tgBadge.className = 'badge-status status-idle';
          } else if (notif.telegram_enabled) {
            tgBadge.textContent = 'Active';
            tgBadge.className = 'badge-status status-running';
          } else {
            tgBadge.textContent = 'Disabled';
            tgBadge.className = 'badge-status status-idle';
          }
        }
      }
    } catch (e) {
      console.error('Error toggling Telegram:', e);
    }
  }

  async function saveTelegramConfig(showNotice) {
    const botToken = document.getElementById('telegram-bot-token').value.trim();
    const chatId = document.getElementById('telegram-chat-id').value.trim();
    const minScore = parseInt(document.getElementById('telegram-min-score').value, 10) || 0;
    const enabled = document.getElementById('telegram-enabled-toggle').checked;
    const msgEl = document.getElementById('telegram-action-msg');

    try {
      const resp = await fetch('/api/settings/notifications', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          telegram_enabled: enabled,
          telegram_bot_token: botToken,
          telegram_chat_id: chatId,
          telegram_min_score: minScore
        })
      });
      const data = await resp.json();
      if (data.success) {
        if (showNotice && msgEl) {
          msgEl.style.display = 'block';
          msgEl.style.background = 'rgba(16, 185, 129, 0.15)';
          msgEl.style.color = 'var(--accent-green)';
          msgEl.textContent = 'Telegram configuration saved successfully.';
          setTimeout(() => { msgEl.style.display = 'none'; }, 4000);
        }
        loadAdminData();
      } else {
        if (msgEl) {
          msgEl.style.display = 'block';
          msgEl.style.background = 'rgba(239, 68, 68, 0.15)';
          msgEl.style.color = '#ef4444';
          msgEl.textContent = 'Error: ' + (data.error || 'Failed to save');
        }
      }
    } catch (e) {
      if (msgEl) {
        msgEl.style.display = 'block';
        msgEl.style.background = 'rgba(239, 68, 68, 0.15)';
        msgEl.style.color = '#ef4444';
        msgEl.textContent = 'Error saving: ' + e;
      }
    }
  }

  async function detectTelegramChatId() {
    const botToken = document.getElementById('telegram-bot-token').value.trim();
    const msgEl = document.getElementById('telegram-action-msg');
    if (msgEl) {
      msgEl.style.display = 'block';
      msgEl.style.background = 'rgba(59, 130, 246, 0.15)';
      msgEl.style.color = 'var(--accent-blue)';
      msgEl.textContent = 'Detecting chat ID from recent Telegram messages...';
    }

    try {
      const resp = await fetch('/api/settings/notifications/detect-chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ bot_token: botToken })
      });
      const data = await resp.json();
      if (data.success && data.chat_id) {
        document.getElementById('telegram-chat-id').value = data.chat_id;
        if (msgEl) {
          msgEl.style.background = 'rgba(16, 185, 129, 0.15)';
          msgEl.style.color = 'var(--accent-green)';
          msgEl.textContent = data.message || 'Chat ID detected: ' + data.chat_id;
          setTimeout(() => { msgEl.style.display = 'none'; }, 4000);
        }
        loadAdminData();
      } else {
        if (msgEl) {
          msgEl.style.background = 'rgba(239, 68, 68, 0.15)';
          msgEl.style.color = '#ef4444';
          msgEl.textContent = data.message || 'Could not detect chat ID. Send a message to your bot first.';
        }
      }
    } catch (e) {
      if (msgEl) {
        msgEl.style.background = 'rgba(239, 68, 68, 0.15)';
        msgEl.style.color = '#ef4444';
        msgEl.textContent = 'Error: ' + e;
      }
    }
  }

  async function sendTestTelegramNotification() {
    const botToken = document.getElementById('telegram-bot-token').value.trim();
    const chatId = document.getElementById('telegram-chat-id').value.trim();
    const msgEl = document.getElementById('telegram-action-msg');
    if (msgEl) {
      msgEl.style.display = 'block';
      msgEl.style.background = 'rgba(59, 130, 246, 0.15)';
      msgEl.style.color = 'var(--accent-blue)';
      msgEl.textContent = 'Sending test Telegram notification...';
    }

    try {
      const resp = await fetch('/api/settings/notifications/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ bot_token: botToken, chat_id: chatId })
      });
      const data = await resp.json();
      if (data.success) {
        if (msgEl) {
          msgEl.style.background = 'rgba(16, 185, 129, 0.15)';
          msgEl.style.color = 'var(--accent-green)';
          msgEl.textContent = data.message || 'Test notification delivered successfully.';
          setTimeout(() => { msgEl.style.display = 'none'; }, 4000);
        }
      } else {
        if (msgEl) {
          msgEl.style.background = 'rgba(239, 68, 68, 0.15)';
          msgEl.style.color = '#ef4444';
          msgEl.textContent = 'Delivery failed: ' + (data.message || data.error || 'Unknown error');
        }
      }
    } catch (e) {
      if (msgEl) {
        msgEl.style.background = 'rgba(239, 68, 68, 0.15)';
        msgEl.style.color = '#ef4444';
        msgEl.textContent = 'Error: ' + e;
      }
    }
  }

  async function toggleWhatsAppNotifications(enabled) {
    try {
      const resp = await fetch('/api/settings/notifications', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ whatsapp_enabled: !!enabled })
      });
      const data = await resp.json();
      if (data.success && data.notifications) {
        const notif = data.notifications;
        const waBadge = document.getElementById('whatsapp-admin-status-badge');
        if (waBadge) {
          if (!notif.whatsapp_configured) {
            waBadge.textContent = 'Not Configured';
            waBadge.className = 'badge-status status-idle';
          } else if (notif.whatsapp_enabled) {
            waBadge.textContent = 'Active';
            waBadge.className = 'badge-status status-running';
          } else {
            waBadge.textContent = 'Disabled';
            waBadge.className = 'badge-status status-idle';
          }
        }
      }
    } catch (e) {
      console.error('Error toggling WhatsApp:', e);
    }
  }

  async function saveWhatsAppConfig(showNotice) {
    const phone = document.getElementById('whatsapp-phone').value.trim();
    const apiKey = document.getElementById('whatsapp-api-key').value.trim();
    const enabled = document.getElementById('whatsapp-enabled-toggle').checked;
    const msgEl = document.getElementById('whatsapp-action-msg');

    try {
      const resp = await fetch('/api/settings/notifications', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          whatsapp_enabled: enabled,
          whatsapp_phone: phone,
          whatsapp_api_key: apiKey
        })
      });
      const data = await resp.json();
      if (data.success) {
        if (showNotice && msgEl) {
          msgEl.style.display = 'block';
          msgEl.style.background = 'rgba(16, 185, 129, 0.15)';
          msgEl.style.color = 'var(--accent-green)';
          msgEl.textContent = 'WhatsApp configuration saved successfully.';
          setTimeout(() => { msgEl.style.display = 'none'; }, 4000);
        }
        loadAdminData();
      } else {
        if (msgEl) {
          msgEl.style.display = 'block';
          msgEl.style.background = 'rgba(239, 68, 68, 0.15)';
          msgEl.style.color = '#ef4444';
          msgEl.textContent = 'Error: ' + (data.error || 'Failed to save');
        }
      }
    } catch (e) {
      if (msgEl) {
        msgEl.style.display = 'block';
        msgEl.style.background = 'rgba(239, 68, 68, 0.15)';
        msgEl.style.color = '#ef4444';
        msgEl.textContent = 'Error saving: ' + e;
      }
    }
  }

  async function sendTestWhatsAppNotification() {
    const phone = document.getElementById('whatsapp-phone').value.trim();
    const apiKey = document.getElementById('whatsapp-api-key').value.trim();
    const msgEl = document.getElementById('whatsapp-action-msg');
    if (msgEl) {
      msgEl.style.display = 'block';
      msgEl.style.background = 'rgba(59, 130, 246, 0.15)';
      msgEl.style.color = 'var(--accent-blue)';
      msgEl.textContent = 'Sending test WhatsApp notification via CallMeBot...';
    }

    try {
      const resp = await fetch('/api/settings/notifications/test-whatsapp', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone: phone, api_key: apiKey })
      });
      const data = await resp.json();
      if (data.success) {
        if (msgEl) {
          msgEl.style.background = 'rgba(16, 185, 129, 0.15)';
          msgEl.style.color = 'var(--accent-green)';
          msgEl.textContent = data.message || 'Test notification delivered successfully.';
          setTimeout(() => { msgEl.style.display = 'none'; }, 4000);
        }
      } else {
        if (msgEl) {
          msgEl.style.background = 'rgba(239, 68, 68, 0.15)';
          msgEl.style.color = '#ef4444';
          msgEl.textContent = 'Delivery failed: ' + (data.message || data.error || 'Unknown error');
        }
      }
    } catch (e) {
      if (msgEl) {
        msgEl.style.background = 'rgba(239, 68, 68, 0.15)';
        msgEl.style.color = '#ef4444';
        msgEl.textContent = 'Error: ' + e;
      }
    }
  }

  async function vacuumDatabase() {
    const msgEl = document.getElementById('db-action-msg');
    msgEl.style.display = 'block';
    msgEl.style.background = 'rgba(99,102,241,0.15)';
    msgEl.style.color = '#818cf8';
    msgEl.style.border = '1px solid rgba(99,102,241,0.3)';
    msgEl.textContent = 'Optimizing SQLite database...';

    try {
      const resp = await fetch('/api/admin/database/vacuum', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
      });
      const data = await resp.json();
      if (data.success) {
        msgEl.style.background = 'rgba(16,185,129,0.15)';
        msgEl.style.color = '#34d399';
        msgEl.style.border = '1px solid rgba(16,185,129,0.3)';
        msgEl.textContent = 'Database optimized and defragmented successfully (VACUUM complete).';
        loadAdminData();
      } else {
        msgEl.style.background = 'rgba(239,68,68,0.15)';
        msgEl.style.color = '#f87171';
        msgEl.style.border = '1px solid rgba(239,68,68,0.3)';
        msgEl.textContent = 'Error during optimization: ' + (data.error || 'Unknown');
      }
    } catch (e) {
      msgEl.style.background = 'rgba(239,68,68,0.15)';
      msgEl.style.color = '#f87171';
      msgEl.style.border = '1px solid rgba(239,68,68,0.3)';
      msgEl.textContent = 'Error executing VACUUM: ' + e;
    }
  }

  function createLocalBackup() {
    const msgEl = document.getElementById('db-action-msg');
    msgEl.style.display = 'block';
    msgEl.style.background = 'rgba(16,185,129,0.15)';
    msgEl.style.color = '#34d399';
    msgEl.style.border = '1px solid rgba(16,185,129,0.3)';
    msgEl.textContent = 'Your backup is safely preserved in .data/session_1ag8q26h_backup/ and .data/applypilot.db';
  }
</script>
</body>
</html>"""


def generate_maintenance_html() -> str:
    """Generate the public Maintenance Mode screen HTML (Light mode theme with logo)."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DuckDuckWork - Scheduled Maintenance</title>
<link rel="icon" type="image/x-icon" href="/favicon.ico">
<link rel="icon" type="image/png" sizes="96x96" href="/favicon-96x96.png">
<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32x32.png">
<link rel="icon" type="image/png" sizes="16x16" href="/favicon-16x16.png">
<link rel="icon" type="image/png" href="/favicon.png">
<link rel="shortcut icon" type="image/png" href="/favicon.png">
<link rel="shortcut icon" href="/favicon.ico">
<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@500;700;800&display=swap" rel="stylesheet">
<style>
  body {
    margin: 0; padding: 0;
    background: #f8fafc;
    color: #0f172a;
    font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, sans-serif;
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 100vh;
    text-align: center;
  }
  .maintenance-box {
    max-width: 460px;
    width: 90%;
    padding: 3rem 2rem;
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 24px;
    box-shadow: 0 10px 30px rgba(15, 23, 42, 0.06);
  }
  .brand-logo-maint {
    width: 64px;
    height: 64px;
    border-radius: 50%;
    object-fit: cover;
    margin-bottom: 0.75rem;
  }
  .brand-name-maint {
    font-size: 1.5rem;
    font-weight: 800;
    color: #0f172a;
    margin-bottom: 1.25rem;
    letter-spacing: -0.02em;
  }
  .badge {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    background: rgba(234, 179, 8, 0.12);
    color: #ca8a04;
    border: 1px solid rgba(234, 179, 8, 0.3);
    padding: 0.35rem 0.85rem;
    border-radius: 20px;
    font-size: 0.75rem;
    font-weight: 800;
    letter-spacing: 0.05em;
    margin-bottom: 1.25rem;
    text-transform: uppercase;
  }
  h1 { font-size: 1.5rem; font-weight: 800; margin-bottom: 0.5rem; color: #0f172a; }
  p { font-size: 0.95rem; color: #64748b; line-height: 1.6; margin: 0; }
  .pulse-dot {
    width: 8px; height: 8px; background: #eab308; border-radius: 50%;
    box-shadow: 0 0 10px #eab308; animation: pulse 1.5s infinite;
  }
  @keyframes pulse { 0% { opacity: 0.4; } 50% { opacity: 1; } 100% { opacity: 0.4; } }
</style>
</head>
<body>
  <div class="maintenance-box">
    <img src="/static/images/duckduckwork_logo.png" class="brand-logo-maint" alt="DuckDuckWork">
    <div class="brand-name-maint">DuckDuckWork</div>
    <div class="badge"><span class="pulse-dot"></span> Maintenance Mode Active</div>
    <h1>System Maintenance</h1>
    <p>DuckDuckWork is currently undergoing scheduled maintenance and updates. We will be back online shortly.</p>
  </div>
</body>
</html>"""


def generate_analytics_html(session_id: str | None = None) -> str:
    """Generate the full Analytics Dashboard and Search Tab ROI Evaluator HTML."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="noindex, nofollow, noarchive">
<title>DuckDuckWork - Analytics Dashboard &amp; Tab Health</title>
<link rel="icon" type="image/x-icon" href="/favicon.ico">
<link rel="icon" type="image/png" sizes="96x96" href="/favicon-96x96.png">
<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32x32.png">
<link rel="icon" type="image/png" sizes="16x16" href="/favicon-16x16.png">
<link rel="icon" type="image/png" href="/favicon.png">
<link rel="shortcut icon" type="image/png" href="/favicon.png">
<link rel="shortcut icon" href="/favicon.ico">
<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">
<link rel="mask-icon" href="/static/images/safari-pinned-tab.svg" color="#6b21a8">
<meta name="theme-color" content="#ffffff" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#0f0c1b" media="(prefers-color-scheme: dark)">

<!-- Typography & Leaflet CSS -->
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Plus+Jakarta+Sans:wght@500;600;700;800&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin=""/>

<script>
(function() {
  var mode = localStorage.getItem('applypilot_theme_mode') || 'system';
  var effective = mode;
  if (mode === 'system') {
    effective = (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light';
  }
  document.documentElement.setAttribute('data-theme', effective);
  document.documentElement.setAttribute('data-theme-mode', mode);
})();
</script>

<style>
  :root {
    --bg-main: #0f0c1b;
    --bg-header: #170e26;
    --bg-card: #1d122f;
    --bg-card-hover: #26163e;
    --border-color: #2e1c4a;
    --text-main: #f8fafc;
    --text-muted: #a78bfa;
    --accent-purple: #9333ea;
    --accent-purple-light: #c084fc;
    --accent-yellow: #facc15;
    --accent-green: #10b981;
    --accent-red: #ef4444;
    --accent-amber: #f59e0b;
    --accent-blue: #38bdf8;
    --radius-lg: 14px;
    --radius-md: 8px;
    --card-shadow: 0 4px 20px rgba(0, 0, 0, 0.35);
  }

  html[data-theme="light"] {
    --bg-main: #f6f3fb;
    --bg-header: #ffffff;
    --bg-card: #ffffff;
    --bg-card-hover: #fbf9ff;
    --border-color: #e9d5ff;
    --text-main: #0f172a;
    --text-muted: #64748b;
    --accent-purple: #7e22ce;
    --accent-purple-light: #9333ea;
    --accent-yellow: #b45309;
    --accent-green: #059669;
    --accent-red: #dc2626;
    --accent-amber: #d97706;
    --accent-blue: #0284c7;
    --card-shadow: 0 4px 16px rgba(107, 33, 168, 0.08);
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, sans-serif;
    background-color: var(--bg-main);
    color: var(--text-main);
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    overflow-x: hidden;
  }

  /* Header */
  .analytics-header {
    background: var(--bg-header);
    border-bottom: 1px solid var(--border-color);
    padding: 0.85rem 1.75rem;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    flex-wrap: wrap;
    position: sticky;
    top: 0;
    z-index: 1000;
  }
  .brand-group {
    display: flex;
    align-items: center;
    gap: 0.75rem;
  }
  .brand-logo {
    width: 36px;
    height: 36px;
    border-radius: 50%;
    object-fit: cover;
  }
  .brand-title {
    font-size: 1.2rem;
    font-weight: 800;
    letter-spacing: -0.01em;
  }
  .brand-badge {
    background: rgba(250, 204, 21, 0.15);
    color: var(--accent-yellow);
    border: 1px solid rgba(250, 204, 21, 0.35);
    font-size: 0.68rem;
    font-weight: 800;
    padding: 2px 7px;
    border-radius: 4px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }
  .header-actions {
    display: flex;
    align-items: center;
    gap: 0.65rem;
    flex-wrap: wrap;
  }

  /* Selector styling */
  .search-selector-wrap {
    display: inline-flex;
    align-items: center;
    gap: 0.45rem;
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    padding: 0.35rem 0.65rem;
    border-radius: var(--radius-md);
  }
  .search-selector-label {
    font-size: 0.78rem;
    font-weight: 700;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.03em;
  }
  .search-select {
    background: transparent;
    border: none;
    color: var(--text-main);
    font-family: inherit;
    font-size: 0.88rem;
    font-weight: 600;
    outline: none;
    cursor: pointer;
    min-width: 170px;
  }
  .search-select option {
    background: var(--bg-card);
    color: var(--text-main);
  }

  /* Buttons */
  .btn-outline {
    background: transparent;
    border: 1px solid var(--border-color);
    color: var(--text-main);
    padding: 0.42rem 0.8rem;
    border-radius: var(--radius-md);
    font-size: 0.84rem;
    font-weight: 600;
    cursor: pointer;
    text-decoration: none;
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    transition: all 0.2s ease;
  }
  .btn-outline:hover {
    border-color: var(--accent-purple-light);
    color: var(--accent-purple-light);
  }
  .btn-primary {
    background: var(--accent-purple);
    border: 1px solid var(--accent-purple);
    color: #ffffff;
    padding: 0.42rem 0.85rem;
    border-radius: var(--radius-md);
    font-size: 0.84rem;
    font-weight: 600;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    transition: opacity 0.2s ease;
  }
  .btn-primary:hover {
    opacity: 0.9;
  }

  /* Theme Switcher */
  .theme-segmented-control {
    display: inline-flex;
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: 9999px;
    padding: 2px;
    gap: 2px;
  }
  .theme-segment-btn {
    background: transparent;
    border: none;
    border-radius: 9999px;
    padding: 4px 7px;
    color: var(--text-muted);
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    justify-content: center;
  }
  .theme-segment-btn.active {
    background: var(--border-color);
    color: var(--text-main);
  }

  /* Main Container */
  .analytics-container {
    max-width: 1400px;
    width: 100%;
    margin: 0 auto;
    padding: 1.5rem;
    display: flex;
    flex-direction: column;
    gap: 1.5rem;
  }

  /* Tab Health & ROI Recommendation Banner */
  .tab-health-banner {
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: var(--radius-lg);
    padding: 1.25rem 1.5rem;
    box-shadow: var(--card-shadow);
    display: flex;
    flex-direction: column;
    gap: 1rem;
    transition: border-color 0.3s ease;
  }
  .tab-health-banner.status-keep {
    border-color: rgba(16, 185, 129, 0.45);
    background: linear-gradient(180deg, rgba(16, 185, 129, 0.06) 0%, var(--bg-card) 100%);
  }
  .tab-health-banner.status-refine {
    border-color: rgba(245, 158, 11, 0.45);
    background: linear-gradient(180deg, rgba(245, 158, 11, 0.06) 0%, var(--bg-card) 100%);
  }
  .tab-health-banner.status-discard {
    border-color: rgba(239, 68, 68, 0.45);
    background: linear-gradient(180deg, rgba(239, 68, 68, 0.06) 0%, var(--bg-card) 100%);
  }
  .banner-top-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    flex-wrap: wrap;
  }
  .banner-headline-wrap {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    flex-wrap: wrap;
  }
  .status-pill {
    padding: 4px 10px;
    border-radius: 9999px;
    font-size: 0.78rem;
    font-weight: 700;
    letter-spacing: 0.02em;
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
  }
  .status-pill.pill-keep {
    background: rgba(16, 185, 129, 0.15);
    color: var(--accent-green);
    border: 1px solid rgba(16, 185, 129, 0.35);
  }
  .status-pill.pill-refine {
    background: rgba(245, 158, 11, 0.15);
    color: var(--accent-amber);
    border: 1px solid rgba(245, 158, 11, 0.35);
  }
  .status-pill.pill-discard {
    background: rgba(239, 68, 68, 0.15);
    color: var(--accent-red);
    border: 1px solid rgba(239, 68, 68, 0.35);
  }
  .banner-title {
    font-size: 1.15rem;
    font-weight: 700;
  }
  .banner-metrics-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 0.85rem;
  }
  .health-metric-box {
    background: rgba(0, 0, 0, 0.15);
    border: 1px solid var(--border-color);
    padding: 0.75rem 1rem;
    border-radius: var(--radius-md);
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
  }
  html[data-theme="light"] .health-metric-box {
    background: rgba(107, 33, 168, 0.03);
  }
  .health-metric-label {
    font-size: 0.72rem;
    color: var(--text-muted);
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .health-metric-value {
    font-family: 'JetBrains Mono', monospace;
    font-size: 1.25rem;
    font-weight: 700;
    color: var(--text-main);
  }
  .banner-advice-text {
    font-size: 0.88rem;
    color: var(--text-muted);
    line-height: 1.5;
  }

  /* KPI Summary Cards */
  .kpi-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 1rem;
  }
  .kpi-card {
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: var(--radius-lg);
    padding: 1.2rem;
    box-shadow: var(--card-shadow);
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
    transition: transform 0.2s ease, border-color 0.2s ease;
  }
  .kpi-card:hover {
    border-color: var(--accent-purple-light);
    transform: translateY(-2px);
  }
  .kpi-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .kpi-label {
    font-size: 0.76rem;
    font-weight: 700;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.03em;
  }
  .kpi-icon-wrap {
    width: 28px;
    height: 28px;
    border-radius: 6px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(167, 139, 250, 0.12);
    color: var(--accent-purple-light);
  }
  .kpi-val {
    font-family: 'JetBrains Mono', monospace;
    font-size: 1.85rem;
    font-weight: 700;
    line-height: 1.1;
  }
  .kpi-sub {
    font-size: 0.78rem;
    color: var(--text-muted);
  }

  /* 2-Column Split: Funnel & Map */
  .split-row {
    display: grid;
    grid-template-columns: 1fr 1.35fr;
    gap: 1.25rem;
  }
  @media (max-width: 1024px) {
    .split-row {
      grid-template-columns: 1fr;
    }
  }

  .card-box {
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: var(--radius-lg);
    padding: 1.25rem;
    box-shadow: var(--card-shadow);
    display: flex;
    flex-direction: column;
    gap: 1rem;
  }
  .card-box-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .card-box-title {
    font-size: 1rem;
    font-weight: 700;
    letter-spacing: -0.01em;
  }
  .card-box-sub {
    font-size: 0.78rem;
    color: var(--text-muted);
  }

  /* Funnel Visualization */
  .funnel-container {
    display: flex;
    flex-direction: column;
    gap: 0.85rem;
    margin-top: 0.25rem;
  }
  .funnel-stage {
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
  }
  .funnel-stage-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    font-size: 0.85rem;
    font-weight: 600;
  }
  .funnel-stage-meta {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.82rem;
    color: var(--text-muted);
  }
  .funnel-track {
    width: 100%;
    height: 14px;
    background: rgba(0, 0, 0, 0.2);
    border-radius: 9999px;
    overflow: hidden;
    border: 1px solid var(--border-color);
  }
  html[data-theme="light"] .funnel-track {
    background: rgba(107, 33, 168, 0.06);
  }
  .funnel-fill {
    height: 100%;
    border-radius: 9999px;
    transition: width 0.4s ease;
  }
  .fill-stage-found { background: #38bdf8; width: 100%; }
  .fill-stage-saved { background: #10b981; }
  .fill-stage-applied { background: #a855f7; }
  .fill-stage-archived { background: #64748b; }

  /* Map Container */
  .map-wrap {
    width: 100%;
    height: 380px;
    border-radius: var(--radius-md);
    overflow: hidden;
    position: relative;
    border: 1px solid var(--border-color);
  }
  #analytics-map {
    width: 100%;
    height: 100%;
    background: #170e26;
  }
  .map-overlay-badge {
    position: absolute;
    bottom: 10px;
    left: 10px;
    background: rgba(15, 12, 27, 0.88);
    backdrop-filter: blur(6px);
    border: 1px solid var(--border-color);
    padding: 6px 12px;
    border-radius: 6px;
    font-size: 0.78rem;
    font-weight: 600;
    color: var(--text-main);
    z-index: 500;
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
  }
  html[data-theme="light"] .map-overlay-badge {
    background: rgba(255, 255, 255, 0.92);
    color: #0f172a;
  }

  /* 3-Column Rankings */
  .rankings-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(310px, 1fr));
    gap: 1.25rem;
  }
  .ranking-list {
    display: flex;
    flex-direction: column;
    gap: 0.65rem;
  }
  .ranking-item {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
  }
  .ranking-item-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
    font-size: 0.84rem;
  }
  .ranking-name-wrap {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    min-width: 0;
  }
  .ranking-badge-num {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.72rem;
    font-weight: 700;
    color: var(--text-muted);
    min-width: 22px;
  }
  .ranking-name {
    font-weight: 600;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .ranking-count-pill {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.76rem;
    font-weight: 700;
    padding: 1px 7px;
    border-radius: 9999px;
    background: rgba(167, 139, 250, 0.15);
    color: var(--accent-purple-light);
    flex-shrink: 0;
  }
  .ranking-track {
    width: 100%;
    height: 6px;
    background: rgba(0, 0, 0, 0.18);
    border-radius: 3px;
    overflow: hidden;
  }
  html[data-theme="light"] .ranking-track {
    background: rgba(107, 33, 168, 0.05);
  }
  .ranking-fill {
    height: 100%;
    background: var(--accent-purple);
    border-radius: 3px;
    transition: width 0.3s ease;
  }

  /* Score Distribution */
  .score-bars-wrap {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }
  .score-bar-row {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
  }
  .score-row-meta {
    display: flex;
    align-items: center;
    justify-content: space-between;
    font-size: 0.82rem;
    font-weight: 600;
  }
  .score-track {
    width: 100%;
    height: 10px;
    background: rgba(0, 0, 0, 0.18);
    border-radius: 9999px;
    overflow: hidden;
  }
  html[data-theme="light"] .score-track {
    background: rgba(107, 33, 168, 0.05);
  }
  .score-fill {
    height: 100%;
    border-radius: 9999px;
    transition: width 0.4s ease;
  }

  /* Empty state */
  .empty-state-notice {
    padding: 2rem 1rem;
    text-align: center;
    color: var(--text-muted);
    font-size: 0.9rem;
  }
</style>
</head>
<body>

  <!-- Top Navigation Header -->
  <header class="analytics-header">
    <div class="brand-group">
      <img src="/static/images/duckduckwork_logo.png" alt="DuckDuckWork" class="brand-logo">
      <span class="brand-title">DuckDuckWork</span>
      <span class="brand-badge">ANALYTICS &amp; ROI</span>
    </div>

    <!-- Active Search Filter Dropdown -->
    <div class="search-selector-wrap">
      <label for="analytics-search-select" class="search-selector-label">Search Tab:</label>
      <select id="analytics-search-select" class="search-select" onchange="onSearchTabChange(this.value)">
        <option value="">All Searches (Aggregated)</option>
      </select>
    </div>

    <div class="header-actions">
      <!-- Theme Switcher -->
      <div class="theme-segmented-control" id="theme-segmented-control-analytics">
        <button type="button" class="theme-segment-btn" data-theme-btn="system" onclick="setAnalyticsTheme('system')" title="System Theme">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2" ry="2"></rect><line x1="8" y1="21" x2="16" y2="21"></line><line x1="12" y1="17" x2="12" y2="21"></line></svg>
        </button>
        <button type="button" class="theme-segment-btn" data-theme-btn="light" onclick="setAnalyticsTheme('light')" title="Light Mode">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"></circle><line x1="12" y1="1" x2="12" y2="3"></line><line x1="12" y1="21" x2="12" y2="23"></line><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"></line><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"></line><line x1="1" y1="12" x2="3" y2="12"></line><line x1="21" y1="12" x2="23" y2="12"></line><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"></line><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"></line></svg>
        </button>
        <button type="button" class="theme-segment-btn" data-theme-btn="dark" onclick="setAnalyticsTheme('dark')" title="Dark Mode">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path></svg>
        </button>
      </div>

      <button class="btn-outline" onclick="loadAnalyticsData()" title="Reload metrics">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"></polyline><polyline points="1 20 1 14 7 14"></polyline><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path></svg>
        Refresh
      </button>

      <a href="/admin" class="btn-outline">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path></svg>
        Settings
      </a>

      <a href="/" class="btn-primary" style="text-decoration:none;">
        &larr; Back to Searches
      </a>
    </div>
  </header>

  <!-- Main View -->
  <main class="analytics-container">

    <!-- 1. Tab Health & ROI Recommendation Banner -->
    <section class="tab-health-banner status-keep" id="tab-health-banner">
      <div class="banner-top-row">
        <div class="banner-headline-wrap">
          <span class="status-pill pill-keep" id="banner-status-pill">Status: High Yield — Recommended to Keep</span>
          <h2 class="banner-title" id="banner-search-name">All Searches Evaluation</h2>
        </div>
        <div style="font-size:0.8rem; color:var(--text-muted); font-weight:600;" id="banner-timestamp">Live Aggregation</div>
      </div>

      <div class="banner-metrics-grid">
        <div class="health-metric-box">
          <span class="health-metric-label">Total Volume</span>
          <span class="health-metric-value" id="health-total-jobs">0</span>
        </div>
        <div class="health-metric-box">
          <span class="health-metric-label">Average Fit Score</span>
          <span class="health-metric-value" id="health-avg-score">0.0</span>
        </div>
        <div class="health-metric-box">
          <span class="health-metric-label">Saved Conversion</span>
          <span class="health-metric-value" id="health-saved-rate">0.0%</span>
        </div>
        <div class="health-metric-box">
          <span class="health-metric-label">Application Rate</span>
          <span class="health-metric-value" id="health-applied-rate">0.0%</span>
        </div>
      </div>

      <p class="banner-advice-text" id="banner-advice-text">
        Evaluating search tab health and ROI. Select a specific search tab to view focused recommendations and actionable yield analysis.
      </p>
    </section>

    <!-- 2. KPI Summary Cards -->
    <section class="kpi-grid">
      <div class="kpi-card">
        <div class="kpi-header">
          <span class="kpi-label">Total Found</span>
          <div class="kpi-icon-wrap">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
          </div>
        </div>
        <div class="kpi-val" id="kpi-total-found">0</div>
        <div class="kpi-sub">Listings scraped &amp; indexed</div>
      </div>

      <div class="kpi-card">
        <div class="kpi-header">
          <span class="kpi-label">Saved Listings</span>
          <div class="kpi-icon-wrap" style="color:var(--accent-green); background:rgba(16,185,129,0.12);">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"></path></svg>
          </div>
        </div>
        <div class="kpi-val" style="color:var(--accent-green);" id="kpi-saved">0</div>
        <div class="kpi-sub" id="kpi-saved-sub">0% of total found</div>
      </div>

      <div class="kpi-card">
        <div class="kpi-header">
          <span class="kpi-label">Applications Sent</span>
          <div class="kpi-icon-wrap" style="color:var(--accent-purple-light); background:rgba(168,85,247,0.12);">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2L11 13"></path><polygon points="22 2 15 22 11 13 2 9 22 2"></polygon></svg>
          </div>
        </div>
        <div class="kpi-val" style="color:var(--accent-purple-light);" id="kpi-applied">0</div>
        <div class="kpi-sub" id="kpi-applied-sub">0% of saved listings</div>
      </div>

      <div class="kpi-card">
        <div class="kpi-header">
          <span class="kpi-label">Archived Listings</span>
          <div class="kpi-icon-wrap" style="color:var(--text-muted); background:rgba(148,163,184,0.12);">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="21 8 21 21 3 21 3 8"></polyline><rect x="1" y="3" width="22" height="5"></rect><line x1="10" y1="12" x2="14" y2="12"></line></svg>
          </div>
        </div>
        <div class="kpi-val" style="color:var(--text-muted);" id="kpi-archived">0</div>
        <div class="kpi-sub" id="kpi-archived-sub">Discarded or closed</div>
      </div>

      <div class="kpi-card">
        <div class="kpi-header">
          <span class="kpi-label">Pending Review</span>
          <div class="kpi-icon-wrap" style="color:var(--accent-blue); background:rgba(56,189,248,0.12);">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>
          </div>
        </div>
        <div class="kpi-val" style="color:var(--accent-blue);" id="kpi-pending">0</div>
        <div class="kpi-sub">Awaiting candidate action</div>
      </div>

      <div class="kpi-card">
        <div class="kpi-header">
          <span class="kpi-label">Average Fit Score</span>
          <div class="kpi-icon-wrap" style="color:var(--accent-yellow); background:rgba(250,204,21,0.12);">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon></svg>
          </div>
        </div>
        <div class="kpi-val" style="color:var(--accent-yellow);" id="kpi-avg-score">0.0</div>
        <div class="kpi-sub" id="kpi-avg-score-sub">AI evaluation rating</div>
      </div>
    </section>

    <!-- 3. Split Row: Conversion Funnel & Leaflet Map -->
    <section class="split-row">

      <!-- Conversion Funnel Card -->
      <div class="card-box">
        <div class="card-box-header">
          <div>
            <h3 class="card-box-title">Conversion Funnel</h3>
            <span class="card-box-sub">Listing progression from discovery to application</span>
          </div>
        </div>

        <div class="funnel-container">
          <div class="funnel-stage">
            <div class="funnel-stage-header">
              <span>1. Found Listings</span>
              <span class="funnel-stage-meta"><span id="funnel-found-count">0</span> (100%)</span>
            </div>
            <div class="funnel-track">
              <div class="funnel-fill fill-stage-found" id="funnel-found-bar" style="width: 100%;"></div>
            </div>
          </div>

          <div class="funnel-stage">
            <div class="funnel-stage-header">
              <span>2. Saved / Shortlisted</span>
              <span class="funnel-stage-meta"><span id="funnel-saved-count">0</span> (<span id="funnel-saved-pct">0.0%</span>)</span>
            </div>
            <div class="funnel-track">
              <div class="funnel-fill fill-stage-saved" id="funnel-saved-bar" style="width: 0%;"></div>
            </div>
          </div>

          <div class="funnel-stage">
            <div class="funnel-stage-header">
              <span>3. Applications Submitted</span>
              <span class="funnel-stage-meta"><span id="funnel-applied-count">0</span> (<span id="funnel-applied-pct">0.0%</span>)</span>
            </div>
            <div class="funnel-track">
              <div class="funnel-fill fill-stage-applied" id="funnel-applied-bar" style="width: 0%;"></div>
            </div>
          </div>

          <div class="funnel-stage">
            <div class="funnel-stage-header">
              <span>4. Archived Listings</span>
              <span class="funnel-stage-meta"><span id="funnel-archived-count">0</span> (<span id="funnel-archived-pct">0.0%</span>)</span>
            </div>
            <div class="funnel-track">
              <div class="funnel-fill fill-stage-archived" id="funnel-archived-bar" style="width: 0%;"></div>
            </div>
          </div>
        </div>

        <!-- Fit Score Distribution -->
        <div style="margin-top:1rem; border-top:1px solid var(--border-color); padding-top:1rem;">
          <h4 style="font-size:0.9rem; font-weight:700; margin-bottom:0.75rem;">AI Fit Score Distribution</h4>
          <div class="score-bars-wrap">
            <div class="score-bar-row">
              <div class="score-row-meta">
                <span>90%+ (Exceptional Fit)</span>
                <span id="score-90-meta" style="font-family:'JetBrains Mono',monospace;">0 (0%)</span>
              </div>
              <div class="score-track">
                <div class="score-fill" id="score-90-bar" style="width:0%; background:#10b981;"></div>
              </div>
            </div>

            <div class="score-bar-row">
              <div class="score-row-meta">
                <span>80-89% (Strong Fit)</span>
                <span id="score-80-meta" style="font-family:'JetBrains Mono',monospace;">0 (0%)</span>
              </div>
              <div class="score-track">
                <div class="score-fill" id="score-80-bar" style="width:0%; background:#38bdf8;"></div>
              </div>
            </div>

            <div class="score-bar-row">
              <div class="score-row-meta">
                <span>70-79% (Moderate Fit)</span>
                <span id="score-70-meta" style="font-family:'JetBrains Mono',monospace;">0 (0%)</span>
              </div>
              <div class="score-track">
                <div class="score-fill" id="score-70-bar" style="width:0%; background:#facc15;"></div>
              </div>
            </div>

            <div class="score-bar-row">
              <div class="score-row-meta">
                <span>&lt;70% (Low Match)</span>
                <span id="score-low-meta" style="font-family:'JetBrains Mono',monospace;">0 (0%)</span>
              </div>
              <div class="score-track">
                <div class="score-fill" id="score-low-bar" style="width:0%; background:#ef4444;"></div>
              </div>
            </div>

            <div class="score-bar-row">
              <div class="score-row-meta">
                <span>Unscored / Pending</span>
                <span id="score-unscored-meta" style="font-family:'JetBrains Mono',monospace;">0 (0%)</span>
              </div>
              <div class="score-track">
                <div class="score-fill" id="score-unscored-bar" style="width:0%; background:#64748b;"></div>
              </div>
            </div>
          </div>
        </div>

      </div>

      <!-- Leaflet Geographic Map Card -->
      <div class="card-box">
        <div class="card-box-header">
          <div>
            <h3 class="card-box-title">Geographic Density Map</h3>
            <span class="card-box-sub">Geographic distribution across active target markets and remote roles</span>
          </div>
          <span style="font-size:0.75rem; font-weight:700; color:var(--text-muted); text-transform:uppercase;" id="map-loc-count">0 Locations</span>
        </div>

        <div class="map-wrap">
          <div id="analytics-map"></div>
          <div class="map-overlay-badge" id="map-remote-badge">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"></circle><line x1="2" y1="12" x2="22" y2="12"></line><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path></svg>
            <span id="map-remote-text">Remote / Worldwide: 0 listings</span>
          </div>
        </div>
      </div>

    </section>

    <!-- 4. Rankings Cards Grid: Portals, Companies, Job Titles -->
    <section class="rankings-grid">

      <!-- Top Portals / Boards -->
      <div class="card-box">
        <div class="card-box-header">
          <div>
            <h3 class="card-box-title">Top 10 Job Portals</h3>
            <span class="card-box-sub">Distribution by source board</span>
          </div>
        </div>
        <div class="ranking-list" id="rankings-portals-list">
          <div class="empty-state-notice">No portal data available</div>
        </div>
      </div>

      <!-- Top Companies -->
      <div class="card-box">
        <div class="card-box-header">
          <div>
            <h3 class="card-box-title">Top 10 Hiring Companies</h3>
            <span class="card-box-sub">Organizations with the most openings</span>
          </div>
        </div>
        <div class="ranking-list" id="rankings-companies-list">
          <div class="empty-state-notice">No company data available</div>
        </div>
      </div>

      <!-- Top Job Titles -->
      <div class="card-box">
        <div class="card-box-header">
          <div>
            <h3 class="card-box-title">Top 10 Job Titles</h3>
            <span class="card-box-sub">Most frequent role classifications</span>
          </div>
        </div>
        <div class="ranking-list" id="rankings-titles-list">
          <div class="empty-state-notice">No job title data available</div>
        </div>
      </div>

    </section>

  </main>

<!-- Leaflet JS -->
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>

<script>
  // Global Analytics State
  var analyticsState = {
    searchId: '',
    data: null,
    leafletMap: null,
    markersLayer: null
  };

  // Theme Management
  function setAnalyticsTheme(mode) {
    localStorage.setItem('applypilot_theme_mode', mode);
    var effective = mode;
    if (mode === 'system') {
      effective = (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light';
    }
    document.documentElement.setAttribute('data-theme', effective);
    document.documentElement.setAttribute('data-theme-mode', mode);

    var btns = document.querySelectorAll('#theme-segmented-control-analytics .theme-segment-btn');
    btns.forEach(function(b) {
      if (b.getAttribute('data-theme-btn') === mode) {
        b.classList.add('active');
      } else {
        b.classList.remove('active');
      }
    });

    if (analyticsState.leafletMap) {
      setTimeout(function() {
        analyticsState.leafletMap.invalidateSize();
      }, 200);
    }
  }

  // Initialize Map
  function initAnalyticsMap() {
    var mapEl = document.getElementById('analytics-map');
    if (!mapEl || typeof L === 'undefined') return;

    if (!analyticsState.leafletMap) {
      // Center default on Central America (Guatemala & Nicaragua region)
      var map = L.map('analytics-map', {
        scrollWheelZoom: false,
        zoomControl: true
      }).setView([14.6349, -90.5069], 4);

      L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors'
      }).addTo(map);

      analyticsState.leafletMap = map;
      analyticsState.markersLayer = L.layerGroup().addTo(map);
    }
  }

  // Update Map Markers from Geolocations
  function updateMapMarkers(geolocations) {
    if (!analyticsState.leafletMap || !analyticsState.markersLayer) return;

    analyticsState.markersLayer.clearLayers();

    var validBounds = [];
    var remoteCount = 0;
    var totalUniqueLocations = 0;

    if (Array.isArray(geolocations)) {
      geolocations.forEach(function(item) {
        totalUniqueLocations++;
        var locLower = (item.location || '').toLowerCase();
        if (locLower.includes('remote') || locLower.includes('worldwide') || locLower.includes('global') || locLower.includes('anywhere')) {
          remoteCount += (item.count || 0);
        }

        if (Array.isArray(item.coordinates) && item.coordinates.length === 2) {
          var lat = item.coordinates[0];
          var lon = item.coordinates[1];
          if (typeof lat === 'number' && typeof lon === 'number') {
            validBounds.push([lat, lon]);

            var radius = Math.min(22, Math.max(7, Math.sqrt(item.count || 1) * 6));
            var marker = L.circleMarker([lat, lon], {
              radius: radius,
              fillColor: '#9333ea',
              color: '#c084fc',
              weight: 2,
              opacity: 0.9,
              fillOpacity: 0.65
            });

            var saved = item.saved || (item.status_breakdown ? item.status_breakdown.saved : 0) || 0;
            var applied = item.applied || (item.status_breakdown ? item.status_breakdown.applied : 0) || 0;

            var popupContent = '<div style="font-family:Plus Jakarta Sans, sans-serif; min-width:140px; padding:2px;">' +
              '<div style="font-weight:700; font-size:0.9rem; margin-bottom:4px; color:#1e1b4b;">' + escapeHtml(item.location || 'Location') + '</div>' +
              '<div style="font-size:0.78rem; color:#475569; display:flex; flex-direction:column; gap:2px;">' +
                '<span><strong>' + (item.count || 0) + '</strong> Total Listings</span>' +
                '<span><strong style="color:#059669;">' + saved + '</strong> Saved</span>' +
                '<span><strong style="color:#7e22ce;">' + applied + '</strong> Applied</span>' +
              '</div>' +
            '</div>';

            marker.bindPopup(popupContent);
            analyticsState.markersLayer.addLayer(marker);
          }
        }
      });
    }

    var mapCountEl = document.getElementById('map-loc-count');
    if (mapCountEl) {
      mapCountEl.textContent = totalUniqueLocations + ' Locations';
    }

    var remoteTextEl = document.getElementById('map-remote-text');
    if (remoteTextEl) {
      remoteTextEl.textContent = 'Remote / Worldwide: ' + remoteCount + ' listings';
    }

    if (validBounds.length > 0) {
      try {
        var bounds = L.latLngBounds(validBounds);
        analyticsState.leafletMap.fitBounds(bounds, { padding: [35, 35], maxZoom: 8 });
      } catch (e) {
        analyticsState.leafletMap.setView([14.6349, -90.5069], 4);
      }
    } else {
      analyticsState.leafletMap.setView([14.6349, -90.5069], 4);
    }
  }

  // Populate Dropdown from tabs_health
  function populateSearchTabsDropdown(tabsHealth) {
    var selectEl = document.getElementById('analytics-search-select');
    if (!selectEl) return;

    var curVal = analyticsState.searchId;
    var optsHtml = '<option value="">All Searches (Aggregated)</option>';

    if (Array.isArray(tabsHealth)) {
      tabsHealth.forEach(function(tab) {
        var sid = tab.search_id || '';
        var sname = tab.search_name || sid;
        var selected = (sid === curVal) ? ' selected' : '';
        optsHtml += '<option value="' + escapeHtml(sid) + '"' + selected + '>' + escapeHtml(sname) + ' (' + (tab.total_jobs || 0) + ')</option>';
      });
    }

    selectEl.innerHTML = optsHtml;
  }

  // Helper: Escape HTML
  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  // Update UI with Analytics Data
  function renderAnalyticsView(data) {
    if (!data) return;
    analyticsState.data = data;

    var total = data.total_found || (data.summary ? data.summary.total_found : 0) || 0;
    var saved = data.saved || (data.summary ? data.summary.saved : 0) || 0;
    var applied = data.applied || (data.summary ? data.summary.applied : 0) || 0;
    var archived = data.archived || (data.summary ? data.summary.archived : 0) || 0;
    var pending = data.pending || (data.summary ? data.summary.pending : 0) || 0;

    var scoreDist = data.score_distribution || {};
    var avgScore = (typeof scoreDist.avg_score === 'number') ? scoreDist.avg_score : 0.0;
    var buckets = scoreDist.score_buckets || {};

    // 1. KPI Cards
    document.getElementById('kpi-total-found').textContent = total.toLocaleString();
    document.getElementById('kpi-saved').textContent = saved.toLocaleString();
    document.getElementById('kpi-applied').textContent = applied.toLocaleString();
    document.getElementById('kpi-archived').textContent = archived.toLocaleString();
    document.getElementById('kpi-pending').textContent = pending.toLocaleString();
    document.getElementById('kpi-avg-score').textContent = avgScore.toFixed(1);

    var savedPct = total > 0 ? ((saved / total) * 100).toFixed(1) : '0.0';
    var appliedPct = saved > 0 ? ((applied / saved) * 100).toFixed(1) : (total > 0 ? ((applied / total) * 100).toFixed(1) : '0.0');
    var archivedPct = total > 0 ? ((archived / total) * 100).toFixed(1) : '0.0';

    document.getElementById('kpi-saved-sub').textContent = savedPct + '% of total found';
    document.getElementById('kpi-applied-sub').textContent = appliedPct + '% conversion';
    document.getElementById('kpi-archived-sub').textContent = archivedPct + '% of total found';

    // 2. Banner: Tab Health & ROI Evaluation
    renderTabHealthBanner(data);

    // 3. Conversion Funnel
    document.getElementById('funnel-found-count').textContent = total.toLocaleString();
    document.getElementById('funnel-saved-count').textContent = saved.toLocaleString();
    document.getElementById('funnel-saved-pct').textContent = savedPct + '%';
    document.getElementById('funnel-applied-count').textContent = applied.toLocaleString();
    document.getElementById('funnel-applied-pct').textContent = (total > 0 ? ((applied / total) * 100).toFixed(1) : '0.0') + '%';
    document.getElementById('funnel-archived-count').textContent = archived.toLocaleString();
    document.getElementById('funnel-archived-pct').textContent = archivedPct + '%';

    document.getElementById('funnel-saved-bar').style.width = Math.min(100, (total > 0 ? (saved / total) * 100 : 0)) + '%';
    document.getElementById('funnel-applied-bar').style.width = Math.min(100, (total > 0 ? (applied / total) * 100 : 0)) + '%';
    document.getElementById('funnel-archived-bar').style.width = Math.min(100, (total > 0 ? (archived / total) * 100 : 0)) + '%';

    // 4. Score Distribution Bars
    var b90 = buckets['90+'] || 0;
    var b80 = buckets['80-89'] || 0;
    var b70 = buckets['70-79'] || 0;
    var bLow = buckets['<70'] || 0;
    var bUn = buckets['unscored'] || 0;
    var scoredTotal = b90 + b80 + b70 + bLow + bUn;

    function renderScoreBar(count, idMeta, idBar) {
      var pct = scoredTotal > 0 ? ((count / scoredTotal) * 100).toFixed(1) : '0.0';
      document.getElementById(idMeta).textContent = count + ' (' + pct + '%)';
      document.getElementById(idBar).style.width = Math.min(100, parseFloat(pct)) + '%';
    }

    renderScoreBar(b90, 'score-90-meta', 'score-90-bar');
    renderScoreBar(b80, 'score-80-meta', 'score-80-bar');
    renderScoreBar(b70, 'score-70-meta', 'score-70-bar');
    renderScoreBar(bLow, 'score-low-meta', 'score-low-bar');
    renderScoreBar(bUn, 'score-unscored-meta', 'score-unscored-bar');

    // 5. Leaflet Map
    updateMapMarkers(data.geolocations || []);

    // 6. Rankings Lists
    renderRankingsList('rankings-portals-list', data.top_sites || data.sites || []);
    renderRankingsList('rankings-companies-list', data.top_companies || data.companies || []);
    renderRankingsList('rankings-titles-list', data.top_titles || data.titles || []);
  }

  // Render Tab Health Banner
  function renderTabHealthBanner(data) {
    var bannerEl = document.getElementById('tab-health-banner');
    var statusPill = document.getElementById('banner-status-pill');
    var nameEl = document.getElementById('banner-search-name');
    var adviceEl = document.getElementById('banner-advice-text');

    var currentSid = analyticsState.searchId;
    var tabsHealth = data.tabs_health || [];
    var activeTab = null;

    if (currentSid) {
      for (var i = 0; i < tabsHealth.length; i++) {
        if (tabsHealth[i].search_id === currentSid) {
          activeTab = tabsHealth[i];
          break;
        }
      }
    }

    var total = activeTab ? activeTab.total_jobs : (data.total_found || 0);
    var avgScore = activeTab ? activeTab.avg_score : ((data.score_distribution && data.score_distribution.avg_score) || 0.0);
    var savedRate = activeTab ? activeTab.saved_rate : (total > 0 ? ((data.saved / total) * 100) : 0.0);
    var appliedRate = activeTab ? activeTab.applied_rate : (total > 0 ? ((data.applied / total) * 100) : 0.0);
    var rec = activeTab ? activeTab.recommendation : 'Keep Searching';

    document.getElementById('health-total-jobs').textContent = total;
    document.getElementById('health-avg-score').textContent = avgScore.toFixed(1);
    document.getElementById('health-saved-rate').textContent = savedRate.toFixed(1) + '%';
    document.getElementById('health-applied-rate').textContent = appliedRate.toFixed(1) + '%';

    bannerEl.className = 'tab-health-banner';
    statusPill.className = 'status-pill';

    if (activeTab) {
      nameEl.textContent = activeTab.search_name + ' ROI Evaluation';
    } else {
      nameEl.textContent = 'All Searches Aggregated Evaluation';
    }

    if (rec === 'Discard / Pause') {
      bannerEl.classList.add('status-discard');
      statusPill.classList.add('pill-discard');
      statusPill.textContent = 'Status: Low Engagement — Recommended to Discard or Pause';
      adviceEl.textContent = 'Over 30 listings have been indexed with 0 candidate interactions (0 saved or applied). Discarding or pausing this search will prevent database bloat and conserve scraper compute.';
    } else if (rec === 'Refine Query') {
      bannerEl.classList.add('status-refine');
      statusPill.classList.add('pill-refine');
      statusPill.textContent = 'Status: Low Fit — Recommended to Refine Query';
      adviceEl.textContent = 'Candidate match scores for this query average under 30% or have yielded minimal engagement. We recommend tightening keywords, job titles, or experience levels in Search Settings.';
    } else {
      bannerEl.classList.add('status-keep');
      statusPill.classList.add('pill-keep');
      statusPill.textContent = 'Status: High Yield — Recommended to Keep Active';
      adviceEl.textContent = 'This search profile demonstrates healthy candidate interaction, strong AI fit scores, or positive application conversion. Recommended to keep running automatic scans.';
    }
  }

  // Render Ranking List items
  function renderRankingsList(containerId, items) {
    var container = document.getElementById(containerId);
    if (!container) return;

    if (!Array.isArray(items) || items.length === 0) {
      container.innerHTML = '<div class="empty-state-notice">No data recorded yet</div>';
      return;
    }

    var maxCount = 1;
    items.forEach(function(it) {
      if ((it.count || 0) > maxCount) maxCount = it.count;
    });

    var html = '';
    items.slice(0, 10).forEach(function(it, idx) {
      var count = it.count || 0;
      var name = it.name || it.site || it.company || it.title || 'Unknown';
      var pct = maxCount > 0 ? ((count / maxCount) * 100).toFixed(0) : '0';

      html += '<div class="ranking-item">' +
        '<div class="ranking-item-row">' +
          '<div class="ranking-name-wrap">' +
            '<span class="ranking-badge-num">#' + (idx + 1) + '</span>' +
            '<span class="ranking-name" title="' + escapeHtml(name) + '">' + escapeHtml(name) + '</span>' +
          '</div>' +
          '<span class="ranking-count-pill">' + count + '</span>' +
        '</div>' +
        '<div class="ranking-track">' +
          '<div class="ranking-fill" style="width:' + pct + '%;"></div>' +
        '</div>' +
      '</div>';
    });

    container.innerHTML = html;
  }

  // Handle Search Tab Change
  function onSearchTabChange(newSid) {
    analyticsState.searchId = newSid || '';
    if (window.history && window.history.replaceState) {
      var newUrl = window.location.pathname + (analyticsState.searchId ? '?search_id=' + encodeURIComponent(analyticsState.searchId) : '');
      window.history.replaceState(null, '', newUrl);
    }
    loadAnalyticsData();
  }

  // Fetch Analytics from API
  async function loadAnalyticsData() {
    var searchParam = analyticsState.searchId ? '?search_id=' + encodeURIComponent(analyticsState.searchId) : '';
    var pathParts = window.location.pathname.split('/').filter(Boolean);
    var apiPrefix = '';
    if (pathParts[0] === 's' && pathParts[1]) {
      apiPrefix = '/s/' + pathParts[1];
    }
    var apiUrl = apiPrefix + '/api/analytics' + (searchParam ? searchParam + '&' : '?') + '_t=' + Date.now();

    try {
      var resp = await fetch(apiUrl, { cache: 'no-store' });
      if (!resp.ok) {
        throw new Error('Server returned HTTP ' + resp.status);
      }
      var data = await resp.json();

      populateSearchTabsDropdown(data.tabs_health || []);
      renderAnalyticsView(data);

      if (apiPrefix) {
        var backBtn = document.querySelector('.header-actions a.btn-primary');
        if (backBtn) backBtn.href = apiPrefix;
        var settingsBtn = document.querySelector('.header-actions a[href="/admin"]');
        if (settingsBtn) settingsBtn.href = apiPrefix + '/admin';
      }

      var tsEl = document.getElementById('banner-timestamp');
      if (tsEl) {
        var now = new Date();
        tsEl.textContent = 'Updated ' + now.toLocaleTimeString();
      }
    } catch (err) {
      console.warn('Failed to load analytics metrics:', err);
    }
  }

  // Init on DOM Content Loaded
  document.addEventListener('DOMContentLoaded', function() {
    var mode = localStorage.getItem('applypilot_theme_mode') || 'system';
    setAnalyticsTheme(mode);

    var initialSearchId = new URLSearchParams(window.location.search).get('search_id');
    if (initialSearchId) {
      analyticsState.searchId = initialSearchId;
    }

    initAnalyticsMap();
    loadAnalyticsData();
  });
</script>
</body>
</html>"""
