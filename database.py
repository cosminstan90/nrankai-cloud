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
# Imports needed for Column-style ORM (compatible with existing Mapped-style models)
from sqlalchemy import Column, Float, Boolean, JSON, Enum as SAEnum, ForeignKey  # noqa: E402


class Prospect(Base):
    __tablename__ = "prospects"

    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(String(50), index=True)
    url = Column(String(500))
    business_name = Column(String(200))
    business_category = Column(String(100))
    location_city = Column(String(100))
    location_state = Column(String(50))
    google_place_id = Column(String(200), unique=True, index=True)
    phone = Column(String(50), nullable=True)
    email_address = Column(String(200), nullable=True)
    google_rating = Column(Float, nullable=True)
    review_count = Column(Integer, nullable=True)
    has_website = Column(Boolean, default=True)
    opportunity_score = Column(Integer, default=0)
    design_score = Column(Integer, nullable=True)
    geo_visibility_score = Column(Integer, nullable=True)
    ai_citation_score = Column(Integer, nullable=True)
    stack = Column(String(50), nullable=True)
    is_old_site = Column(Boolean, nullable=True)
    mobile_score = Column(Integer, nullable=True)
    top_issues = Column(JSON, nullable=True)
    segment = Column(String(50), nullable=True)
    status = Column(
        SAEnum(
            "pending", "processing", "scored", "contacted", "replied", "booked",
            name="prospect_status",
        ),
        default="pending",
    )
    gap_report_text = Column(Text, nullable=True)
    competitors_found = Column(JSON, nullable=True)
    ai_queries_run = Column(JSON, nullable=True)
    gap_report_generated_at = Column(DateTime, nullable=True)
    callback_url = Column(String(500), nullable=True)
    email_sent_at = Column(DateTime, nullable=True)
    subject_used = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    processed_at = Column(DateTime, nullable=True)


class EmailTemplate(Base):
    __tablename__ = "email_templates"

    id = Column(Integer, primary_key=True)
    segment = Column(String(50), unique=True, index=True)
    subject = Column(Text)
    body_html = Column(Text)
    body_text = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


class CallbackLog(Base):
    __tablename__ = "callback_log"

    id = Column(Integer, primary_key=True)
    prospect_id = Column(Integer, ForeignKey("prospects.id"))
    campaign_id = Column(String(50))
    callback_url = Column(String(500))
    payload = Column(JSON)
    response_status = Column(Integer, nullable=True)
    response_body = Column(Text, nullable=True)
    attempt = Column(Integer, default=1)
    sent_at = Column(DateTime, default=datetime.utcnow)
    success = Column(Boolean, default=False)
