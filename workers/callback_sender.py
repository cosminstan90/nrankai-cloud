"""
Callback Sender — POST rezultatele unui prospect la callback_url cu retry logic.

Retry: maxim 3 incercari, backoff exponential [5, 15, 45] secunde.
Fiecare attempt e logat in CallbackLog.
"""

import asyncio
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import CallbackLog, Prospect

logger = logging.getLogger(__name__)

TIMEOUT = 15.0
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = [5, 15, 45]


def _build_payload(prospect: Prospect) -> dict:
    """Construieste payload-ul complet pentru callback."""
    return {
        "prospect_id": prospect.id,
        "campaign_id": prospect.campaign_id,
        "business_name": prospect.business_name,
        "business_category": prospect.business_category,
        "url": prospect.url,
        "location_city": prospect.location_city,
        "location_state": prospect.location_state,
        "segment": prospect.segment,
        "opportunity_score": prospect.opportunity_score,
        "email_address": prospect.email_address,
        "phone": prospect.phone,
        "google_rating": prospect.google_rating,
        "review_count": prospect.review_count,
        "gap_report_text": prospect.gap_report_text,
        "competitors_found": prospect.competitors_found,
        "top_issues": prospect.top_issues,
        "mobile_score": prospect.mobile_score,
        "has_schema": None,  # nu exista camp direct in DB
        "is_old_site": prospect.is_old_site,
        "stack": prospect.stack,
        "design_score": prospect.design_score,
        "ai_citation_score": prospect.ai_citation_score,
        "opportunity_confirmed": (
            prospect.ai_citation_score == 0
            if prospect.ai_citation_score is not None
            else None
        ),
    }


async def send_callback(prospect_id: int, db: AsyncSession) -> bool:
    """
    Trimite datele prospectului la callback_url cu retry logic.

    Args:
        prospect_id: ID-ul prospectului din DB.
        db: Sesiunea SQLAlchemy activa (poate fi request-scoped sau din AsyncSessionLocal).

    Returns:
        True daca cel putin un attempt a returnat HTTP 2xx, False altfel.
    """
    # ── 1. Citeste prospect ───────────────────────────────────────────────────
    result = await db.execute(select(Prospect).where(Prospect.id == prospect_id))
    prospect = result.scalar_one_or_none()

    if prospect is None:
        logger.warning("send_callback: prospect %d not found", prospect_id)
        return False

    if not prospect.callback_url:
        logger.info("send_callback: prospect %d has no callback_url, skipping", prospect_id)
        return False

    # ── 2. Build payload ──────────────────────────────────────────────────────
    payload = _build_payload(prospect)

    # ── 3. Retry loop ─────────────────────────────────────────────────────────
    for attempt in range(1, MAX_ATTEMPTS + 1):
        response_status = None
        response_body = None
        success = False

        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                resp = await client.post(
                    prospect.callback_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
            response_status = resp.status_code
            response_body = resp.text[:2000]
            success = resp.status_code < 300

            if success:
                logger.info(
                    "Callback OK — prospect=%d attempt=%d/%d status=%d url=%s",
                    prospect_id, attempt, MAX_ATTEMPTS, resp.status_code, prospect.callback_url,
                )
            else:
                logger.warning(
                    "Callback HTTP %d — prospect=%d attempt=%d/%d url=%s",
                    resp.status_code, prospect_id, attempt, MAX_ATTEMPTS, prospect.callback_url,
                )

        except httpx.TimeoutException:
            response_body = "timeout"
            logger.warning(
                "Callback timeout — prospect=%d attempt=%d/%d url=%s",
                prospect_id, attempt, MAX_ATTEMPTS, prospect.callback_url,
            )
        except Exception as exc:
            response_body = str(exc)[:2000]
            logger.error(
                "Callback error — prospect=%d attempt=%d/%d: %s",
                prospect_id, attempt, MAX_ATTEMPTS, exc,
            )

        # Log attempt
        log_entry = CallbackLog(
            prospect_id=prospect_id,
            campaign_id=(prospect.campaign_id or "").split("::")[0],
            callback_url=prospect.callback_url,
            payload=payload,
            response_status=response_status,
            response_body=response_body,
            attempt=attempt,
            sent_at=datetime.now(timezone.utc),
            success=success,
        )
        db.add(log_entry)
        await db.commit()

        if success:
            return True

        # Backoff inainte de urmatoarea incercare
        if attempt < MAX_ATTEMPTS:
            wait = BACKOFF_SECONDS[attempt - 1]
            logger.info(
                "Callback retry in %ds — prospect=%d attempt=%d/%d",
                wait, prospect_id, attempt, MAX_ATTEMPTS,
            )
            await asyncio.sleep(wait)

    logger.error(
        "Callback failed after %d attempts — prospect=%d url=%s",
        MAX_ATTEMPTS, prospect_id, prospect.callback_url,
    )
    return False
