"""
Admin dashboard endpoints — requires HTTP Basic Auth.

Auth: ADMIN_USERNAME + ADMIN_PASSWORD env vars (set in .env).
If either is missing, all /admin/* endpoints return 503.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import case, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from auth import require_admin
from database import Prospect, Unsubscribe, get_session

router = APIRouter()
_templates = Jinja2Templates(directory="templates")


# ── GET /admin ─────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse, include_in_schema=False)
@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def admin_dashboard(request: Request):
    # Auth is handled client-side via JS login form; API endpoints still enforce Basic Auth.
    return _templates.TemplateResponse("admin.html", {"request": request})


# ── GET /admin/stats ───────────────────────────────────────────────────────────

@router.get("/stats")
async def admin_stats(
    db: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin),
):
    now = datetime.now(timezone.utc)

    # ── pipeline_overview ────────────────────────────────────────────────────
    status_result = await db.execute(
        select(Prospect.status, func.count().label("cnt")).group_by(Prospect.status)
    )
    breakdown = {row.status: row.cnt for row in status_result}
    total_prospects = sum(breakdown.values())

    pipeline_overview = {
        "total_prospects": total_prospects,
        "breakdown": {
            s: breakdown.get(s, 0)
            for s in ("pending", "processing", "scored", "contacted", "replied", "booked", "unsubscribed")
        },
    }

    # ── email_funnel ──────────────────────────────────────────────────────────
    sent = (
        breakdown.get("contacted", 0)
        + breakdown.get("replied", 0)
        + breakdown.get("booked", 0)
    )

    opened_result = await db.execute(
        select(func.count()).where(Prospect.open_count > 0)
    )
    opened = opened_result.scalar() or 0

    clicked_result = await db.execute(
        select(func.count()).where(Prospect.click_count > 0)
    )
    clicked = clicked_result.scalar() or 0

    email_funnel = {
        "sent": sent,
        "opened": opened,
        "clicked": clicked,
        "open_rate": round(opened / sent * 100, 1) if sent else 0.0,
        "click_rate": round(clicked / sent * 100, 1) if sent else 0.0,
    }

    # ── by_segment ────────────────────────────────────────────────────────────
    seg_result = await db.execute(
        select(
            Prospect.segment,
            func.count().label("count"),
            func.sum(
                case((Prospect.status.in_(["contacted", "replied", "booked"]), 1), else_=0)
            ).label("contacted"),
            func.sum(case((Prospect.open_count > 0, 1), else_=0)).label("opened"),
            func.sum(case((Prospect.click_count > 0, 1), else_=0)).label("clicked"),
        )
        .group_by(Prospect.segment)
        .order_by(func.count().desc())
    )
    by_segment = [
        {
            "segment": row.segment or "unknown",
            "count": row.count,
            "contacted": row.contacted,
            "open_rate": round(row.opened / row.contacted * 100, 1) if row.contacted else 0.0,
            "click_rate": round(row.clicked / row.contacted * 100, 1) if row.contacted else 0.0,
        }
        for row in seg_result
    ]

    # ── by_industry ───────────────────────────────────────────────────────────
    ind_result = await db.execute(
        select(
            Prospect.business_category,
            func.count().label("count"),
            func.sum(
                case((Prospect.status.in_(["contacted", "replied", "booked"]), 1), else_=0)
            ).label("contacted"),
            func.sum(case((Prospect.open_count > 0, 1), else_=0)).label("opened"),
            func.avg(Prospect.opportunity_score).label("avg_score"),
        )
        .group_by(Prospect.business_category)
        .order_by(func.count().desc())
        .limit(20)
    )
    by_industry = [
        {
            "business_category": row.business_category or "unknown",
            "count": row.count,
            "contacted": row.contacted,
            "open_rate": round(row.opened / row.contacted * 100, 1) if row.contacted else 0.0,
            "avg_opportunity_score": round(row.avg_score or 0, 1),
        }
        for row in ind_result
    ]

    # ── by_location ───────────────────────────────────────────────────────────
    loc_result = await db.execute(
        select(
            Prospect.location_city,
            Prospect.location_state,
            func.count().label("count"),
            func.sum(
                case((Prospect.status.in_(["contacted", "replied", "booked"]), 1), else_=0)
            ).label("contacted"),
            func.sum(case((Prospect.open_count > 0, 1), else_=0)).label("opened"),
        )
        .group_by(Prospect.location_city, Prospect.location_state)
        .order_by(func.count().desc())
        .limit(20)
    )
    by_location = []
    for row in loc_result:
        city = row.location_city or ""
        state = row.location_state or ""
        location = f"{city}, {state}".strip(", ") if city or state else "unknown"
        by_location.append(
            {
                "location": location,
                "count": row.count,
                "contacted": row.contacted,
                "open_rate": round(row.opened / row.contacted * 100, 1) if row.contacted else 0.0,
            }
        )

    # ── by_campaign ───────────────────────────────────────────────────────────
    # campaign_id stored as "campaign_id::job_id" — strip suffix in Python
    camp_result = await db.execute(
        select(
            Prospect.campaign_id,
            func.count().label("count"),
            func.sum(
                case((Prospect.status.in_(["contacted", "replied", "booked"]), 1), else_=0)
            ).label("contacted"),
            func.sum(case((Prospect.open_count > 0, 1), else_=0)).label("opened"),
            func.max(Prospect.created_at).label("latest_created"),
        )
        .where(Prospect.campaign_id.isnot(None))
        .group_by(Prospect.campaign_id)
        .order_by(func.max(Prospect.created_at).desc())
        .limit(10)
    )

    def _strip_suffix(composite: str) -> str:
        parts = composite.split("::", 1)
        return parts[0] if len(parts) == 2 else composite

    by_campaign = [
        {
            "campaign_id": _strip_suffix(row.campaign_id),
            "count": row.count,
            "contacted": row.contacted,
            "open_rate": round(row.opened / row.contacted * 100, 1) if row.contacted else 0.0,
            "latest_created": row.latest_created.isoformat() if row.latest_created else None,
        }
        for row in camp_result
    ]

    # ── timeline (last 12 weeks) ──────────────────────────────────────────────
    twelve_weeks_ago = now - timedelta(weeks=12)
    timeline_result = await db.execute(
        text("""
            SELECT
                strftime('%Y-W%W', created_at) AS week,
                COUNT(*) AS prospects_added,
                SUM(CASE WHEN email_sent_at IS NOT NULL THEN 1 ELSE 0 END) AS emails_sent
            FROM prospects
            WHERE created_at >= :since
            GROUP BY week
            ORDER BY week ASC
        """),
        {"since": twelve_weeks_ago.isoformat()},
    )
    timeline = [
        {
            "week": row.week,
            "prospects_added": row.prospects_added,
            "emails_sent": row.emails_sent,
        }
        for row in timeline_result
    ]

    # ── unsubscribes ──────────────────────────────────────────────────────────
    seven_days_ago = now - timedelta(days=7)
    thirty_days_ago = now - timedelta(days=30)

    unsub_total = (await db.execute(select(func.count()).select_from(Unsubscribe))).scalar() or 0
    unsub_7d = (
        await db.execute(
            select(func.count()).select_from(Unsubscribe).where(Unsubscribe.created_at >= seven_days_ago)
        )
    ).scalar() or 0
    unsub_30d = (
        await db.execute(
            select(func.count()).select_from(Unsubscribe).where(Unsubscribe.created_at >= thirty_days_ago)
        )
    ).scalar() or 0

    # ── roi ───────────────────────────────────────────────────────────────────
    roi_result = await db.execute(
        select(
            func.count(Prospect.booked_at).label("total_booked"),
            func.sum(Prospect.deal_value).label("total_revenue"),
            func.avg(Prospect.deal_value).label("avg_deal_value"),
        )
    )
    roi_row = roi_result.one()
    total_booked = roi_row.total_booked or 0
    total_revenue = round(roi_row.total_revenue or 0.0, 2)
    avg_deal_value = round(roi_row.avg_deal_value or 0.0, 2)
    conversion_rate = round(total_booked / sent * 100, 1) if sent else 0.0

    return {
        "pipeline_overview": pipeline_overview,
        "email_funnel": email_funnel,
        "by_segment": by_segment,
        "by_industry": by_industry,
        "by_location": by_location,
        "by_campaign": by_campaign,
        "timeline": timeline,
        "unsubscribes": {
            "total": unsub_total,
            "last_7_days": unsub_7d,
            "last_30_days": unsub_30d,
        },
        "roi": {
            "total_booked": total_booked,
            "total_revenue": total_revenue,
            "avg_deal_value": avg_deal_value,
            "conversion_rate": conversion_rate,
        },
    }


# ── GET /admin/warm-leads ──────────────────────────────────────────────────────

@router.get("/warm-leads")
async def warm_leads(
    db: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin),
):
    """Prospects that opened the email but haven't replied or booked yet."""
    result = await db.execute(
        select(Prospect)
        .where(
            Prospect.open_count > 0,
            Prospect.status.not_in(["replied", "booked", "unsubscribed"]),
        )
        .order_by(Prospect.last_opened_at.desc())
        .limit(50)
    )
    prospects = result.scalars().all()

    return [
        {
            "id": p.id,
            "business_name": p.business_name,
            "location_city": p.location_city,
            "location_state": p.location_state,
            "segment": p.segment,
            "status": p.status,
            "open_count": p.open_count,
            "click_count": p.click_count,
            "email_sent_at": p.email_sent_at.isoformat() if p.email_sent_at else None,
            "subject_used": p.subject_used,
            "last_opened_at": p.last_opened_at.isoformat() if p.last_opened_at else None,
            "email_address": p.email_address,
            "phone": p.phone,
            "opportunity_score": p.opportunity_score,
        }
        for p in prospects
    ]


# ── POST /admin/prospects/{id}/mark-status ────────────────────────────────────

@router.post("/prospects/{prospect_id}/mark-status")
async def admin_mark_status(
    prospect_id: int,
    body: dict,
    db: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin),
):
    """Admin version of mark-status — uses BasicAuth instead of Bearer token."""
    from fastapi import HTTPException as _HTTPException

    allowed = {"replied", "booked"}
    status = body.get("status")
    if status not in allowed:
        raise _HTTPException(
            status_code=400,
            detail=f"Invalid status '{status}'. Must be one of: {sorted(allowed)}",
        )

    from sqlalchemy import select as _select
    result = await db.execute(_select(Prospect).where(Prospect.id == prospect_id))
    prospect = result.scalar_one_or_none()
    if prospect is None:
        raise _HTTPException(status_code=404, detail=f"Prospect '{prospect_id}' not found")

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
