"""Anti-Hallucination Fact Verification Gate for DuckDuckWork.

Programmatically verifies that tailored resumes and generated cover letters
strictly adhere to the candidate's ground-truth profile (companies, schools,
degrees, real metrics, and skills boundary) without inventing credentials or
unearned achievements. Inspired by Career-Ops Block B fact verification.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from applypilot.scoring.validator import FABRICATION_WATCHLIST

log = logging.getLogger(__name__)


def extract_numbers_and_metrics(text: str) -> list[str]:
    """Extract metric strings (percentages, currency, multipliers, scale) from text."""
    patterns = [
        r"\b\d+(?:\.\d+)?%",                            # e.g., 40%, 99.9%
        r"\$\s*\d+(?:,\d{3})*(?:\.\d+)?[kmbKMB]?\b",      # e.g., $100k, $2.5M
        r"\b\d+\s*(?:hours|hrs|minutes|mins|days|weeks|months|seconds|secs)\b", # time metrics
        r"\b\d+x\b",                                     # e.g., 10x, 3x
        r"\b(?:reduced|increased|boosted|saved|cut)\s+[^\n.,;]+", # outcome phrases
    ]
    matches: list[str] = []
    for pat in patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            s = m.group(0).strip()
            if s and s not in matches:
                matches.append(s)
    return matches


def verify_facts(
    generated_text: str,
    profile: dict[str, Any],
    doc_type: str = "resume",
    original_text: str = "",
) -> dict[str, Any]:
    """Execute the Fact Verification Gate over generated text against the candidate profile.

    Args:
        generated_text: The tailored resume or cover letter text.
        profile: Candidate profile dict (with personal, resume_facts, skills_boundary, experience).
        doc_type: "resume" or "cover_letter".
        original_text: Optional original base resume text for diff/baseline check.

    Returns:
        Structured report:
        {
            "verified": bool,
            "confidence_score": int (0-100),
            "confidence_level": "HIGH" | "MEDIUM" | "LOW",
            "verified_entities": dict,
            "hallucinations_detected": list[str],
            "warnings": list[str],
            "errors": list[str],
        }
    """
    errors: list[str] = []
    warnings: list[str] = []
    hallucinations: list[str] = []

    text_lower = (generated_text or "").lower()
    personal = profile.get("personal", {})
    resume_facts = profile.get("resume_facts", {})
    boundary = profile.get("skills_boundary", {})
    experience = profile.get("experience", {})

    verified_entities: dict[str, Any] = {
        "candidate_name": None,
        "companies": [],
        "schools": [],
        "metrics": [],
        "skills": [],
    }

    # 1. Candidate Name Verification
    full_name = (personal.get("full_name") or "").strip()
    preferred_name = (personal.get("preferred_name") or "").strip()
    target_names = [n for n in (full_name, preferred_name) if n]

    if target_names:
        name_found = any(n.lower() in text_lower for n in target_names)
        if name_found:
            verified_entities["candidate_name"] = full_name or preferred_name
        else:
            if doc_type == "cover_letter":
                errors.append(f"Sign-off name '{full_name or preferred_name}' is missing.")
                hallucinations.append("Missing candidate sign-off identity.")
            else:
                warnings.append(f"Candidate name '{full_name or preferred_name}' should be present in resume header.")

    # 2. Preserved Companies Verification
    preserved_companies = [str(c).strip() for c in resume_facts.get("preserved_companies", []) if c]
    for comp in preserved_companies:
        if comp.lower() in text_lower:
            verified_entities["companies"].append(comp)
        else:
            # If doc_type is resume, omitting real employers is an error
            if doc_type == "resume":
                errors.append(f"Real company '{comp}' missing from resume experience.")
                hallucinations.append(f"Omission of real experience at '{comp}'.")

    # 3. Preserved Education & School Verification
    preserved_school = str(resume_facts.get("preserved_school", "")).strip()
    education_level = str(experience.get("education_level", "")).strip()

    if preserved_school:
        if preserved_school.lower() in text_lower:
            verified_entities["schools"].append(preserved_school)
        elif doc_type == "resume":
            errors.append(f"Verified school '{preserved_school}' is missing from education section.")
            hallucinations.append(f"Missing accredited school '{preserved_school}'.")

    # Check for fabricated degrees (e.g. claiming PhD or Master's if not in profile)
    unearned_degrees = ["ph.d", "phd", "doctor of", "master of science", "m.s.", "mba"]
    candidate_highest = (education_level + " " + preserved_school).lower()
    for degree in unearned_degrees:
        if degree in text_lower and degree not in candidate_highest:
            errors.append(f"Unearned degree claimed in text: '{degree.upper()}'.")
            hallucinations.append(f"Fabricated credential/degree '{degree.upper()}'.")

    # 4. Skills Boundary & Fabrication Watchlist Check
    allowed_skills: set[str] = set()
    for cat_items in boundary.values():
        if isinstance(cat_items, (list, set)):
            for s in cat_items:
                allowed_skills.add(str(s).lower().strip())

    # Check against fabrication watchlist
    for fake in FABRICATION_WATCHLIST:
        if len(fake) <= 2:
            continue
        # Use regex word boundaries for accurate skill matching
        if re.search(r"\b" + re.escape(fake) + r"\b", text_lower):
            # Check if candidate legitimately has it in their profile
            if fake not in allowed_skills and fake not in (original_text or "").lower():
                errors.append(f"Fabricated tool/skill detected: '{fake}'.")
                hallucinations.append(f"Fabricated skill '{fake}' not present in profile stack.")

    # Record matched legitimate skills
    for skill in allowed_skills:
        if len(skill) > 2 and re.search(r"\b" + re.escape(skill) + r"\b", text_lower):
            verified_entities["skills"].append(skill)

    # 5. Metrics & Numbers Check
    real_metrics = [str(m).strip() for m in resume_facts.get("real_metrics", []) if m]
    found_metrics = extract_numbers_and_metrics(generated_text)
    verified_entities["metrics"] = found_metrics

    # Check if metrics are wildly exaggerated
    for m in found_metrics:
        # Check for extreme percentage claims (> 500% or unrealistic)
        pct_match = re.search(r"(\d+)%", m)
        if pct_match:
            pct_val = int(pct_match.group(1))
            if pct_val > 500 and not any(str(pct_val) in rm for rm in real_metrics):
                warnings.append(f"High metric percentage claim ({pct_val}%) not found in real metrics.")

    # 6. Calculate Confidence Score and Level
    total_deductions = (len(errors) * 35) + (len(hallucinations) * 20) + (len(warnings) * 5)
    confidence_score = max(0, min(100, 100 - total_deductions))

    if len(errors) == 0 and len(hallucinations) == 0:
        confidence_level = "HIGH"
        verified = True
    elif len(errors) <= 1 and confidence_score >= 60:
        confidence_level = "MEDIUM"
        verified = False
    else:
        confidence_level = "LOW"
        verified = False

    return {
        "verified": verified,
        "confidence_score": confidence_score,
        "confidence_level": confidence_level,
        "verified_entities": verified_entities,
        "hallucinations_detected": hallucinations,
        "warnings": warnings,
        "errors": errors,
    }
