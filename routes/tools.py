import logging

from fastapi import APIRouter, Depends, HTTPException

from auth import require_worker_key
from workers.site_design_detector import detect

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/site-design")
async def site_design_check(
    payload: dict,
    _: None = Depends(require_worker_key),
) -> dict:
    """
    Analyse a URL technically (stack, SSL, schema, mobile score, etc.).

    Returns a design/quality snapshot suitable for prospect scoring.
    No LLM calls — purely HTTP + HTML parsing.
    """
    url = payload.get("url")

    if not url or not isinstance(url, str) or not url.strip():
        raise HTTPException(
            status_code=422,
            detail="'url' field is required and must be a non-empty string.",
        )

    url = url.strip()

    # Basic scheme sanity check — detect() normalises further
    if not url.startswith(("http://", "https://", "www.")):
        # Allow bare domains like "example.com"
        if " " in url or url.startswith("/"):
            raise HTTPException(
                status_code=422,
                detail=f"'{url}' does not look like a valid URL.",
            )

    try:
        result = await detect(url)
    except Exception as exc:
        logger.exception("Unexpected error in site_design_check for %s", url)
        raise HTTPException(
            status_code=500,
            detail=f"Site analysis failed unexpectedly: {exc}",
        ) from exc

    return result
