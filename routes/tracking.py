"""
Email open & click tracking.

Endpoints are intentionally short (/t/o/... and /t/c/...) because they
appear inside email bodies where URL length matters.

All tracking endpoints are public (no auth) — they are called by email
clients and links clicked by prospects.  The HMAC token prevents
third-party enumeration of prospect IDs.
"""

import hashlib
import hmac
import logging
import os
from datetime import datetime, timezone
from urllib.parse import quote, unquote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select

from auth import require_worker_key
from database import AsyncSessionLocal, EmailTrackingEvent, Prospect

logger = logging.getLogger(__name__)
router = APIRouter()

# 1×1 transparent GIF — returned for every open-pixel request regardless of
# token validity so email clients never show a broken image.
_GIF_BYTES = (
    b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00'
    b'\xff\xff\xff\x00\x00\x00\x21\xf9\x04\x00\x00\x00\x00\x00'
    b'\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b'
)
_GIF_RESPONSE = Response(
    content=_GIF_BYTES,
    media_type="image/gif",
    headers={
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "Pragma": "no-cache",
    },
)


# ── Token helpers ─────────────────────────────────────────────────────────────

def generate_tracking_token(prospect_id: int) -> str:
    """HMAC-SHA256 token derived from CLOUD_WEBHOOK_SECRET + prospect_id."""
    secret = os.getenv("CLOUD_WEBHOOK_SECRET", "default-secret")
    return hmac.new(secret.encode(), str(prospect_id).encode(), hashlib.sha256).hexdigest()[:16]


def verify_tracking_token(prospect_id: int, token: str) -> bool:
    expected = generate_tracking_token(prospect_id)
    return hmac.compare_digest(expected, token)


# ── 1. GET /t/o/{prospect_id}/{token} — open pixel ───────────────────────────

@router.get("/t/o/{prospect_id}/{token}", include_in_schema=False)
async def open_pixel(prospect_id: int, token: str, request: Request):
    """
    1×1 tracking pixel embedded in outreach emails.

    Always returns a GIF — valid or not — so email clients never show an
    error.  Only logs when the token is valid to avoid spam from crawlers.
    """
    if not verify_tracking_token(prospect_id, token):
        return _GIF_RESPONSE

    ip = request.client.host if request.client else None
    user_agent = request.headers.get("User-Agent")

    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Prospect).where(Prospect.id == prospect_id)
            )
            prospect = result.scalar_one_or_none()
            if prospect is None:
                return _GIF_RESPONSE

            now = datetime.now(timezone.utc)

            session.add(EmailTrackingEvent(
                prospect_id=prospect_id,
                event_type="open",
                ip_address=ip,
                user_agent=user_agent,
            ))

            prospect.open_count = (prospect.open_count or 0) + 1
            if prospect.first_opened_at is None:
                prospect.first_opened_at = now
            prospect.last_opened_at = now
            prospect.updated_at = now

            await session.commit()

    except Exception as exc:
        logger.error("open_pixel error for prospect %d: %s", prospect_id, exc)

    return _GIF_RESPONSE


# ── 2. GET /t/c/{prospect_id}/{token} — click redirect ───────────────────────

@router.get("/t/c/{prospect_id}/{token}", include_in_schema=False)
async def click_redirect(
    prospect_id: int,
    token: str,
    request: Request,
    url: str = Query(..., description="Destination URL (URL-encoded)"),
):
    """
    Click-tracking redirect embedded in outreach emails.

    Always redirects to *url* — valid token or not — so broken links never
    reach the prospect.  Only logs when the token is valid.
    """
    destination = unquote(url)

    # Safety: only redirect to http(s) URLs
    if not destination.startswith(("http://", "https://")):
        destination = "https://" + destination

    if verify_tracking_token(prospect_id, token):
        ip = request.client.host if request.client else None
        user_agent = request.headers.get("User-Agent")

        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(Prospect).where(Prospect.id == prospect_id)
                )
                prospect = result.scalar_one_or_none()
                if prospect is not None:
                    session.add(EmailTrackingEvent(
                        prospect_id=prospect_id,
                        event_type="click",
                        link_url=destination,
                        ip_address=ip,
                        user_agent=user_agent,
                    ))

                    prospect.click_count = (prospect.click_count or 0) + 1
                    prospect.updated_at = datetime.now(timezone.utc)

                    await session.commit()

        except Exception as exc:
            logger.error("click_redirect error for prospect %d: %s", prospect_id, exc)

    return RedirectResponse(url=destination, status_code=302)


# ── 3. POST /tracking/generate-links  [require_worker_key] ───────────────────

@router.post("/tracking/generate-links")
async def generate_links(
    body: dict,
    _: None = Depends(require_worker_key),
):
    """
    Generate a tracking pixel and wrapped tracked links for a prospect.

    Body: { "prospect_id": int, "links": [{"label": str, "url": str}] }
    """
    prospect_id = body.get("prospect_id")
    links = body.get("links", [])

    if not isinstance(prospect_id, int) or prospect_id <= 0:
        raise HTTPException(status_code=422, detail="'prospect_id' must be a positive integer.")
    if not isinstance(links, list):
        raise HTTPException(status_code=422, detail="'links' must be a list.")

    base_url = os.getenv("BASE_URL", "https://api.nrankai.com").rstrip("/")
    token = generate_tracking_token(prospect_id)

    pixel_html = (
        f"<img src='{base_url}/t/o/{prospect_id}/{token}' "
        f"width='1' height='1' style='display:none;' />"
    )

    tracked_links = []
    for item in links:
        label = item.get("label", "")
        original_url = item.get("url", "")
        if not original_url:
            continue
        encoded_url = quote(original_url, safe="")
        tracked_url = f"{base_url}/t/c/{prospect_id}/{token}?url={encoded_url}"
        tracked_links.append({
            "label": label,
            "original": original_url,
            "tracked": tracked_url,
        })

    return {
        "prospect_id": prospect_id,
        "pixel_html": pixel_html,
        "tracked_links": tracked_links,
    }
