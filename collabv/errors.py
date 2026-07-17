"""
CollabV AI - structured error codes.

Every HTTPException raised by the API uses one of these codes so that frontend
clients can localize, retry, or route errors without parsing message strings.
The HTTP status and a user-friendly default message ship with each code.

Usage:
    from .errors import api_error, ErrorCode
    raise api_error(ErrorCode.MATCH_NOT_FOUND)
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from fastapi import HTTPException


class ErrorCode(str, Enum):
    # 4xx - client errors
    INVALID_REQUEST           = "INVALID_REQUEST"
    INVALID_CREDENTIALS       = "INVALID_CREDENTIALS"
    AUTHENTICATION_REQUIRED   = "AUTHENTICATION_REQUIRED"
    AUTHORIZATION_FAILED      = "AUTHORIZATION_FAILED"
    INVALID_API_KEY           = "INVALID_API_KEY"
    INVALID_TOKEN             = "INVALID_TOKEN"
    EMAIL_ALREADY_REGISTERED  = "EMAIL_ALREADY_REGISTERED"
    PROFESSOR_NOT_FOUND       = "PROFESSOR_NOT_FOUND"
    COMPANY_NOT_FOUND         = "COMPANY_NOT_FOUND"
    MATCH_NOT_FOUND           = "MATCH_NOT_FOUND"
    MISSING_INPUT             = "MISSING_INPUT"
    INPUT_TOO_LARGE           = "INPUT_TOO_LARGE"
    RATE_LIMITED              = "RATE_LIMITED"
    QUOTA_EXCEEDED            = "QUOTA_EXCEEDED"
    INSUFFICIENT_FEEDBACK     = "INSUFFICIENT_FEEDBACK"
    UNKNOWN_TEMPLATE          = "UNKNOWN_TEMPLATE"

    # 5xx - server errors
    ENGINE_NOT_READY          = "ENGINE_NOT_READY"
    EMBEDDINGS_UNAVAILABLE    = "EMBEDDINGS_UNAVAILABLE"
    CLAUDE_API_ERROR          = "CLAUDE_API_ERROR"
    INTERNAL_ERROR            = "INTERNAL_ERROR"

    # ─── Marketplace ────
    LISTING_NOT_FOUND          = "LISTING_NOT_FOUND"
    LISTING_NOT_ACTIVATABLE    = "LISTING_NOT_ACTIVATABLE"
    LISTING_ALREADY_ACTIVE     = "LISTING_ALREADY_ACTIVE"
    LISTING_INACTIVE           = "LISTING_INACTIVE"
    LISTING_AWAITING_APPROVAL  = "LISTING_AWAITING_APPROVAL"
    BUYER_NOT_FOUND            = "BUYER_NOT_FOUND"
    BUYER_PROFILE_INCOMPLETE   = "BUYER_PROFILE_INCOMPLETE"
    PROPOSAL_NOT_FOUND         = "PROPOSAL_NOT_FOUND"
    INQUIRY_NOT_FOUND          = "INQUIRY_NOT_FOUND"
    STUDENT_NOT_PERMITTED      = "STUDENT_NOT_PERMITTED"
    NOT_LISTING_OWNER          = "NOT_LISTING_OWNER"
    NOT_BUYER_PROFILE_OWNER    = "NOT_BUYER_PROFILE_OWNER"
    PROPOSAL_QUOTA_EXCEEDED    = "PROPOSAL_QUOTA_EXCEEDED"
    STUB_REQUIRES_ADMIN_ACTIVATION = "STUB_REQUIRES_ADMIN_ACTIVATION"

    # ─── Patent <-> Problem Statement matching (Engines 3 & 4) ────
    PROBLEM_STATEMENT_NOT_FOUND = "PROBLEM_STATEMENT_NOT_FOUND"

    # ─── Patent Marketplace: audience matching + offers (Engine 5) ────
    INVALID_TARGET_TYPE   = "INVALID_TARGET_TYPE"
    PATENT_NOT_FOUND       = "PATENT_NOT_FOUND"
    OFFER_NOT_FOUND         = "OFFER_NOT_FOUND"

    # ─── Technology Transfer hub: negotiation threads + requests ────
    INVALID_THREAD_TYPE      = "INVALID_THREAD_TYPE"
    TECH_REQUEST_NOT_FOUND    = "TECH_REQUEST_NOT_FOUND"

    # ─── Job Matching (AI Matching Engine 9) ────
    JOB_NOT_FOUND             = "JOB_NOT_FOUND"

    # ─── Matching Engine 8 (Research Opportunities) ────
    OPPORTUNITY_NOT_FOUND     = "OPPORTUNITY_NOT_FOUND"
    RESUME_NOT_FOUND          = "RESUME_NOT_FOUND"
    FILE_TOO_LARGE            = "FILE_TOO_LARGE"


# ─── HTTP status + default message per code ───────────────────────────────

_DEFINITIONS: dict[ErrorCode, tuple[int, str]] = {
    ErrorCode.INVALID_REQUEST:          (400, "Request is invalid"),
    ErrorCode.INVALID_CREDENTIALS:      (401, "Email or password is incorrect"),
    ErrorCode.AUTHENTICATION_REQUIRED:  (401, "Sign in to continue"),
    ErrorCode.AUTHORIZATION_FAILED:     (403, "You don't have access to this resource"),
    ErrorCode.INVALID_API_KEY:          (401, "The API key is invalid or has been revoked"),
    ErrorCode.INVALID_TOKEN:            (401, "Your session has expired - please sign in again"),
    ErrorCode.EMAIL_ALREADY_REGISTERED: (409, "An account with this email already exists"),
    ErrorCode.PROFESSOR_NOT_FOUND:      (404, "We couldn't find that professor"),
    ErrorCode.COMPANY_NOT_FOUND:        (404, "We couldn't find that company request"),
    ErrorCode.MATCH_NOT_FOUND:          (404, "We couldn't find that match - it may have expired"),
    ErrorCode.MISSING_INPUT:            (400, "Required information is missing"),
    ErrorCode.INPUT_TOO_LARGE:          (413, "The text you submitted is too long"),
    ErrorCode.RATE_LIMITED:             (429, "You're sending requests too fast - please wait a moment"),
    ErrorCode.QUOTA_EXCEEDED:           (429, "Daily limit reached - upgrade your plan or try again tomorrow"),
    ErrorCode.INSUFFICIENT_FEEDBACK:    (400, "Not enough feedback yet to retrain the model"),
    ErrorCode.UNKNOWN_TEMPLATE:         (400, "That contract template doesn't exist"),
    ErrorCode.ENGINE_NOT_READY:         (503, "The matching engine is still starting - try again in a moment"),
    ErrorCode.EMBEDDINGS_UNAVAILABLE:   (503, "The embedding service is unavailable"),
    ErrorCode.CLAUDE_API_ERROR:         (502, "The AI explanation service is temporarily unavailable"),
    ErrorCode.INTERNAL_ERROR:           (500, "Something went wrong on our end"),
    # Marketplace
    ErrorCode.LISTING_NOT_FOUND:         (404, "We couldn't find that listing"),
    ErrorCode.LISTING_NOT_ACTIVATABLE:   (400, "This listing can't be activated from its current state"),
    ErrorCode.LISTING_ALREADY_ACTIVE:    (409, "This listing is already active"),
    ErrorCode.LISTING_INACTIVE:          (409, "This listing isn't accepting inquiries right now"),
    ErrorCode.LISTING_AWAITING_APPROVAL: (409, "This listing is awaiting admin approval"),
    ErrorCode.BUYER_NOT_FOUND:           (404, "We couldn't find that buyer profile"),
    ErrorCode.BUYER_PROFILE_INCOMPLETE:  (400, "Complete your buyer profile to use this feature"),
    ErrorCode.PROPOSAL_NOT_FOUND:        (404, "We couldn't find that proposal"),
    ErrorCode.INQUIRY_NOT_FOUND:         (404, "We couldn't find that inquiry"),
    ErrorCode.STUDENT_NOT_PERMITTED:     (403, "Student accounts can browse and inquire, but can't perform this action"),
    ErrorCode.NOT_LISTING_OWNER:         (403, "Only the inventor (or an admin) can modify this listing"),
    ErrorCode.NOT_BUYER_PROFILE_OWNER:   (403, "You can only modify your own buyer profile"),
    ErrorCode.PROPOSAL_QUOTA_EXCEEDED:   (429, "You've hit your monthly proposal quota - upgrade your plan or wait for the next cycle"),
    ErrorCode.STUB_REQUIRES_ADMIN_ACTIVATION: (403, "This listing belongs to an auto-created inventor stub. Admin/TTO must activate it after confirming the real inventor has consented."),
    ErrorCode.PROBLEM_STATEMENT_NOT_FOUND: (404, "We couldn't find that problem statement"),
    ErrorCode.INVALID_TARGET_TYPE:         (400, "target_type must be one of: company, student, employee, professor, institute"),
    ErrorCode.PATENT_NOT_FOUND:            (404, "We couldn't find that patent"),
    ErrorCode.OFFER_NOT_FOUND:              (404, "We couldn't find that offer"),
    ErrorCode.INVALID_THREAD_TYPE:          (400, "thread_type must be 'offer' or 'inquiry'"),
    ErrorCode.TECH_REQUEST_NOT_FOUND:       (404, "We couldn't find that technology request"),
    ErrorCode.JOB_NOT_FOUND:                (404, "We couldn't find that job posting"),
    ErrorCode.OPPORTUNITY_NOT_FOUND:        (404, "We couldn't find that research opportunity"),
    ErrorCode.RESUME_NOT_FOUND:             (404, "No resume file is on file for this profile"),
    ErrorCode.FILE_TOO_LARGE:               (413, "That file is too large"),
}


def api_error(code: ErrorCode, detail: Optional[str] = None, **extra) -> HTTPException:
    """Construct an HTTPException with a stable error code in the response."""
    status, default_msg = _DEFINITIONS[code]
    payload = {"error": code.value, "message": detail or default_msg}
    if extra:
        payload.update(extra)
    return HTTPException(status_code=status, detail=payload)


__all__ = ["ErrorCode", "api_error"]
