"""
CollabV AI - Resume file storage
===================================
Persists the actual uploaded resume bytes (PDF/DOCX) to a local uploads/
directory, so the Professor Dashboard's candidate view can preview/download
the original file rather than just the extracted text. Previously,
upload_student_resume/upload_employee_resume (api.py) discarded the raw
bytes immediately after text extraction - only resume_filename (a string)
and resume_text were ever persisted.

Path safety: the client's filename is NEVER used to build the on-disk path.
The stored filename is entirely server-generated (user id + random hex +
a validated extension), so there is no user-controlled path segment for a
".."-style traversal to exploit. resolve_resume_path adds a second layer of
defense by verifying the resolved path still lives under the uploads root.
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Optional

UPLOADS_ROOT = Path(__file__).parent.parent / "uploads" / "resumes"
ALLOWED_EXTENSIONS = {".pdf", ".docx"}
MAX_RESUME_BYTES = 10 * 1024 * 1024  # 10 MB

_SAFE_ID_PATTERN = re.compile(r"[^A-Za-z0-9_-]")


class UnsupportedResumeFileType(ValueError):
    pass


class ResumeFileTooLarge(ValueError):
    pass


def _safe_id(user_id: str) -> str:
    cleaned = _SAFE_ID_PATTERN.sub("_", user_id or "user")
    return cleaned[:64] or "user"


def save_resume_file(user_id: str, filename: str, content: bytes) -> str:
    """Save resume bytes under uploads/resumes/, returning a path relative
    to the repo root (e.g. "uploads/resumes/STU-JANE__a1b2c3d4.pdf") to
    store in the DB. Raises ResumeFileTooLarge / UnsupportedResumeFileType
    on invalid input - callers should catch these and surface a 4xx."""
    if len(content) > MAX_RESUME_BYTES:
        raise ResumeFileTooLarge(
            f"Resume file is too large ({len(content) / 1_000_000:.1f} MB) - the limit is "
            f"{MAX_RESUME_BYTES // 1_000_000} MB.",
        )

    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise UnsupportedResumeFileType(
            f"Unsupported file type '{ext or '(none)'}' for storage - only PDF and DOCX resumes "
            "can be saved for preview/download.",
        )

    UPLOADS_ROOT.mkdir(parents=True, exist_ok=True)
    stored_name = f"{_safe_id(user_id)}__{uuid.uuid4().hex[:8]}{ext}"
    dest = UPLOADS_ROOT / stored_name
    dest.write_bytes(content)

    repo_root = Path(__file__).parent.parent
    return str(dest.relative_to(repo_root)).replace("\\", "/")


def resolve_resume_path(relative_path: str) -> Optional[Path]:
    """Re-join a stored relative path against the repo root and verify it
    still resolves inside the uploads root before returning it - defense in
    depth on top of the already-safe, server-generated filename."""
    if not relative_path:
        return None
    repo_root = Path(__file__).parent.parent
    candidate = (repo_root / relative_path).resolve()
    try:
        candidate.relative_to(UPLOADS_ROOT.resolve())
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate


__all__ = [
    "save_resume_file", "resolve_resume_path",
    "UnsupportedResumeFileType", "ResumeFileTooLarge",
    "MAX_RESUME_BYTES", "ALLOWED_EXTENSIONS",
]
