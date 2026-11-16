"""High-Match LinkedIn Outreach & Contact Strategy Generator for DuckDuckWork.

Generates decision-maker search links and tailored outreach messages for
roles with high fit scores (e.g. >= 80%). Produces 3 targeted message
variations adhering to Voice DNA, including a strictly bounded (<300 chars)
connection request note.
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from typing import Any

from applypilot.scoring.validator import BANNED_WORDS, sanitize_text

log = logging.getLogger(__name__)


def get_contact_archetypes(company: str, title: str) -> list[dict[str, str]]:
    """Determine the top 3 decision-maker archetypes and generate LinkedIn search URLs."""
    company_clean = (company or "").strip()
    title_lower = (title or "").lower()

    if any(k in title_lower for k in ("product", "po", "pm", "owner")):
        archetypes = [
            {"role": "Hiring Manager", "title": "Head of Product / VP Product", "query": "Head of Product OR VP Product"},
            {"role": "Talent Acquisition", "title": "Product Recruiter / Talent Lead", "query": "Product Recruiter OR Talent Acquisition"},
            {"role": "Peer Connection", "title": "Senior / Lead Product Manager", "query": "Senior Product Manager OR Lead Product Manager"},
        ]
    elif any(k in title_lower for k in ("data", "analytics", "bi", "machine learning")):
        archetypes = [
            {"role": "Hiring Manager", "title": "Head of Data / Director of Analytics", "query": "Head of Data OR Director of Analytics"},
            {"role": "Talent Acquisition", "title": "Technical Recruiter (Data)", "query": "Technical Recruiter OR Talent Acquisition"},
            {"role": "Peer Connection", "title": "Staff / Senior Data Engineer", "query": "Senior Data Engineer OR Staff Data Engineer"},
        ]
    elif any(k in title_lower for k in ("scrum", "agile", "project", "program")):
        archetypes = [
            {"role": "Hiring Manager", "title": "Director of PMO / Delivery Lead", "query": "Director of PMO OR Agile Delivery Lead"},
            {"role": "Talent Acquisition", "title": "Technical Recruiter", "query": "Technical Recruiter OR Talent Acquisition"},
            {"role": "Peer Connection", "title": "Agile Coach / Scrum Master", "query": "Scrum Master OR Agile Coach"},
        ]
    else:  # Software Engineering / DevOps / Default
        archetypes = [
            {"role": "Hiring Manager", "title": "Engineering Manager / Director", "query": "Engineering Manager OR Director of Engineering"},
            {"role": "Talent Acquisition", "title": "Technical Recruiter / Talent Lead", "query": "Technical Recruiter OR Talent Acquisition"},
            {"role": "Peer Connection", "title": "Staff / Senior Engineer", "query": "Senior Engineer OR Staff Engineer"},
        ]

    results: list[dict[str, str]] = []
    for arch in archetypes:
        combined_q = f'"{company_clean}" ({arch["query"]})'
        encoded = urllib.parse.quote(combined_q)
        search_url = f"https://www.linkedin.com/search/results/people/?keywords={encoded}"
        results.append({
            "archetype_role": arch["role"],
            "title_label": arch["title"],
            "query": combined_q,
            "search_url": search_url,
        })

    return results


def build_template_messages(company: str, title: str, candidate_name: str, key_skill: str) -> dict[str, str]:
    """Generate deterministic high-quality outreach messages adhering to Voice DNA."""
    c_name = candidate_name.split()[0] if candidate_name else "Candidate"
    comp = company or "your team"
    role = title or "this role"
    skill = key_skill or "relevant technologies"

    # Variation 1: Connection Request (MUST BE <= 300 CHARACTERS)
    connection_note = f"Hi [Name], I noticed the {role} position at {comp}. I have a track record building with {skill} and solving similar engineering challenges. Would love to connect and follow {comp}'s journey. Best, {c_name}."
    if len(connection_note) > 300:
        connection_note = f"Hi [Name], saw the {role} role at {comp}. Experienced with {skill} and scaling systems. Would love to connect! Best, {c_name}."
    if len(connection_note) > 300:
        connection_note = connection_note[:297] + "..."

    # Variation 2: InMail / Recruiter Direct Pitch (Under 140 words, direct, outcome-oriented)
    recruiter_pitch = f"""Hi [Name],

I saw {comp} is actively hiring for a {role}.

My background focuses on {skill} with extensive experience delivering production systems. I've spent years helping teams streamline execution, reduce technical overhead, and accelerate roadmap delivery.

Given what {comp} is building, I believe my hands-on background would be an immediate fit. If this requisition is still open, I'd welcome the chance to share my background and discuss how I can help.

Best regards,
{candidate_name}"""

    # Variation 3: Peer-to-Peer Engineering Inquiry (Technical & human)
    peer_inquiry = f"""Hey [Name],

Came across your profile while looking at {comp}'s engineering openings. I see you're working on the team that's hiring for a {role}.

I work extensively with {skill} and am really interested in how {comp} tackles architecture and team velocity. Are you enjoying the engineering culture and day-to-day challenges there?

Would love to hear your perspective if you have a quick minute.

Cheers,
{c_name}"""

    return {
        "connection_note": connection_note.strip(),
        "recruiter_pitch": recruiter_pitch.strip(),
        "peer_inquiry": peer_inquiry.strip(),
    }


def generate_outreach_strategies(job: dict[str, Any], profile: dict[str, Any], use_llm: bool = True) -> dict[str, Any]:
    """Generate LinkedIn contact archetypes, search URLs, and 3 Voice DNA outreach variations.

    Args:
        job: Job listing dict (title, company, description, etc.).
        profile: Candidate profile dict (personal, skills, etc.).
        use_llm: Whether to attempt LLM generation with fallback to deterministic templates.

    Returns:
        Structured outreach strategy dictionary.
    """
    company = str(job.get("company") or "the company").strip()
    title = str(job.get("title") or "the role").strip()
    personal = profile.get("personal", {})
    candidate_name = personal.get("preferred_name") or personal.get("full_name") or "Candidate"

    # Pick top primary skill from candidate profile
    boundary = profile.get("skills_boundary", {})
    primary_skills: list[str] = []
    for cat_list in boundary.values():
        if isinstance(cat_list, list) and cat_list:
            primary_skills.extend(cat_list[:2])
    key_skill = ", ".join(primary_skills[:2]) if primary_skills else "modern software stacks"

    archetypes = get_contact_archetypes(company, title)
    templates = build_template_messages(company, title, candidate_name, key_skill)

    # Optional LLM polish if requested
    messages_out = templates
    if use_llm:
        try:
            from applypilot.llm import get_client
            client = get_client()
            banned_str = ", ".join(BANNED_WORDS[:30])
            prompt = f"""Generate 3 outreach messages for {candidate_name} applying to {title} at {company}.
Candidate tools: {key_skill}.

RULES:
- Voice: direct, engineering-grounded, zero fluff.
- NO BANNED WORDS: {banned_str}.
- No em dashes.

Return ONLY a JSON object with exactly these 3 keys:
1. "connection_note": strictly under 290 characters (LinkedIn limit is 300).
2. "recruiter_pitch": direct pitch under 130 words.
3. "peer_inquiry": informal peer message under 80 words asking about team and tech.
"""
            llm_res = client.chat([{"role": "user", "content": prompt}], max_tokens=1024, temperature=0.3)
            # Extract JSON from markdown fences if present
            cleaned = sanitize_text(llm_res)
            json_match = re.search(r"\{[\s\S]*\}", cleaned)
            if json_match:
                import json
                data = json.loads(json_match.group(0))
                cn = data.get("connection_note", "").strip()
                if 20 < len(cn) <= 300:
                    messages_out["connection_note"] = cn
                if data.get("recruiter_pitch"):
                    messages_out["recruiter_pitch"] = data.get("recruiter_pitch").strip()
                if data.get("peer_inquiry"):
                    messages_out["peer_inquiry"] = data.get("peer_inquiry").strip()
        except Exception as e:
            log.debug("LLM outreach generation fallback: %s", e)

    return {
        "company": company,
        "title": title,
        "archetypes": archetypes,
        "messages": {
            "connection_note": {
                "label": "Solicitud de Conexión LinkedIn",
                "char_count": len(messages_out["connection_note"]),
                "max_chars": 300,
                "text": messages_out["connection_note"],
                "tip": "Máximo 300 caracteres permitidos por LinkedIn para notas de invitación."
            },
            "recruiter_pitch": {
                "label": "Mensaje Directo a Reclutador (InMail)",
                "word_count": len(messages_out["recruiter_pitch"].split()),
                "text": messages_out["recruiter_pitch"],
                "tip": "Enfocado en resultados concretos y alineación técnica inmediata."
            },
            "peer_inquiry": {
                "label": "Contacto Informal con un Par Técnico",
                "word_count": len(messages_out["peer_inquiry"].split()),
                "text": messages_out["peer_inquiry"],
                "tip": "Tono entre pares ingenieros para indagar sobre la cultura y el stack."
            }
        }
    }
