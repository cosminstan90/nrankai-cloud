"""
Prospect bulk intake and management endpoints.

All endpoints require N8N_API_KEY authentication via Bearer token.

campaign_id is stored in DB as "{campaign_id}::{job_id}" so a single
campaign can be split across multiple bulk intake calls, while job_id
stays unique per batch.
"""

import csv
import io
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_n8n_key, require_worker_key
from database import CallbackLog, EmailTemplate, Prospect, get_session
from schemas import BulkIntakeRequest, BulkIntakeResponse

logger = logging.getLogger(__name__)
router = APIRouter()
_templates = Jinja2Templates(directory="templates")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _composite_campaign_id(campaign_id: str, job_id: str) -> str:
    return f"{campaign_id}::{job_id}"


def _parse_campaign_id(composite: str) -> tuple[str, str]:
    """Split "campaign_id::job_id" → (campaign_id, job_id)."""
    parts = composite.split("::", 1)
    return (parts[0], parts[1]) if len(parts) == 2 else (composite, "")


def _prospect_to_dict(p: Prospect) -> dict:
    campaign_id_raw, job_id_raw = _parse_campaign_id(p.campaign_id or "")
    return {
        "id": p.id,
        "campaign_id": campaign_id_raw,
        "job_id": job_id_raw,
        "url": p.url,
        "business_name": p.business_name,
        "business_category": p.business_category,
        "location_city": p.location_city,
        "location_state": p.location_state,
        "google_place_id": p.google_place_id,
        "phone": p.phone,
        "email_address": p.email_address,
        "google_rating": p.google_rating,
        "review_count": p.review_count,
        "has_website": p.has_website,
        "opportunity_score": p.opportunity_score,
        "design_score": p.design_score,
        "geo_visibility_score": p.geo_visibility_score,
        "ai_citation_score": p.ai_citation_score,
        "stack": p.stack,
        "is_old_site": p.is_old_site,
        "mobile_score": p.mobile_score,
        "top_issues": p.top_issues,
        "segment": p.segment,
        "status": p.status,
        "gap_report_text": p.gap_report_text,
        "callback_url": p.callback_url,
        "email_sent_at": p.email_sent_at.isoformat() if p.email_sent_at else None,
        "subject_used": p.subject_used,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "processed_at": p.processed_at.isoformat() if p.processed_at else None,
    }


# ── 1. POST /prospects/bulk ────────────────────────────────────────────────────

@router.post("/bulk", response_model=BulkIntakeResponse)
async def bulk_intake(
    request: BulkIntakeRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_session),
    _: None = Depends(require_n8n_key),
):
    """
    Accept a batch of prospects.  Duplicate google_place_id rows are silently
    skipped.  Returns immediately; scoring runs in the background.
    """
    from tasks import process_prospects_batch  # lazy to avoid circular import

    job_id = str(uuid.uuid4())
    composite_campaign = _composite_campaign_id(request.campaign_id, job_id)

    accepted = 0
    duplicates = 0

    for lead in request.leads:
        prospect = Prospect(
            campaign_id=composite_campaign,
            url=lead.url,
            business_name=lead.business_name,
            business_category=lead.business_category,
            location_city=lead.location_city,
            location_state=lead.location_state,
            google_place_id=lead.google_place_id,
            phone=lead.phone,
            email_address=lead.email_address,
            google_rating=lead.google_rating,
            review_count=lead.review_count,
            has_website=bool(lead.url),
            callback_url=request.callback_url,
            status="pending",
        )
        db.add(prospect)
        try:
            await db.flush()
            accepted += 1
        except IntegrityError:
            await db.rollback()
            duplicates += 1
            # Re-open the transaction for subsequent inserts
            await db.begin()

    await db.commit()

    if accepted > 0:
        background_tasks.add_task(
            process_prospects_batch, job_id, request.campaign_id
        )

    return BulkIntakeResponse(accepted=accepted, duplicates=duplicates, job_id=job_id)


# ── 2. GET /prospects/job/{job_id} ────────────────────────────────────────────

@router.get("/job/{job_id}")
async def job_status(
    job_id: str,
    db: AsyncSession = Depends(get_session),
    _: None = Depends(require_n8n_key),
):
    """Return progress summary for a bulk intake job."""
    result = await db.execute(
        select(Prospect).where(Prospect.campaign_id.like(f"%::{job_id}"))
    )
    prospects = result.scalars().all()

    if not prospects:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    total = len(prospects)
    counts: dict[str, int] = {}
    for p in prospects:
        counts[p.status] = counts.get(p.status, 0) + 1

    done = sum(counts.get(s, 0) for s in ("scored", "contacted", "replied", "booked"))
    completed_pct = round(done / total * 100) if total else 0

    return {
        "job_id": job_id,
        "total": total,
        "pending": counts.get("pending", 0),
        "processing": counts.get("processing", 0),
        "scored": counts.get("scored", 0),
        "contacted": counts.get("contacted", 0),
        "completed_pct": completed_pct,
    }


# ── 3. GET /prospects/ ────────────────────────────────────────────────────────

@router.get("/")
async def list_prospects(
    campaign_id: Optional[str] = Query(None),
    segment: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    min_score: int = Query(0, ge=0, le=100),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_session),
    _: None = Depends(require_n8n_key),
):
    """Paginated prospect list with optional filters."""
    stmt = select(Prospect)

    if campaign_id:
        stmt = stmt.where(Prospect.campaign_id.like(f"{campaign_id}::%"))
    if segment:
        stmt = stmt.where(Prospect.segment == segment)
    if status:
        stmt = stmt.where(Prospect.status == status)
    if min_score:
        stmt = stmt.where(Prospect.opportunity_score >= min_score)

    stmt = stmt.offset(offset).limit(limit).order_by(Prospect.opportunity_score.desc())

    result = await db.execute(stmt)
    prospects = result.scalars().all()
    return [_prospect_to_dict(p) for p in prospects]


# ── 4. GET /prospects/{id}/email-preview ─────────────────────────────────────

def _safe_list_get(lst: list | None, index: int, default: str) -> str:
    """Extrage element din lista in mod sigur, cu fallback."""
    if lst and isinstance(lst, list) and len(lst) > index:
        return str(lst[index])
    return default


@router.get("/{prospect_id}/email-preview")
async def email_preview(
    prospect_id: int,
    db: AsyncSession = Depends(get_session),
    _: None = Depends(require_worker_key),
):
    """Returneaza template-ul de email cu placeholder-ele completate pentru prospect."""
    import os

    result = await db.execute(select(Prospect).where(Prospect.id == prospect_id))
    prospect = result.scalar_one_or_none()
    if prospect is None:
        raise HTTPException(status_code=404, detail=f"Prospect '{prospect_id}' not found")

    segment = prospect.segment or "general"

    tpl_result = await db.execute(
        select(EmailTemplate).where(EmailTemplate.segment == segment)
    )
    template = tpl_result.scalar_one_or_none()
    if template is None:
        raise HTTPException(
            status_code=404,
            detail=f"No email template found for segment '{segment}'. Run POST /email-templates/seed first.",
        )

    # ── Placeholder values ────────────────────────────────────────────────────
    competitors = prospect.competitors_found or []
    top_issues = prospect.top_issues or []

    replacements = {
        "{{business_name}}":    prospect.business_name or "",
        "{{city}}":             prospect.location_city or "",
        "{{business_category}}": prospect.business_category or "",
        "{{competitor_1}}":     _safe_list_get(competitors, 0, "a competitor"),
        "{{competitor_2}}":     _safe_list_get(competitors, 1, "another competitor"),
        "{{gap_report_text}}":  prospect.gap_report_text or "",
        "{{top_issue_1}}":      _safe_list_get(top_issues, 0, "technical issues"),
        "{{top_issue_2}}":      _safe_list_get(top_issues, 1, "missing structured data"),
        "{{google_rating}}":    str(prospect.google_rating) if prospect.google_rating is not None else "your current rating",
        "{{booking_url}}":      os.environ.get("BOOKING_URL", "https://nrankai.com"),
    }

    subject   = template.subject   or ""
    body_html = template.body_html or ""
    body_text = template.body_text or ""

    for placeholder, value in replacements.items():
        subject   = subject.replace(placeholder, value)
        body_html = body_html.replace(placeholder, value)
        body_text = body_text.replace(placeholder, value)

    return {
        "prospect_id": prospect_id,
        "segment":     segment,
        "subject":     subject,
        "body_html":   body_html,
        "body_text":   body_text,
    }


# ── 5. POST /prospects/{id}/mark-contacted ────────────────────────────────────

@router.post("/{prospect_id}/mark-contacted")
async def mark_contacted(
    prospect_id: int,
    body: dict,
    db: AsyncSession = Depends(get_session),
    _: None = Depends(require_n8n_key),
):
    """Mark a prospect as contacted and record email metadata."""
    result = await db.execute(select(Prospect).where(Prospect.id == prospect_id))
    prospect = result.scalar_one_or_none()
    if prospect is None:
        raise HTTPException(status_code=404, detail=f"Prospect '{prospect_id}' not found")

    email_sent_raw = body.get("email_sent_at")
    if email_sent_raw:
        try:
            prospect.email_sent_at = datetime.fromisoformat(
                email_sent_raw.replace("Z", "+00:00")
            )
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid email_sent_at format — expected ISO 8601")

    prospect.subject_used = body.get("subject_used")
    prospect.status = "contacted"
    prospect.updated_at = datetime.now(timezone.utc)

    await db.commit()
    return {"ok": True, "prospect_id": prospect_id, "status": "contacted"}


# ── 6. GET /prospects/export ──────────────────────────────────────────────────
# NOTE: must be declared BEFORE /{prospect_id}/... routes to avoid routing ambiguity

@router.get("/export")
async def export_csv(
    campaign_id: Optional[str] = Query(None),
    segment: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_session),
    _: None = Depends(require_worker_key),
):
    """Download prospects as CSV."""
    stmt = select(Prospect)

    if campaign_id:
        stmt = stmt.where(Prospect.campaign_id.like(f"{campaign_id}::%"))
    if segment:
        stmt = stmt.where(Prospect.segment == segment)
    if status:
        stmt = stmt.where(Prospect.status == status)

    stmt = stmt.order_by(Prospect.opportunity_score.desc())
    result = await db.execute(stmt)
    prospects = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id", "business_name", "url", "business_category",
        "location_city", "location_state", "phone", "email_address",
        "google_rating", "review_count", "opportunity_score",
        "design_score", "mobile_score", "stack", "is_old_site",
        "segment", "status", "created_at",
    ])
    for p in prospects:
        campaign_raw, _ = _parse_campaign_id(p.campaign_id or "")
        writer.writerow([
            p.id, p.business_name, p.url, p.business_category,
            p.location_city, p.location_state, p.phone, p.email_address,
            p.google_rating, p.review_count, p.opportunity_score,
            p.design_score, p.mobile_score, p.stack, p.is_old_site,
            p.segment, p.status,
            p.created_at.isoformat() if p.created_at else "",
        ])

    output.seek(0)
    filename = f"prospects_{campaign_id or 'all'}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── 7. POST /prospects/{id}/retry-callback ────────────────────────────────────

async def _run_callback_standalone(prospect_id: int) -> None:
    """Wrapper pentru background tasks — deschide propria sesiune DB."""
    import workers.callback_sender as callback_sender
    from database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        await callback_sender.send_callback(prospect_id, db)


@router.post("/{prospect_id}/retry-callback")
async def retry_callback(
    prospect_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_session),
    _: None = Depends(require_worker_key),
):
    """Re-trigger the callback for a prospect."""
    result = await db.execute(select(Prospect).where(Prospect.id == prospect_id))
    prospect = result.scalar_one_or_none()
    if prospect is None:
        raise HTTPException(status_code=404, detail=f"Prospect '{prospect_id}' not found")
    if not prospect.callback_url:
        raise HTTPException(status_code=400, detail="Prospect has no callback_url configured")

    background_tasks.add_task(_run_callback_standalone, prospect_id)
    return {"status": "retry_queued", "prospect_id": prospect_id}


# ── 8. GET /prospects/dashboard ───────────────────────────────────────────────

@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def prospects_dashboard(request: Request):
    """Internal prospects management dashboard."""
    return _templates.TemplateResponse("prospects.html", {"request": request})
