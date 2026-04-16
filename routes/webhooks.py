"""
Webhook endpoints for nrankai-tool integration.

Receives POST /webhook/audit-complete from the geo_tool pipeline
after an audit batch finishes. Updates Prospect scores if prospect_id
is provided and scores are present in the payload.

Auth: require_worker_key (Bearer WORKER_API_KEY)
"""

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_worker_key
from database import get_session, Prospect

logger = logging.getLogger(__name__)

router = APIRouter()


class AuditCompletePayload(BaseModel):
    website: str
    audit_type: Optional[str] = None
    prospect_id: Optional[int] = None
    campaign_id: Optional[str] = None
    status: str = "completed"
    source: Optional[str] = None
    completed_at: Optional[str] = None
    scores: Optional[dict[str, Any]] = None


@router.post("/audit-complete")
async def audit_complete(
    payload: AuditCompletePayload,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_worker_key),
):
    """
    Called by nrankai-tool when a batch audit pipeline finishes.

    - Logs the event
    - If prospect_id present: finds Prospect and updates scores
    - Returns { "received": True, "prospect_id": prospect_id }
    """
    logger.info(
        "[webhook] audit-complete received | website=%s audit_type=%s prospect_id=%s source=%s",
        payload.website,
        payload.audit_type,
        payload.prospect_id,
        payload.source,
    )

    updated_prospect_id = None

    if payload.prospect_id is not None:
        result = await session.execute(
            select(Prospect).where(Prospect.id == payload.prospect_id)
        )
        prospect = result.scalar_one_or_none()

        if prospect is None:
            logger.warning("[webhook] prospect_id=%s not found in DB", payload.prospect_id)
            raise HTTPException(status_code=404, detail=f"Prospect {payload.prospect_id} not found")

        if payload.scores:
            # Map known score keys from audit_scores.json → Prospect columns
            if "geo_visibility_score" in payload.scores:
                prospect.geo_visibility_score = int(payload.scores["geo_visibility_score"])
            if "opportunity_score" in payload.scores:
                prospect.opportunity_score = int(payload.scores["opportunity_score"])
            if "ai_citation_score" in payload.scores:
                prospect.ai_citation_score = int(payload.scores["ai_citation_score"])

            prospect.status = "scored"
            await session.commit()
            logger.info(
                "[webhook] Updated scores for prospect_id=%s | status=scored",
                payload.prospect_id,
            )

        updated_prospect_id = payload.prospect_id

    return {"received": True, "prospect_id": updated_prospect_id}
