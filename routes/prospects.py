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

import hashlib

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, Request, UploadFile
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
    step: str = Query(default="initial", description="Sequence step: initial | followup_1 | followup_2"),
    db: AsyncSession = Depends(get_session),
    _: None = Depends(require_n8n_key),
):
    """Returneaza template-ul de email cu placeholder-ele completate pentru prospect.

    step param selects which template in a multi-email sequence:
      - initial     → {segment}_initial  (falls back to {segment})
      - followup_1  → {segment}_followup_1
      - followup_2  → {segment}_followup_2
    """
    import os

    result = await db.execute(select(Prospect).where(Prospect.id == prospect_id))
    prospect = result.scalar_one_or_none()
    if prospect is None:
        raise HTTPException(status_code=404, detail=f"Prospect '{prospect_id}' not found")

    segment = prospect.segment or "general"

    # Build candidate template names in preference order
    _STEP_MAP = {
        "initial":    [f"{segment}_initial", segment],
        "followup_1": [f"{segment}_followup_1"],
        "followup_2": [f"{segment}_followup_2"],
    }
    candidates = _STEP_MAP.get(step, [f"{segment}_{step}", segment])

    template = None
    for candidate in candidates:
        tpl_result = await db.execute(
            select(EmailTemplate).where(EmailTemplate.segment == candidate)
        )
        template = tpl_result.scalar_one_or_none()
        if template:
            break

    if template is None:
        raise HTTPException(
            status_code=404,
            detail=f"No email template found for segment='{segment}' step='{step}'. Tried: {candidates}",
        )

    # ── Placeholder values ────────────────────────────────────────────────────
    competitors = prospect.competitors_found or []
    top_issues = prospect.top_issues or []

    # Extract first name from notes field (format: "Job Title | First Last")
    def _extract_first_name(notes: str | None) -> str:
        if not notes:
            return "there"
        parts = notes.split(" | ", 1)
        name_part = parts[-1].strip()          # "Laurent Forcioli, Dds"
        first = name_part.split()[0] if name_part else ""
        # Strip trailing comma/period and credentials
        first = first.rstrip(".,").strip()
        return first or "there"

    replacements = {
        "{{first_name}}":       _extract_first_name(prospect.notes),
        "{{business_name}}":    prospect.business_name or "",
        "{{practice_name}}":    prospect.business_name or "",
        "{{city}}":             prospect.location_city or "",
        "{{business_category}}": prospect.business_category or "",
        "{{competitor_1}}":     _safe_list_get(competitors, 0, "a competitor"),
        "{{competitor_2}}":     _safe_list_get(competitors, 1, "another competitor"),
        "{{gap_report_text}}":  prospect.gap_report_text or "",
        "{{top_issue_1}}":      _safe_list_get(top_issues, 0, "not appearing in AI search results"),
        "{{top_issue_2}}":      _safe_list_get(top_issues, 1, "missing structured data"),
        "{{audit_finding_1}}":  _safe_list_get(top_issues, 0, "not appearing in AI search results"),
        "{{audit_finding_2}}":  _safe_list_get(top_issues, 1, "missing structured data"),
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
        "prospect_id":      prospect_id,
        "segment":          segment,
        "step":             step,
        "template_segment": template.segment,
        "to_email":         prospect.email_address,
        "to_name":          prospect.business_name,
        "subject":          subject,
        "body_html":        body_html,
        "body_text":        body_text,
    }


# ── 5. POST /prospects/import-csv ────────────────────────────────────────────

def _synthetic_place_id(email: str) -> str:
    return "linkedin_" + hashlib.md5(email.lower().encode()).hexdigest()


def _clean(val: str | None) -> str:
    return val.strip() if val else ""


@router.post("/import-csv")
async def import_csv(
    file: UploadFile = File(...),
    campaign_id: str = Query(default="dental_2026-04"),
    segment: str = Query(default="dental"),
    db: AsyncSession = Depends(get_session),
    _: None = Depends(require_n8n_key),
):
    """Import LinkedIn/Vibe Prospecting CSV export directly into prospects table."""
    content = await file.read()
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))

    accepted = 0
    duplicates = 0
    skipped = 0
    now = datetime.now(timezone.utc)

    for row in reader:
        email = _clean(
            row.get("contact_professions_email") or
            row.get("contact_professions_email_address") or
            row.get("email_address") or ""
        )
        if not email or "@" not in email:
            skipped += 1
            continue

        if _clean(row.get("contact_professional_email_status", "")) == "invalid":
            skipped += 1
            continue

        business_name = _clean(
            row.get("prospect_company_name") or
            row.get("business_name") or ""
        )
        if not business_name:
            skipped += 1
            continue

        url = _clean(
            row.get("prospect_company_website") or
            row.get("business_domain") or ""
        )
        if url and not url.startswith("http"):
            url = "https://" + url

        first = _clean(row.get("prospect_first_name", ""))
        last = _clean(row.get("prospect_last_name", ""))
        full_name = f"{first} {last}".strip() or _clean(row.get("prospect_full_name", ""))
        city = _clean(row.get("prospect_city", ""))
        state = _clean(row.get("prospect_region_name", ""))
        job_title = _clean(row.get("prospect_job_title", ""))

        prospect = Prospect(
            campaign_id=campaign_id,
            url=url or None,
            business_name=business_name,
            business_category="Dental Practice",
            location_city=city or None,
            location_state=state or None,
            google_place_id=_synthetic_place_id(email),
            email_address=email,
            has_website=bool(url),
            segment=segment,
            status="pending",
            opportunity_score=0,
            created_at=now,
            updated_at=now,
            notes=f"{job_title} | {full_name}" if job_title else full_name,
        )
        db.add(prospect)
        try:
            await db.flush()
            accepted += 1
        except IntegrityError:
            await db.rollback()
            duplicates += 1

    await db.commit()
    return {
        "ok": True,
        "accepted": accepted,
        "duplicates": duplicates,
        "skipped": skipped,
        "campaign_id": campaign_id,
        "segment": segment,
    }


# ── 7. POST /prospects/{id}/mark-contacted ────────────────────────────────────

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


# ── 8. GET /prospects/next-pending-audit ─────────────────────────────────────

@router.get("/next-pending-audit")
async def next_pending_audit(
    segment: str = Query(default="dental"),
    db: AsyncSession = Depends(get_session),
    _: None = Depends(require_n8n_key),
):
    """Return next prospect with a URL that hasn't been audited yet.
    Returns 204 when no pending prospects remain."""
    result = await db.execute(
        select(Prospect)
        .where(
            Prospect.segment == segment,
            Prospect.url.isnot(None),
            Prospect.top_issues.is_(None),
            Prospect.status == "pending",
        )
        .order_by(Prospect.id)
        .limit(1)
    )
    prospect = result.scalar_one_or_none()
    if prospect is None:
        from fastapi.responses import Response
        return Response(status_code=204)

    return {
        "id": prospect.id,
        "url": prospect.url,
        "business_name": prospect.business_name,
        "city": prospect.location_city,
        "segment": prospect.segment,
    }


# ── 9. PATCH /prospects/{id}/audit-data ──────────────────────────────────────

@router.patch("/{prospect_id}/audit-data")
async def save_audit_data(
    prospect_id: int,
    body: dict,
    db: AsyncSession = Depends(get_session),
    _: None = Depends(require_n8n_key),
):
    """Store GEO audit results on a prospect (top_issues, geo_visibility_score, etc.)."""
    result = await db.execute(select(Prospect).where(Prospect.id == prospect_id))
    prospect = result.scalar_one_or_none()
    if prospect is None:
        raise HTTPException(status_code=404, detail=f"Prospect '{prospect_id}' not found")

    if "top_issues" in body:
        raw = body["top_issues"]
        prospect.top_issues = [
            i["title"] if isinstance(i, dict) else str(i)
            for i in raw[:5]
        ]
    if "geo_score" in body:
        prospect.geo_visibility_score = int(body["geo_score"])
    if "gap_report_text" in body:
        prospect.gap_report_text = str(body["gap_report_text"])[:2000]
    if "status" in body:
        prospect.status = body["status"]

    prospect.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"ok": True, "prospect_id": prospect_id}


# ── 10. GET /prospects/{id} ───────────────────────────────────────────────────
# NOTE: must be declared AFTER all literal /path routes to avoid routing conflict.

@router.get("/{prospect_id}")
async def get_prospect(
    prospect_id: int,
    db: AsyncSession = Depends(get_session),
    _: None = Depends(require_n8n_key),
):
    """
    Fetch a single prospect by ID.
    Used by n8n workflow node 'check-opened' after the 3-day wait to read
    open_count and phone before deciding whether to send an SMS follow-up.
    """
    result = await db.execute(select(Prospect).where(Prospect.id == prospect_id))
    prospect = result.scalar_one_or_none()
    if prospect is None:
        raise HTTPException(status_code=404, detail=f"Prospect '{prospect_id}' not found")
    return _prospect_to_dict(prospect)


# ── 9. POST /prospects/{id}/mark-status ──────────────────────────────────────

@router.post("/{prospect_id}/mark-status")
async def mark_status(
    prospect_id: int,
    body: dict,
    db: AsyncSession = Depends(get_session),
    _: None = Depends(require_n8n_key),
):
    """Mark a prospect as replied or booked, optionally recording deal value and notes."""
    allowed_statuses = {"replied", "booked"}
    status = body.get("status")
    if status not in allowed_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{status}'. Must be one of: {sorted(allowed_statuses)}",
        )

    result = await db.execute(select(Prospect).where(Prospect.id == prospect_id))
    prospect = result.scalar_one_or_none()
    if prospect is None:
        raise HTTPException(status_code=404, detail=f"Prospect '{prospect_id}' not found")

    now = datetime.now(timezone.utc)
    prospect.status = status
    prospect.updated_at = now

    if status == "replied":
        prospect.replied_at = now
    elif status == "booked":
        prospect.booked_at = now

    deal_value = body.get("deal_value")
    if deal_value is not None:
        prospect.deal_value = float(deal_value)

    notes = body.get("notes")
    if notes is not None:
        prospect.notes = str(notes)[:1000]

    await db.commit()
    return {"ok": True, "prospect_id": prospect_id, "status": status}


# ── 12. GET /prospects/dashboard ──────────────────────────────────────────────

@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def prospects_dashboard(request: Request):
    """Internal prospects management dashboard."""
    return _templates.TemplateResponse("prospects.html", {"request": request})
