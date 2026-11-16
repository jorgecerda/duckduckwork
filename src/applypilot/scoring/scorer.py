"""Job fit scoring: LLM-powered evaluation of candidate-job match quality.

Scores jobs on a 1-10 scale by comparing the user's resume against each
job description. All personal data is loaded at runtime from the user's
profile and resume file.
"""

import logging
import os
import re
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from applypilot.config import APP_DIR
from applypilot.database import get_connection
from applypilot.llm import get_client

log = logging.getLogger(__name__)


# ── Scoring Prompt ────────────────────────────────────────────────────────

SCORE_PROMPT = """You are a job fit evaluator. Given a candidate's resume and a job description, score how well the candidate fits the role.

SCORING CRITERIA:
- 9-10: Perfect match. Candidate has direct experience in nearly all required skills and qualifications.
- 7-8: Strong match. Candidate has most required skills, minor gaps easily bridged.
- 5-6: Moderate match. Candidate has some relevant skills but missing key requirements.
- 3-4: Weak match. Significant skill gaps, would need substantial ramp-up.
- 1-2: Poor match. Completely different field or experience level.

IMPORTANT FACTORS:
- Weight technical skills heavily (programming languages, frameworks, tools)
- Consider transferable experience (automation, scripting, API work)
- Factor in the candidate's project experience
- Be realistic about experience level vs. job requirements (years of experience, seniority)
- Penalize score significantly if job title domain does not match candidate's target role (e.g. Software Engineer for a Scrum Master role)

RESPOND IN EXACTLY THIS FORMAT (no other text):
SCORE: [1-10]
SUMMARY: [2 concise sentences describing this job in simple, plain terms: what the role does and its main focus, without company boilerplate or buzzwords]
KEYWORDS: [comma-separated ATS keywords from the job description that match or could match the candidate]
REASONING: [2 concise sentences stating explicitly whether the candidate's resume is aligned or not with this job, referencing the match percentage and specific key qualifications or gaps]"""


def _parse_score_response(response: str) -> dict:
    """Parse the LLM's score response into structured data.

    Args:
        response: Raw LLM response text.

    Returns:
        {"score": int, "summary": str, "keywords": str, "reasoning": str}
    """
    score = 0
    summary = ""
    keywords = ""
    reasoning = response

    for line in response.split("\n"):
        line = line.strip()
        if line.startswith("SCORE:"):
            try:
                score = int(re.search(r"\d+", line).group())
                score = max(1, min(10, score))
            except (AttributeError, ValueError):
                score = 0
        elif line.startswith("SUMMARY:"):
            summary = line.replace("SUMMARY:", "").strip()
        elif line.startswith("KEYWORDS:"):
            keywords = line.replace("KEYWORDS:", "").strip()
        elif line.startswith("REASONING:"):
            reasoning = line.replace("REASONING:", "").strip()

    return {"score": score, "summary": summary, "keywords": keywords, "reasoning": reasoning}


def generate_job_ai_summary(title: str, company: str, description: str, client=None) -> str:
    """Generate a clean 2-sentence AI summary of what the role does in simple terms."""
    if not description and not title:
        return ""
    if client is None:
        client = get_client()

    clean_text = re.sub(r'<[^>]*>', ' ', description or '')[:2500]
    prompt = f"""You are a career advisor. Summarize this job in exactly 2 concise sentences in simple, plain terms.
Focus on: what the person in this role will actually do and the main objective of the job.
Do NOT include company marketing fluff, boilerplate, or buzzwords. Keep it under 180 characters total.

Job Title: {title or 'Role'}
Company: {company or 'Direct Employer'}
Description:
{clean_text}
"""
    try:
        if getattr(client, "_is_gemini", False):
            _throttle_llm_call(0.25)
        res = client.chat([{"role": "user", "content": prompt}], max_tokens=100, temperature=0.2)
        summary = res.strip().replace("\n", " ")
        summary = re.sub(r"\s+", " ", summary).strip()
        return summary
    except Exception as e:
        log.warning("Failed to generate AI summary: %s", e)
        return ""


_last_gemini_call = 0.0
_gemini_lock = threading.Lock()


def _throttle_llm_call(min_interval: float = 0.25) -> None:
    """Enforce minimum inter-request interval to prevent socket flood and respect rate limits."""
    global _last_gemini_call
    with _gemini_lock:
        now = time.time()
        wait_needed = min_interval - (now - _last_gemini_call)
        if wait_needed > 0:
            time.sleep(wait_needed)
        _last_gemini_call = time.time()


def score_job(resume_text: str, job: dict) -> dict:
    """Score a single job against the resume.

    Args:
        resume_text: The candidate's full resume text.
        job: Job dict with keys: title, site, location, full_description.

    Returns:
        {"score": int | None, "keywords": str, "reasoning": str, "error": str | None}
    """
    job_text = (
        f"TITLE: {job.get('title', 'N/A')}\n"
        f"COMPANY: {job.get('company') or job.get('site', 'N/A')}\n"
        f"LOCATION: {job.get('location', 'N/A')}\n\n"
        f"DESCRIPTION:\n{(job.get('full_description') or job.get('description') or '')[:6000]}"
    )

    messages = [
        {"role": "system", "content": SCORE_PROMPT},
        {"role": "user", "content": f"RESUME:\n{resume_text}\n\n---\n\nJOB POSTING:\n{job_text}"},
    ]

    try:
        client = get_client()
        if getattr(client, "_is_gemini", False):
            _throttle_llm_call(0.25)
        response = client.chat(messages, max_tokens=512, temperature=0.2)
        return _parse_score_response(response)
    except Exception as e:
        log.error("LLM error scoring job '%s': %s", job.get("title", "?"), e)
        return {"score": None, "keywords": "", "reasoning": "", "error": str(e)}


BATCH_SCORE_PROMPT = """You are a job fit evaluator. Given a candidate's resume and multiple job postings (indexed 0 to N-1), evaluate each job independently on a 1-10 scale.

FOR EACH JOB, RESPOND IN THIS EXACT BLOCK FORMAT SEPARATED BY '===JOB_END===':
JOB_INDEX: [0 to N-1]
SCORE: [1-10]
SUMMARY: [2 concise sentences describing what the role does in plain terms, without boilerplate]
KEYWORDS: [comma-separated matching ATS keywords]
REASONING: [2 concise sentences explaining candidate fit and any gaps]
===JOB_END==="""


def _parse_batch_score_response(response: str, count: int) -> dict[int, dict]:
    """Parse a multi-job batch LLM response into a mapping of index -> score dict."""
    parsed_map: dict[int, dict] = {}
    if not response:
        return parsed_map

    # Support JSON array format if returned by the model
    stripped = response.strip()
    if stripped.startswith("[") or "```json" in stripped:
        import json as _json
        clean_json = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped, flags=re.DOTALL).strip()
        try:
            arr = _json.loads(clean_json)
            if isinstance(arr, list):
                for pos, item in enumerate(arr):
                    if not isinstance(item, dict):
                        continue
                    idx = int(item.get("index", item.get("job_index", pos)))
                    raw_score = int(item.get("score", 0) or 0)
                    if 1 <= raw_score <= 10:
                        raw_score = raw_score * 10
                    if 0 <= idx < count and raw_score > 0:
                        parsed_map[idx] = {
                            "score": min(100, raw_score),
                            "keywords": str(item.get("keywords", "") or ""),
                            "reasoning": str(item.get("reasoning", "") or ""),
                            "summary": str(item.get("summary", "") or "") or None,
                            "error": None,
                        }
                if parsed_map:
                    return parsed_map
        except Exception:
            pass

    blocks = response.split("===JOB_END===")
    for block in blocks:
        block_clean = block.strip()
        if not block_clean:
            continue
        idx_match = re.search(r"JOB_INDEX:\s*(\d+)", block_clean)
        if not idx_match:
            continue
        idx = int(idx_match.group(1))
        if 0 <= idx < count:
            parsed = _parse_score_response(block_clean)
            if parsed.get("score", 0) > 0:
                parsed_map[idx] = parsed
    return parsed_map


def score_jobs_batch(resume_text: str, jobs: list[dict]) -> list[dict]:
    """Score up to 8 jobs in a single LLM request to minimize API quota consumption.

    Falls back to single-job scoring for any item not returned in the batch response.
    """
    if not jobs:
        return []
    if len(jobs) == 1:
        r = score_job(resume_text, jobs[0])
        r["url"] = jobs[0].get("url", "")
        r["title"] = jobs[0].get("title", "?")
        return [r]

    postings_str = []
    for idx, job in enumerate(jobs):
        desc_snippet = (job.get("full_description") or job.get("description") or "")[:2000]
        postings_str.append(
            f"JOB_INDEX: {idx}\n"
            f"TITLE: {job.get('title', 'N/A')}\n"
            f"COMPANY: {job.get('company') or job.get('site', 'N/A')}\n"
            f"LOCATION: {job.get('location', 'N/A')}\n"
            f"DESCRIPTION:\n{desc_snippet}"
        )

    messages = [
        {"role": "system", "content": BATCH_SCORE_PROMPT},
        {"role": "user", "content": f"RESUME:\n{resume_text[:4000]}\n\n---\n\n" + "\n\n---\n\n".join(postings_str)},
    ]

    results: list[dict] = []
    try:
        client = get_client()
        if getattr(client, "_is_gemini", False):
            _throttle_llm_call(0.5)
        response = client.chat(messages, max_tokens=2048, temperature=0.2)
        parsed_map = _parse_batch_score_response(response, len(jobs))
        for idx, job in enumerate(jobs):
            if idx in parsed_map:
                res = parsed_map[idx]
            else:
                res = score_job(resume_text, job)
            res["url"] = job.get("url", "")
            res["title"] = job.get("title", "?")
            results.append(res)
        return results
    except Exception as e:
        log.warning("Batch LLM scoring failed (%s), falling back to single-job scoring", e)
        for job in jobs:
            res = score_job(resume_text, job)
            res["url"] = job.get("url", "")
            res["title"] = job.get("title", "?")
            results.append(res)
        return results


def run_scoring(
    limit: int = 0,
    rescore: bool = False,
    search_id: str | None = None,
    progress_callback: Any | None = None,
    use_batch: bool | None = None,
) -> dict:
    """Score unscored jobs that have full descriptions.

    Args:
        limit: Maximum number of jobs to score in this run.
        rescore: If True, re-score all jobs (not just unscored ones).
        search_id: Optional search_id filter to score jobs for a specific search profile.
        progress_callback: Optional callable(completed: int, total: int) for live progress tracking.
        use_batch: Optional override to batch-score up to 8 jobs per LLM call (enabled automatically in Cloud Mode).

    Returns:
        {"scored": int, "errors": int, "elapsed": float, "distribution": list}
    """
    if use_batch is None:
        use_batch = (
            os.environ.get("VERCEL", "").lower() in ("1", "true")
            or os.environ.get("DUCKDUCKWORK_CLOUD_MODE", "").lower() in ("1", "true")
            or os.environ.get("APPLYPILOT_BATCH_SCORING", "").lower() in ("1", "true")
        )
    app_dir = Path(os.environ.get("APPLYPILOT_DIR", str(APP_DIR)))
    master_resume_path = app_dir / "resume.txt"
    master_resume_text = master_resume_path.read_text(encoding="utf-8") if master_resume_path.exists() else ""

    conn = get_connection()

    params = []
    if rescore:
        query = "SELECT * FROM jobs WHERE (full_description IS NOT NULL OR (description IS NOT NULL AND LENGTH(description) > 50))"
    else:
        query = "SELECT * FROM jobs WHERE (full_description IS NOT NULL OR (description IS NOT NULL AND LENGTH(description) > 50)) AND fit_score IS NULL"

    if search_id:
        query += " AND search_id = ?"
        params.append(search_id)

    query += " ORDER BY discovered_at DESC"
    if limit > 0:
        query += " LIMIT ?"
        params.append(limit)

    jobs = conn.execute(query, params).fetchall()

    if not jobs:
        log.info("No unscored jobs with descriptions found.")
        if progress_callback:
            try:
                progress_callback(0, 0)
            except Exception:
                pass
        return {"scored": 0, "errors": 0, "elapsed": 0.0, "distribution": []}

    # Convert sqlite3.Row to dicts if needed
    if jobs and not isinstance(jobs[0], dict):
        columns = jobs[0].keys()
        jobs = [dict(zip(columns, row)) for row in jobs]

    import concurrent.futures
    import threading

    total_jobs = len(jobs)
    db_lock = threading.Lock()
    log.info("Scoring %d jobs concurrently (max_workers=5)...", total_jobs)
    if progress_callback:
        try:
            progress_callback(0, total_jobs)
        except Exception:
            pass

    t0 = time.time()
    completed = 0
    errors = 0
    results: list[dict] = []

    def _score_one(job_item: dict) -> dict:
        desc_text = job_item.get("full_description") or job_item.get("description") or ""
        from applypilot.discovery.liveness import check_posting_liveness
        liveness = check_posting_liveness(desc_text)
        if not liveness["is_live"]:
            return {
                "score": 0,
                "keywords": "N/A (Oferta Expirada / Requisicion Cerrada)",
                "reasoning": f"Liveness Gate: {liveness['reason']}",
                "url": job_item["url"],
                "title": job_item.get("title", "?"),
                "is_expired": 1,
                "expired_reason": liveness["reason"],
            }

        j_search_id = job_item.get("search_id")
        j_resume_text = master_resume_text
        if j_search_id:
            search_txt_path = app_dir / "resumes" / f"{j_search_id}.txt"
            if search_txt_path.exists():
                st = search_txt_path.read_text(encoding="utf-8").strip()
                if st:
                    j_resume_text = st
        res = score_job(j_resume_text, job_item)
        res["url"] = job_item["url"]
        res["title"] = job_item.get("title", "?")
        return res

    def _persist_result(res: dict) -> None:
        nonlocal completed, errors
        results.append(res)
        completed += 1
        if res.get("error"):
            errors += 1
            log.warning(
                "Scoring failed for '%s': %s (leaving job unscored for retry)",
                res.get("title", "?"),
                res["error"],
            )
        else:
            if res.get("score") is None:
                errors += 1
            now = datetime.now(UTC).isoformat()
            with db_lock:
                if res.get("is_expired"):
                    conn.execute(
                        "UPDATE jobs SET fit_score = ?, score_reasoning = ?, ai_summary = ?, scored_at = ?, is_expired = 1, expired_reason = ? WHERE url = ?",
                        (res["score"], f"{res['keywords']}\n{res['reasoning']}", res.get("summary"), now, res.get("expired_reason"), res["url"]),
                    )
                else:
                    conn.execute(
                        "UPDATE jobs SET fit_score = ?, score_reasoning = ?, ai_summary = ?, scored_at = ? WHERE url = ?",
                        (res["score"], f"{res['keywords']}\n{res['reasoning']}", res.get("summary"), now, res["url"]),
                    )
                conn.commit()
            log.info(
                "[%d/%d] score=%s  %s",
                completed, total_jobs, res.get("score"), res["title"][:60],
            )
        if progress_callback:
            try:
                progress_callback(completed, total_jobs)
            except Exception:
                pass

    if use_batch and len(jobs) > 1:
        from applypilot.discovery.liveness import check_posting_liveness
        live_by_search: dict[str, list[dict]] = {}
        for job_item in jobs:
            desc_text = job_item.get("full_description") or job_item.get("description") or ""
            liveness = check_posting_liveness(desc_text)
            if not liveness["is_live"]:
                _persist_result({
                    "score": 0,
                    "keywords": "N/A (Oferta Expirada / Requisicion Cerrada)",
                    "reasoning": f"Liveness Gate: {liveness['reason']}",
                    "url": job_item["url"],
                    "title": job_item.get("title", "?"),
                    "is_expired": 1,
                    "expired_reason": liveness["reason"],
                })
            else:
                sid_key = str(job_item.get("search_id") or "")
                live_by_search.setdefault(sid_key, []).append(job_item)

        for sid_key, group_jobs in live_by_search.items():
            j_resume_text = master_resume_text
            if sid_key:
                search_txt_path = app_dir / "resumes" / f"{sid_key}.txt"
                if search_txt_path.exists():
                    st = search_txt_path.read_text(encoding="utf-8").strip()
                    if st:
                        j_resume_text = st
            for i in range(0, len(group_jobs), 8):
                chunk = group_jobs[i : i + 8]
                batch_res = score_jobs_batch(j_resume_text, chunk)
                for res in batch_res:
                    _persist_result(res)
    else:
        workers = 4 if getattr(get_client(), "_is_gemini", False) else 5
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_job = {executor.submit(_score_one, job): job for job in jobs}
            for future in concurrent.futures.as_completed(future_to_job):
                try:
                    res = future.result()
                    _persist_result(res)
                except Exception as e:
                    completed += 1
                    errors += 1
                    log.error("Scoring thread error: %s", e)
                    if progress_callback:
                        try:
                            progress_callback(completed, total_jobs)
                        except Exception:
                            pass

    elapsed = time.time() - t0
    log.info("Done: %d scored in %.1fs (%.1f jobs/sec)", len(results), elapsed, len(results) / elapsed if elapsed > 0 else 0)

    # Score distribution
    dist = conn.execute("""
        SELECT fit_score, COUNT(*) FROM jobs
        WHERE fit_score IS NOT NULL
        GROUP BY fit_score ORDER BY fit_score DESC
    """).fetchall()
    distribution = [(row[0], row[1]) for row in dist]

    return {
        "scored": len(results),
        "errors": errors,
        "elapsed": elapsed,
        "distribution": distribution,
    }
