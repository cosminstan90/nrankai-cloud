import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware

from database import AsyncSessionLocal, LeadAuditJob, init_db
from routes.lead_audits import router as lead_audits_router
from tasks import stale_job_recovery_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

BASE_URL = os.environ.get("BASE_URL", "https://api.nrankai.com")
SHOW_DOCS = os.environ.get("SHOW_DOCS", "false").lower() == "true"

# ── Rate limiter ──────────────────────────────────────────────────────────────
# Uses X-Forwarded-For when running behind nginx (set FORWARDED_ALLOW_IPS=127.0.0.1)

def _real_ip(request: Request) -> str:
    """Extract real client IP — respects X-Real-IP set by nginx."""
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    return get_remote_address(request)


limiter = Limiter(key_func=_real_ip)


# ── Security headers middleware ───────────────────────────────────────────────

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        # Remove server fingerprinting
        if "server" in response.headers:
            del response.headers["server"]
        return response


# ── CORS origins ──────────────────────────────────────────────────────────────

_ALLOWED_ORIGINS = [o.strip() for o in os.environ.get(
    "ALLOWED_ORIGINS",
    "https://nrankai.com,https://www.nrankai.com,https://api.nrankai.com,https://app.nrankai.com",
).split(",") if o.strip()]


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logger.info("Database initialised")
    task = asyncio.create_task(stale_job_recovery_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="nrankai.com — Lead Audit API",
    version="1.0.0",
    docs_url="/api/docs" if SHOW_DOCS else None,
    redoc_url=None,
    openapi_url="/api/openapi.json" if SHOW_DOCS else None,
    lifespan=lifespan,
)

# Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Security headers
app.add_middleware(SecurityHeadersMiddleware)

# CORS — only allow our own origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(lead_audits_router, prefix="/api")


# ── Landing page ──────────────────────────────────────────────────────────────

templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def landing_page(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


# ── Public report page ────────────────────────────────────────────────────────


@app.get("/reports/{token}", response_class=HTMLResponse, include_in_schema=False)
async def public_report(request: Request, token: str):
    # Validate token format — 12 base64url chars, no path traversal
    if not token or len(token) > 20 or not token.replace("-", "").replace("_", "").isalnum():
        return templates.TemplateResponse(
            "report_not_found.html", {"request": request}, status_code=404
        )

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(LeadAuditJob).where(LeadAuditJob.public_token == token)
        )
        job = result.scalar_one_or_none()

    if job is None:
        return templates.TemplateResponse(
            "report_not_found.html", {"request": request}, status_code=404
        )

    now = datetime.now(timezone.utc)
    if job.expires_at and job.expires_at.replace(tzinfo=timezone.utc) < now:
        return templates.TemplateResponse(
            "report_expired.html",
            {"request": request, "website": job.website},
            status_code=410,
        )

    if job.status in ("pending", "running"):
        return templates.TemplateResponse(
            "report_pending.html",
            {"request": request, "website": job.website},
        )

    if job.status == "failed" and not job.result:
        return templates.TemplateResponse(
            "report_error.html",
            {"request": request, "website": job.website},
            status_code=503,
        )

    result_data = job.result or {}
    parsed = urlparse(job.website)
    website_display = parsed.netloc or job.website

    scores = result_data.get("scores", {})
    geo_score = scores.get("geo_score", 0)

    if geo_score <= 30:
        score_color = "#EF4444"
        score_label = "Poor"
    elif geo_score <= 60:
        score_color = "#F97316"
        score_label = "Needs Improvement"
    elif geo_score <= 80:
        score_color = "#84CC16"
        score_label = "Good"
    else:
        score_color = "#22C55E"
        score_label = "Excellent"

    arc_pct = geo_score / 100
    lead = job.lead
    language = lead.get("language", "en")

    return templates.TemplateResponse(
        "report.html",
        {
            "request": request,
            "website": job.website,
            "website_display": website_display,
            "scores": scores,
            "geo_score": geo_score,
            "score_color": score_color,
            "score_label": score_label,
            "arc_pct": arc_pct,
            "top_issues": result_data.get("top_issues", []),
            "quick_wins": result_data.get("quick_wins", []),
            "summary": result_data.get("summary", {}),
            "industry_benchmark": scores.get("industry_benchmark", 61),
            "gap": scores.get("gap", 0),
            "pages_analyzed": scores.get("pages_analyzed", 1),
            "language": language,
            "generated_date": job.completed_at.strftime("%B %d, %Y") if job.completed_at else "",
            "expires_date": job.expires_at.strftime("%B %d, %Y") if job.expires_at else "",
        },
    )


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/api/health", include_in_schema=False)
async def health():
    return {"status": "ok", "service": "nrankai-lead-audit"}
