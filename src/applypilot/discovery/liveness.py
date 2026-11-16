"""Liveness Gate & Ghost Job Detection for DuckDuckWork.

Inspects HTML and plain text for signals that a posting has expired, is closed,
or has been filled before spending AI scoring tokens on dead requisitions.
Inspired by Career-Ops liveness engine.
"""

import re
from typing import Any

# Normalized closure patterns across English and Spanish
_HARD_EXPIRED_PATTERNS = [
    re.compile(r"job (is )?no longer available", re.IGNORECASE),
    re.compile(r"job.*no longer open", re.IGNORECASE),
    re.compile(r"this job has expired", re.IGNORECASE),
    re.compile(r"job posting has expired", re.IGNORECASE),
    re.compile(r"no longer accepting applications", re.IGNORECASE),
    re.compile(r"this (position|role|job) (is )?no longer", re.IGNORECASE),
    re.compile(r"\b(?:job|jobs|position|role|posting|opening|vacancy|requisition|req|listing)\b[\s\S]{0,60}?\bhas been filled\b(?!\s+out)", re.IGNORECASE),
    re.compile(r"role is closed\b(?!-)", re.IGNORECASE),
    re.compile(r"position is closed\b(?!-)", re.IGNORECASE),
    re.compile(r"application window (is |has )?closed", re.IGNORECASE),
    re.compile(r"esta oferta (ya )?ha expirado", re.IGNORECASE),
    re.compile(r"ya no se aceptan solicitudes", re.IGNORECASE),
    re.compile(r"(ya no acepta|no se aceptan m[aá]s) postulaciones", re.IGNORECASE),
    re.compile(r"(este empleo|esta oferta|esta vacante) (ya )?(est[aá] cerrada|ha finalizado|cerr[oó]|expir[oó])", re.IGNORECASE),
    re.compile(r"el proceso (de selecci[oó]n )?ha finalizado", re.IGNORECASE),
    re.compile(r"convocatoria (cerrada|finalizada)", re.IGNORECASE),
    re.compile(r"no estamos recibiendo (m[aá]s )?postulaciones", re.IGNORECASE),
    re.compile(r"postulaciones cerradas", re.IGNORECASE),
    re.compile(r"esta vacante (ya )?no est[aá] disponible", re.IGNORECASE),
    re.compile(r"el puesto ha sido cubierto", re.IGNORECASE),
    re.compile(r"oferta no disponible", re.IGNORECASE),
    re.compile(r"404 not found|p[aá]gina no encontrada", re.IGNORECASE),
]


def check_posting_liveness(content: str | None, status_code: int = 200) -> dict[str, Any]:
    """Evaluate whether a job posting is live or closed/expired.

    Args:
        content: Raw HTML or plain text of the job description / page.
        status_code: HTTP response status code (404, 410, etc.).

    Returns:
        dict: {"is_live": bool, "reason": str | None}
    """
    if status_code in (404, 410):
        return {"is_live": False, "reason": f"HTTP {status_code} Not Found / Gone"}

    if not content or len(content.strip()) < 50:
        return {"is_live": False, "reason": "Insufficient content / empty page"}

    # Normalize unicode quotes and whitespace
    normalized = content.replace("’", "'").replace("“", '"').replace("”", '"')

    for pattern in _HARD_EXPIRED_PATTERNS:
        match = pattern.search(normalized)
        if match:
            matched_phrase = match.group(0).strip()
            return {"is_live": False, "reason": f"Detected closure phrase: '{matched_phrase}'"}

    return {"is_live": True, "reason": None}
