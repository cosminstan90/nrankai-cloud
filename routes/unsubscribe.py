"""
CAN-SPAM compliant unsubscribe flow.

All public endpoints (/unsubscribe GET/POST) require no auth — they are
accessed by real humans clicking links in outreach emails.
"""

import hashlib
import hmac
import logging
import os
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from auth import require_n8n_key, require_worker_key
from database import AsyncSessionLocal, Prospect, Unsubscribe

logger = logging.getLogger(__name__)
router = APIRouter()
_templates = Jinja2Templates(directory="templates")


# ── Token helpers (email-keyed, same HMAC approach as tracking.py) ────────────

def generate_unsubscribe_token(email: str) -> str:
    """HMAC-SHA256 token keyed from CLOUD_WEBHOOK_SECRET + email address."""
    secret = os.environ["CLOUD_WEBHOOK_SECRET"]  # guaranteed set at startup by main.py
    return hmac.new(secret.encode(), email.lower().encode(), hashlib.sha256).hexdigest()[:16]


def verify_unsubscribe_token(email: str, token: str) -> bool:
    expected = generate_unsubscribe_token(email)
    return hmac.compare_digest(expected, token)


# ── 1. GET /unsubscribe — show form ──────────────────────────────────────────

@router.get("/unsubscribe", response_class=HTMLResponse, include_in_schema=False)
async def unsubscribe_page(
    request: Request,
    email: str = Query(""),
    token: str = Query(""),
):
    """
    Renders the unsubscribe form.
    - Invalid / missing token → error page (no form shown).
    - Valid token → form pre-filled with email (readonly).
    """
    if not email or not token or not verify_unsubscribe_token(email, token):
        return _templates.TemplateResponse(
            "unsubscribe.html",
            {"request": request, "error": True, "email": "", "token": ""},
            status_code=400,
        )

    return _templates.TemplateResponse(
        "unsubscribe.html",
        {"request": request, "error": False, "email": email, "token": token},
    )


# ── 2. POST /unsubscribe — process form submission ───────────────────────────

@router.post("/unsubscribe", response_class=HTMLResponse, include_in_schema=False)
async def unsubscribe_submit(
    request: Request,
    email: str = Form(...),
    token: str = Form(...),
    reason: str = Form(""),
):
    """
    Process unsubscribe form.  On success shows confirmation page.
    On invalid token returns the error variant of the form (HTTP 400).
    """
    if not verify_unsubscribe_token(email, token):
        return _templates.TemplateResponse(
            "unsubscribe.html",
            {"request": request, "error": True, "email": "", "token": ""},
            status_code=400,
        )

    ip = request.client.host if request.client else None

    try:
        async with AsyncSessionLocal() as session:
            # INSERT OR IGNORE via IntegrityError catch
            from sqlalchemy.exc import IntegrityError

            unsub = Unsubscribe(
                email_address=email.lower().strip(),
                reason=reason.strip() or None,
                ip_address=ip,
            )
            session.add(unsub)
            try:
                await session.flush()
            except IntegrityError:
                await session.rollback()
                # Already unsubscribed — still show confirmation
            else:
                await session.commit()

            # Mark all matching prospects as unsubscribed
            async with AsyncSessionLocal() as session2:
                result = await session2.execute(
                    select(Prospect).where(
                        Prospect.email_address == email.lower().strip()
                    )
                )
                prospects = result.scalars().all()
                for p in prospects:
                    p.status = "unsubscribed"
                    p.updated_at = datetime.now(timezone.utc)
                if prospects:
                    await session2.commit()
                    logger.info(
                        "Unsubscribed %s — %d prospect(s) updated",
                        email, len(prospects),
                    )

    except Exception as exc:
        logger.error("unsubscribe_submit error for %s: %s", email, exc)

    return _templates.TemplateResponse(
        "unsubscribe_confirmed.html",
        {"request": request, "email": email},
    )


# ── 3. GET /unsubscribe/check  [require_n8n_key] ─────────────────────────────

@router.get("/unsubscribe/check")
async def check_unsubscribed(
    email: str = Query(..., description="Email address to check"),
    _: None = Depends(require_n8n_key),
):
    """Return whether an email address is on the unsubscribe list."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Unsubscribe).where(
                Unsubscribe.email_address == email.lower().strip()
            )
        )
        is_unsub = result.scalar_one_or_none() is not None

    return {"email": email, "is_unsubscribed": is_unsub}


# ── 4. POST /unsubscribe/generate-link  [require_worker_key] ─────────────────

@router.post("/unsubscribe/generate-link")
async def generate_unsubscribe_link(
    body: dict,
    _: None = Depends(require_worker_key),
):
    """
    Generate a signed unsubscribe URL for use in outreach emails.

    Body: { "prospect_id": int, "email": str }
    """
    email = body.get("email", "").strip()
    prospect_id = body.get("prospect_id")

    if not email:
        raise HTTPException(status_code=422, detail="'email' is required.")
    if not isinstance(prospect_id, int) or prospect_id <= 0:
        raise HTTPException(status_code=422, detail="'prospect_id' must be a positive integer.")

    base_url = os.getenv("BASE_URL", "https://api.nrankai.com").rstrip("/")
    token = generate_unsubscribe_token(email)
    encoded_email = quote(email, safe="")

    url = f"{base_url}/unsubscribe?email={encoded_email}&token={token}"

    return {
        "prospect_id": prospect_id,
        "email": email,
        "unsubscribe_url": url,
    }
