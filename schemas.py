from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, EmailStr, HttpUrl, field_validator


# ── Inbound (n8n → cloud) ─────────────────────────────────────────────────────

class LeadInfo(BaseModel):
    email: EmailStr
    first_name: str
    last_name: Optional[str] = None
    company_name: str
    job_title: Optional[str] = None
    language: str = "en"   # "en" | "ro"
    market: Optional[str] = None  # "RO" | "US" | etc.

    @field_validator("language")
    @classmethod
    def validate_language(cls, v: str) -> str:
        v = v.lower()
        if v not in ("en", "ro"):
            raise ValueError("language must be 'en' or 'ro'")
        return v


class AuditOptions(BaseModel):
    audit_type: str = "GEO_AUDIT"
    language: str = "English"
    max_chars: int = 15000

    @field_validator("audit_type")
    @classmethod
    def validate_audit_type(cls, v: str) -> str:
        # Accept both short names (GEO) and full geo_tool names (GEO_AUDIT)
        short_to_full = {
            "GEO":           "GEO_AUDIT",
            "SEO":           "SEO_AUDIT",
            "ACCESSIBILITY": "ACCESSIBILITY_AUDIT",
        }
        v = v.upper()
        v = short_to_full.get(v, v)  # normalise short -> full
        allowed = {
            "GEO_AUDIT", "SEO_AUDIT", "ACCESSIBILITY_AUDIT",
            "AI_OVERVIEW_OPTIMIZATION", "BRAND_VOICE", "COMPETITOR_ANALYSIS",
            "CONTENT_FRESHNESS", "CONTENT_QUALITY", "E_COMMERCE",
            "INTERNAL_LINKING", "LEGAL_GDPR", "LOCAL_SEO",
            "READABILITY_AUDIT", "SECURITY_CONTENT_AUDIT", "SPELLING_GRAMMAR",
            "TECHNICAL_SEO", "TRANSLATION_QUALITY", "UX_CONTENT",
        }
        if v not in allowed:
            raise ValueError(f"audit_type must be one of: {', '.join(sorted(allowed))}")
        return v


class CreateLeadAuditRequest(BaseModel):
    website: str
    lead: LeadInfo
    options: AuditOptions = AuditOptions()
    n8n_webhook: Optional[str] = None

    @field_validator("website")
    @classmethod
    def validate_website(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith(("http://", "https://")):
            v = "https://" + v
        return v


class PublicSubmitRequest(BaseModel):
    """Simplified request for the public landing page form."""
    website: str
    email: EmailStr
    first_name: str = "there"
    company_name: str = ""
    language: str = "en"

    @field_validator("website")
    @classmethod
    def validate_website(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith(("http://", "https://")):
            v = "https://" + v
        return v

    @field_validator("language")
    @classmethod
    def validate_language(cls, v: str) -> str:
        v = v.lower()
        return v if v in ("en", "ro") else "en"


# ── Outbound — job created ─────────────────────────────────────────────────────

class CreateLeadAuditResponse(BaseModel):
    job_id: str
    status: str
    report_url: str
    status_url: str
    created_at: datetime
    expires_at: datetime


# ── Outbound — next job for worker ────────────────────────────────────────────

class NextJobResponse(BaseModel):
    job_id: str
    website: str
    lead: Dict[str, Any]
    options: Dict[str, Any]
    public_token: str
    created_at: datetime


# ── Inbound — worker uploads result ──────────────────────────────────────────

class ScoresResult(BaseModel):
    geo_score: int
    average_page_score: int
    classification: str
    pages_analyzed: int
    industry_benchmark: int = 61
    gap: int


class IssueResult(BaseModel):
    rank: int
    category: str
    severity: str
    title: str
    description: str
    impact: str


class SummaryResult(BaseModel):
    one_liner: str
    executive: str
    opportunity: Optional[str] = None


class EmailReadyResult(BaseModel):
    subject: Dict[str, str]   # {"en": "...", "ro": "..."}
    preheader: Dict[str, str]


class WorkerResultSuccess(BaseModel):
    status: Literal["completed"]
    local_audit_id: Optional[str] = None
    scores: ScoresResult
    top_issues: List[IssueResult]
    quick_wins: List[str]
    summary: SummaryResult
    email_ready: EmailReadyResult


class ErrorResult(BaseModel):
    code: str
    message: str
    retryable: bool = True


class WorkerResultFailure(BaseModel):
    status: Literal["failed"]
    local_audit_id: Optional[str] = None
    error: ErrorResult


WorkerResult = Union[WorkerResultSuccess, WorkerResultFailure]


# ── Outbound — result uploaded ────────────────────────────────────────────────

class UploadResultResponse(BaseModel):
    ok: bool
    webhook_fired: bool


# ── Outbound — status poll ────────────────────────────────────────────────────

class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    website: str
    lead: Dict[str, Any]
    created_at: datetime
    picked_up_at: Optional[datetime]
    completed_at: Optional[datetime]
    report_url: str
    result: Optional[Dict[str, Any]]
    error: Optional[Dict[str, Any]]
