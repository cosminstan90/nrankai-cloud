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
