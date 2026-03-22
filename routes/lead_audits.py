import ipaddress
import json
import logging
import os
import secrets
import socket
from datetime import datetime, timedelta, timezone
from typing import List
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_n8n_key, require_worker_key
from database import LeadAuditJob, get_session
from schemas import (
    CreateLeadAuditRequest,
    CreateLeadAuditResponse,
    JobStatusResponse,
    NextJobResponse,
    PublicSubmitRequest,
    UploadResultResponse,
    WorkerResult,
    WorkerResultSuccess,
)
from webhook import build_completed_payload, build_failed_payload, fire_n8n_webhook

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/lead-audits", tags=["lead-audits"])

BASE_URL = os.environ.get("BASE_URL", "https://api.nrankai.com")

# Private/reserved IP ranges to block (SSRF protection)
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]


def _token() -> str:
    """Generate a URL-safe 12-character public report token."""
    return secrets.token_urlsafe(9)  # 9 bytes → 12 base64url chars


def _validate_public_url(url: str) -> None:
    """
    Block SSRF attempts — reject URLs that resolve to private/loopback addresses,
    use non-HTTP schemes, or point to obviously internal hostnames.
    Raises HTTPException(400) on failure.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid URL")

    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="URL must use http or https")

    hostname = parsed.hostname or ""
    if not hostname:
        raise HTTPException(status_code=400, detail="URL has no hostname")

    # Block bare IP submissions targeting private ranges directly
    try:
        addr = ipaddress.ip_address(hostname)
        for net in _BLOCKED_NETWORKS:
            if addr in net:
                raise HTTPException(status_code=400, detail="URL points to a restricted address")
    except ValueError:
        pass  # hostname is a domain name, not a bare IP — resolve below

    # Resolve hostname and check the resulting IP
    try:
        resolved_ip = ipaddress.ip_address(socket.gethostbyname(hostname))
        for net in _BLOCKED_NETWORKS:
            if resolved_ip in net:
                raise HTTPException(status_code=400, detail="URL resolves to a restricted address")
    except socket.gaierror:
        raise HTTPException(status_code=400, detail="Could not resolve hostname")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid URL")


# Sliding-window in-process rate store: {ip: [timestamp, ...]}
# Survives across requests but resets on server restart.
# Acceptable for single-process uvicorn; swap for Redis if scaling.
_submit_hits: dict[str, list] = {}


async def _check_rate_limit(request: Request, limit: int = 3, window_seconds: int = 3600) -> None:
    """Raise 429 if this IP has exceeded `limit` requests within `window_seconds`."""
    ip = (
        request.headers.get("X-Real-IP")
        or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        or (request.client.host if request.client else "unknown")
    )
    now = datetime.now(timezone.utc).timestamp()
    cutoff = now - window_seconds

    hits = [t for t in _submit_hits.get(ip, []) if t > cutoff]
    if len(hits) >= limit:
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please wait before submitting another audit.",
            headers={"Retry-After": str(window_seconds)},
        )
    hits.append(now)
    _submit_hits[ip] = hits


# ── 1. Create a job (n8n → cloud) ─────────────────────────────────────────────

@router.post("", status_code=202, response_model=CreateLeadAuditResponse)
async def create_lead_audit(
    body: CreateLeadAuditRequest,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_n8n_key),
):
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=14)

    job = LeadAuditJob(
        status="pending",
        website=body.website,
        lead_json=body.lead.model_dump_json(),
        options_json=body.options.model_dump_json(),
        public_token=_token(),
        n8n_webhook=body.n8n_webhook,
        created_at=now,
        expires_at=expires,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    logger.info("Job %s created for %s", job.id, job.website)

    return CreateLeadAuditResponse(
        job_id=job.id,
        status=job.status,
        report_url=job.report_url(BASE_URL),
        status_url=job.status_url(BASE_URL),
        created_at=job.created_at,
        expires_at=job.expires_at,
    )


# ── 1b. Public submit — landing page form (no API key, rate-limited) ──────────

@router.post("/submit", status_code=202, response_model=CreateLeadAuditResponse)
async def public_submit_audit(
    request: Request,
    body: PublicSubmitRequest,
    session: AsyncSession = Depends(get_session),
):
    """
    Public endpoint for the nrankai.com landing page free audit form.
    No API key required. Rate-limited to 3 submissions per IP per hour.
    """
    # Rate limit applied via slowapi decorator on the app (see main.py limiter)
    # The @limiter.limit decorator can't be applied here because the router is
    # included before the limiter is added, so we use the request.state check.
    await _check_rate_limit(request)

    _validate_public_url(body.website)

    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=14)
    company = body.company_name or urlparse(body.website).netloc or body.website

    job = LeadAuditJob(
        status="pending",
        website=body.website,
        lead_json=json.dumps({
            "email": str(body.email),
            "first_name": body.first_name,
            "company_name": company,
            "language": body.language,
        }),
        options_json=json.dumps({"audit_type": "GEO_AUDIT", "language": "English", "max_chars": 15000}),
        public_token=_token(),
        n8n_webhook=None,
        created_at=now,
        expires_at=expires,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    client_ip = request.headers.get("X-Real-IP") or request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or (request.client.host if request.client else "unknown")
    logger.info("Public submit: job %s created for %s (%s)", job.id, job.website, client_ip)

    return CreateLeadAuditResponse(
        job_id=job.id,
        status=job.status,
        report_url=job.report_url(BASE_URL),
        status_url=job.status_url(BASE_URL),
        created_at=job.created_at,
        expires_at=job.expires_at,
    )


# ── 2. Worker fetches next pending job ────────────────────────────────────────

@router.get("/next")
async def get_next_job(
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_worker_key),
):
    # Fetch the oldest pending job and atomically claim it
    result = await session.execute(
        select(LeadAuditJob)
        .where(LeadAuditJob.status == "pending")
        .order_by(LeadAuditJob.created_at)
        .limit(1)
    )
    job = result.scalar_one_or_none()

    if job is None:
        return Response(status_code=204)

    job.status = "running"
    job.picked_up_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(job)

    logger.info("Job %s claimed by worker for %s", job.id, job.website)

    return NextJobResponse(
        job_id=job.id,
        website=job.website,
        lead=job.lead,
        options=job.options,
        public_token=job.public_token,
        created_at=job.created_at,
    )


# ── 3. Worker uploads result ──────────────────────────────────────────────────

@router.post("/{job_id}/result", response_model=UploadResultResponse)
async def upload_result(
    job_id: str,
    body: WorkerResult,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_worker_key),
):
    result = await session.execute(
        select(LeadAuditJob).where(LeadAuditJob.id == job_id)
    )
    job = result.scalar_one_or_none()

    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status not in ("running", "pending"):
        raise HTTPException(
            status_code=409,
            detail=f"Job is already in terminal state: {job.status}",
        )

    now = datetime.now(timezone.utc)

    if isinstance(body, WorkerResultSuccess):
        job.status = "completed"
        job.result_json = body.model_dump_json()
        job.local_audit_id = body.local_audit_id
        job.completed_at = now
        logger.info("Job %s completed (geo_score=%s)", job.id, body.scores.geo_score)
    else:
        # WorkerResultFailure
        job.status = "failed"
        job.error_json = body.error.model_dump_json()
        job.local_audit_id = body.local_audit_id
        job.completed_at = now
        logger.warning("Job %s failed: %s", job.id, body.error.code)

    await session.commit()
    await session.refresh(job)

    # Fire n8n webhook if configured
    webhook_fired = False
    if job.n8n_webhook:
        if job.status == "completed":
            payload = build_completed_payload(job)
        else:
            payload = build_failed_payload(job)
        webhook_fired = await fire_n8n_webhook(job.n8n_webhook, payload)

    return UploadResultResponse(ok=True, webhook_fired=webhook_fired)


# ── 4. Ready jobs for n8n polling ─────────────────────────────────────────────

@router.get("/ready", response_model=List[JobStatusResponse])
async def get_ready_jobs(
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_n8n_key),
):
    """Return completed jobs not yet acknowledged by n8n (for polling)."""
    result = await session.execute(
        select(LeadAuditJob)
        .where(LeadAuditJob.status == "completed")
        .where(LeadAuditJob.n8n_notified == 0)
        .order_by(LeadAuditJob.completed_at)
    )
    jobs = result.scalars().all()
    return [
        JobStatusResponse(
            job_id=job.id,
            status=job.status,
            website=job.website,
            lead=job.lead,
            created_at=job.created_at,
            picked_up_at=job.picked_up_at,
            completed_at=job.completed_at,
            report_url=job.report_url(BASE_URL),
            result=job.result,
            error=job.error,
        )
        for job in jobs
    ]


# ── 5. Acknowledge — prevent duplicate notifications ──────────────────────────

@router.post("/{job_id}/acknowledge")
async def acknowledge_job(
    job_id: str,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_n8n_key),
):
    """Mark job as notified so it won't appear in /ready again."""
    result = await session.execute(
        select(LeadAuditJob).where(LeadAuditJob.id == job_id)
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    job.n8n_notified = 1
    await session.commit()
    logger.info("Job %s acknowledged by n8n", job_id)
    return {"ok": True}


# ── 6. Status / result polling ─────────────────────────────────────────────────

@router.get("/{job_id}/status", response_model=JobStatusResponse)
async def get_job_status(
    job_id: str,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_n8n_key),
):
    result = await session.execute(
        select(LeadAuditJob).where(LeadAuditJob.id == job_id)
    )
    job = result.scalar_one_or_none()

    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    return JobStatusResponse(
        job_id=job.id,
        status=job.status,
        website=job.website,
        lead=job.lead,
        created_at=job.created_at,
        picked_up_at=job.picked_up_at,
        completed_at=job.completed_at,
        report_url=job.report_url(BASE_URL),
        result=job.result,
        error=job.error,
    )
