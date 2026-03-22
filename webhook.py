import json
import logging
import os
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

CLOUD_WEBHOOK_SECRET = os.environ.get("CLOUD_WEBHOOK_SECRET", "")


async def fire_n8n_webhook(webhook_url: str, payload: dict) -> bool:
    """
    POST the payload to the n8n webhook URL.
    Returns True if the webhook was delivered (2xx), False otherwise.
    Never raises — failures are logged and swallowed.
    """
    try:
        headers = {
            "Content-Type": "application/json",
            "X-Cloud-Secret": CLOUD_WEBHOOK_SECRET,
            "User-Agent": "nrankai-cloud/1.0",
        }
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.post(webhook_url, json=payload, headers=headers)
            if r.status_code < 300:
                logger.info("Webhook delivered to %s → %s", webhook_url, r.status_code)
                return True
            else:
                logger.warning(
                    "Webhook to %s returned non-2xx: %s %s",
                    webhook_url, r.status_code, r.text[:200],
                )
                return False
    except Exception as exc:
        logger.error("Webhook to %s failed: %s", webhook_url, exc)
        return False


def build_completed_payload(job) -> dict:
    result = job.result or {}
    return {
        "event": "lead_audit.completed",
        "job_id": job.id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "lead": job.lead,
        "website": job.website,
        "report_url": job.report_url(os.environ.get("BASE_URL", "")),
        "status_url": job.status_url(os.environ.get("BASE_URL", "")),
        "scores": result.get("scores"),
        "top_issues": result.get("top_issues"),
        "quick_wins": result.get("quick_wins"),
        "summary": result.get("summary"),
        "email_ready": result.get("email_ready"),
    }


def build_failed_payload(job) -> dict:
    return {
        "event": "lead_audit.failed",
        "job_id": job.id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "lead": job.lead,
        "website": job.website,
        "error": job.error,
    }
