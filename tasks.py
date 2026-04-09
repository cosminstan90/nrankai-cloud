import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from database import AsyncSessionLocal, LeadAuditJob

logger = logging.getLogger(__name__)

STALE_AFTER_MINUTES = 10
CHECK_INTERVAL_SECONDS = 60
MAX_RETRIES = 3


async def reset_stale_jobs() -> None:
    """
    Find jobs stuck in 'running' for more than STALE_AFTER_MINUTES and
    reset them to 'pending' (up to MAX_RETRIES), then mark as 'failed'.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=STALE_AFTER_MINUTES)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(LeadAuditJob).where(
                LeadAuditJob.status == "running",
                LeadAuditJob.picked_up_at < cutoff,
            )
        )
        stale = result.scalars().all()

        if not stale:
            return

        logger.warning("Found %d stale job(s) — resetting", len(stale))

        for job in stale:
            if job.retry_count >= MAX_RETRIES:
                job.status = "failed"
                job.error_json = json.dumps(
                    {
                        "code": "max_retries_exceeded",
                        "message": f"Job failed after {MAX_RETRIES} attempts",
                        "retryable": False,
                    }
                )
                job.completed_at = datetime.now(timezone.utc)
                logger.error("Job %s permanently failed (max retries)", job.id)
            else:
                job.status = "pending"
                job.retry_count += 1
                job.picked_up_at = None
                logger.info(
                    "Job %s reset to pending (attempt %d/%d)",
                    job.id, job.retry_count, MAX_RETRIES,
                )

        await session.commit()


async def stale_job_recovery_loop() -> None:
    """Background loop — runs forever, checks every CHECK_INTERVAL_SECONDS."""
    logger.info("Stale-job recovery loop started (interval=%ds)", CHECK_INTERVAL_SECONDS)
    while True:
        try:
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)
            await reset_stale_jobs()
        except asyncio.CancelledError:
            logger.info("Stale-job recovery loop cancelled")
            return
        except Exception as exc:
            logger.error("Stale-job recovery error: %s", exc)


# ── Prospect batch processor ───────────────────────────────────────────────────

async def _score_single_prospect(prospect_id: int, campaign_id: str) -> None:
    """Score one prospect: detect → assign scores & segment → update DB."""
    from workers.site_design_detector import detect  # lazy import
    from workers.callback_sender import send_callback  # lazy import
    from database import Prospect
    from sqlalchemy import select as _select

    async with AsyncSessionLocal() as db:
        res = await db.execute(_select(Prospect).where(Prospect.id == prospect_id))
        prospect = res.scalar_one_or_none()
        if prospect is None:
            return

        # ── 1. Mark processing ────────────────────────────────────────────────
        prospect.status = "processing"
        await db.commit()

        try:
            # ── 2. Site design detection ──────────────────────────────────────
            detection: dict = {}
            if prospect.url:
                try:
                    detection = await detect(prospect.url)
                except Exception as exc:
                    logger.warning(
                        "detect() failed for prospect %d (%s): %s",
                        prospect_id, prospect.url, exc,
                    )

            design_score: int = detection.get("design_score", 0)
            mobile_score = detection.get("mobile_score")
            is_old_site = detection.get("is_old_site", False)
            stack = detection.get("stack", "unknown")
            has_schema: bool = detection.get("has_schema", False)

            # ── 3. Opportunity score ──────────────────────────────────────────
            geo_vis = prospect.geo_visibility_score  # may be None (not yet set)
            opportunity_score = round(
                design_score * 0.4 + (100 - (geo_vis if geo_vis is not None else 50)) * 0.6
            )
            opportunity_score = max(0, min(100, opportunity_score))

            # ── 4. Segment assignment (priority order) ────────────────────────
            ai_cit = prospect.ai_citation_score
            google_rating = prospect.google_rating
            segment: str

            if ai_cit is not None and ai_cit == 0:
                segment = "no_geo_presence"
            elif (
                mobile_score is not None
                and mobile_score > 70
                and (geo_vis if geo_vis is not None else 100) < 35
            ):
                segment = "good_site_bad_geo"
            elif is_old_site or (mobile_score is not None and mobile_score < 50):
                segment = "old_site"
            elif not has_schema:
                segment = "no_schema"
            elif google_rating is not None and google_rating < 4.0:
                segment = "low_rating"
            else:
                segment = "general"

            # ── 5. Persist scores ─────────────────────────────────────────────
            from datetime import datetime as _dt, timezone as _tz
            prospect.design_score = design_score
            prospect.mobile_score = mobile_score
            prospect.is_old_site = is_old_site
            prospect.stack = stack
            prospect.opportunity_score = opportunity_score
            prospect.segment = segment
            prospect.top_issues = detection.get("top_issues")

            # ── 6. Mark scored ────────────────────────────────────────────────
            prospect.status = "scored"
            prospect.processed_at = _dt.now(_tz.utc)
            await db.commit()

        except Exception as exc:
            logger.error("Error scoring prospect %d: %s", prospect_id, exc)
            prospect.status = "pending"  # reset so it can be retried
            await db.commit()
            return

        # ── 7. Skip if email is unsubscribed ─────────────────────────────────
        if prospect.email_address:
            from database import Unsubscribe
            unsub_res = await db.execute(
                _select(Unsubscribe).where(
                    Unsubscribe.email_address == prospect.email_address.lower().strip()
                )
            )
            if unsub_res.scalar_one_or_none() is not None:
                prospect.status = "unsubscribed"
                await db.commit()
                logger.info(
                    "Prospect %d skipped — email %s is unsubscribed",
                    prospect_id, prospect.email_address,
                )
                return

        # ── 8. Fire callback if configured ───────────────────────────────────
        if prospect.callback_url:
            try:
                await send_callback(prospect_id, db)
            except Exception as exc:
                logger.error("Callback failed for prospect %d: %s", prospect_id, exc)


async def process_prospects_batch(job_id: str, campaign_id: str) -> None:
    """
    Background task: score all pending prospects for a bulk intake job.
    Processes up to 5 prospects concurrently.
    """
    from database import Prospect
    from sqlalchemy import select as _select

    logger.info("process_prospects_batch started: job_id=%s campaign=%s", job_id, campaign_id)

    composite = f"{campaign_id}::{job_id}"

    async with AsyncSessionLocal() as db:
        res = await db.execute(
            _select(Prospect.id).where(
                Prospect.campaign_id == composite,
                Prospect.status == "pending",
            )
        )
        ids = [row[0] for row in res.all()]

    if not ids:
        logger.info("process_prospects_batch: no pending prospects for job %s", job_id)
        return

    logger.info("process_prospects_batch: %d prospects to score", len(ids))

    # Process in chunks of 5
    chunk_size = 5
    for i in range(0, len(ids), chunk_size):
        chunk = ids[i: i + chunk_size]
        await asyncio.gather(
            *[_score_single_prospect(pid, campaign_id) for pid in chunk],
            return_exceptions=True,
        )

    logger.info("process_prospects_batch finished: job_id=%s", job_id)
