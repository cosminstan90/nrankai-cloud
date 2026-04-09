import uuid
import json
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import String, Text, Integer, DateTime, Index
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

DATABASE_URL = "sqlite+aiosqlite:///./lead_audits.db"

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class LeadAuditJob(Base):
    __tablename__ = "lead_audit_jobs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    status: Mapped[str] = mapped_column(String(20), default="pending")
    website: Mapped[str] = mapped_column(String(512))

    # Stored as JSON text
    lead_json: Mapped[str] = mapped_column(Text)
    options_json: Mapped[str] = mapped_column(Text)

    # Report link
    public_token: Mapped[str] = mapped_column(String(20), unique=True)

    # n8n callback
    n8n_webhook: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    picked_up_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Worker data
    result_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    local_audit_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    # Set to 1 after n8n has been notified (prevents duplicate approval emails)
    n8n_notified: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    __table_args__ = (
        Index("ix_lead_audit_jobs_status", "status"),
        Index("ix_lead_audit_jobs_public_token", "public_token"),
        Index("ix_lead_audit_jobs_n8n_notified", "n8n_notified"),
    )

    # ── helpers ──────────────────────────────────────────────────────────

    @property
    def lead(self) -> dict:
        return json.loads(self.lead_json)

    @property
    def options(self) -> dict:
        return json.loads(self.options_json)

    @property
    def result(self) -> Optional[dict]:
        return json.loads(self.result_json) if self.result_json else None

    @property
    def error(self) -> Optional[dict]:
        return json.loads(self.error_json) if self.error_json else None

    def report_url(self, base_url: str) -> str:
        return f"{base_url}/reports/{self.public_token}"

    def status_url(self, base_url: str) -> str:
        return f"{base_url}/api/lead-audits/{self.id}/status"


async def get_session():
    async with AsyncSessionLocal() as session:
        yield session


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# ── Prospect pipeline models ───────────────────────────────────────────────────
from sqlalchemy import Boolean, Float, ForeignKey, JSON  # noqa: E402


class Prospect(Base):
    __tablename__ = "prospects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[Optional[str]] = mapped_column(String(50), index=True, nullable=True)
    url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    business_name: Mapped[str] = mapped_column(String(200))
    business_category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    location_city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    location_state: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    google_place_id: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    email_address: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    google_rating: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    review_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    has_website: Mapped[Optional[bool]] = mapped_column(Boolean, default=True)
    opportunity_score: Mapped[int] = mapped_column(Integer, default=0)
    design_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    geo_visibility_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ai_citation_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    stack: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    is_old_site: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    mobile_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    top_issues: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    segment: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # pending / processing / scored / contacted / replied / booked / unsubscribed
    status: Mapped[str] = mapped_column(String(20), default="pending")
    gap_report_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    competitors_found: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    ai_queries_run: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    gap_report_generated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    callback_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    email_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    subject_used: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    open_count: Mapped[int] = mapped_column(Integer, default=0)
    click_count: Mapped[int] = mapped_column(Integer, default=0)
    first_opened_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_opened_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class EmailTemplate(Base):
    __tablename__ = "email_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    segment: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    subject: Mapped[str] = mapped_column(Text)
    body_html: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    body_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


class CallbackLog(Base):
    __tablename__ = "callback_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prospect_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("prospects.id"), nullable=True
    )
    campaign_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    callback_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    response_status: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    response_body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    success: Mapped[bool] = mapped_column(Boolean, default=False)


class EmailTrackingEvent(Base):
    __tablename__ = "email_tracking_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prospect_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("prospects.id"), index=True, nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(20))
    link_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


class Unsubscribe(Base):
    __tablename__ = "unsubscribes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email_address: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    prospect_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("prospects.id"), nullable=True
    )
    reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
