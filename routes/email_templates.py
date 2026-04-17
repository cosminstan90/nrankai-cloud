"""
Email Templates — CRUD + seed pentru template-urile de outreach per segment.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_n8n_key
from database import EmailTemplate, get_session

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Schemas locale ────────────────────────────────────────────────────────────

class TemplateUpsertBody(BaseModel):
    subject: str
    body_html: str
    body_text: str


# ── Template-uri default ──────────────────────────────────────────────────────

_DEFAULT_TEMPLATES: list[dict] = [
    {
        "segment": "no_geo_presence",
        "subject": "{{business_name}} is invisible to AI search — here's proof",
        "body_text": (
            "Hi,\n\n"
            "I ran a quick GEO (Generative Engine Optimization) check on {{business_name}} "
            "and found something worth flagging: when potential clients ask ChatGPT or Perplexity "
            "for {{business_category}} in {{city}}, you're not showing up — but {{competitor_1}} "
            "and {{competitor_2}} are.\n\n"
            "{{gap_report_text}}\n\n"
            "Would it be useful if I sent you a full visibility report showing exactly what's "
            "missing and how to fix it?\n\n"
            "Best, Cosmin | nrankai.com\n"
            "Book a call: {{booking_url}}"
        ),
        "body_html": (
            "<p>Hi,</p>"
            "<p>I ran a quick GEO (Generative Engine Optimization) check on <strong>{{business_name}}</strong> "
            "and found something worth flagging: when potential clients ask ChatGPT or Perplexity "
            "for {{business_category}} in {{city}}, you're not showing up — but <strong>{{competitor_1}}</strong> "
            "and <strong>{{competitor_2}}</strong> are.</p>"
            "<p>{{gap_report_text}}</p>"
            "<p>Would it be useful if I sent you a full visibility report showing exactly what's "
            "missing and how to fix it?</p>"
            "<p>Best, Cosmin | nrankai.com<br>"
            "Book a call: <a href=\"{{booking_url}}\">{{booking_url}}</a></p>"
        ),
    },
    {
        "segment": "old_site",
        "subject": "{{business_name}}'s website may be costing you clients",
        "body_text": (
            "Hi,\n\n"
            "I came across {{business_name}} and noticed your site has some technical issues "
            "that likely hurt both your Google rankings and your visibility in AI tools like "
            "ChatGPT. Specifically: {{top_issue_1}} and {{top_issue_2}}.\n\n"
            "{{gap_report_text}}\n\n"
            "Would a free audit showing the impact of these issues be helpful?\n\n"
            "Best, Cosmin | nrankai.com\n"
            "Book a call: {{booking_url}}"
        ),
        "body_html": (
            "<p>Hi,</p>"
            "<p>I came across <strong>{{business_name}}</strong> and noticed your site has some "
            "technical issues that likely hurt both your Google rankings and your visibility in "
            "AI tools like ChatGPT. Specifically: <em>{{top_issue_1}}</em> and "
            "<em>{{top_issue_2}}</em>.</p>"
            "<p>{{gap_report_text}}</p>"
            "<p>Would a free audit showing the impact of these issues be helpful?</p>"
            "<p>Best, Cosmin | nrankai.com<br>"
            "Book a call: <a href=\"{{booking_url}}\">{{booking_url}}</a></p>"
        ),
    },
    {
        "segment": "no_schema",
        "subject": "Quick fix that could put {{business_name}} in AI answers",
        "body_text": (
            "Hi,\n\n"
            "{{business_name}} has a solid site, but it's missing structured data — which is "
            "one of the main reasons businesses don't get cited when people ask AI assistants "
            "for recommendations in {{city}}.\n\n"
            "{{gap_report_text}}\n\n"
            "Want me to show you exactly what to add and what it would change?\n\n"
            "Best, Cosmin | nrankai.com\n"
            "Book a call: {{booking_url}}"
        ),
        "body_html": (
            "<p>Hi,</p>"
            "<p><strong>{{business_name}}</strong> has a solid site, but it's missing structured "
            "data — which is one of the main reasons businesses don't get cited when people ask "
            "AI assistants for recommendations in {{city}}.</p>"
            "<p>{{gap_report_text}}</p>"
            "<p>Want me to show you exactly what to add and what it would change?</p>"
            "<p>Best, Cosmin | nrankai.com<br>"
            "Book a call: <a href=\"{{booking_url}}\">{{booking_url}}</a></p>"
        ),
    },
    {
        "segment": "good_site_bad_geo",
        "subject": "{{business_name}} has a great site — but AI doesn't know you exist",
        "body_text": (
            "Hi,\n\n"
            "Your site loads fast and looks good, but when I checked how {{business_name}} "
            "appears in AI search results for {{business_category}} in {{city}}, you weren't "
            "showing up at all — even though {{competitor_1}} was.\n\n"
            "{{gap_report_text}}\n\n"
            "Interested in a quick breakdown of what's holding you back in AI visibility?\n\n"
            "Best, Cosmin | nrankai.com\n"
            "Book a call: {{booking_url}}"
        ),
        "body_html": (
            "<p>Hi,</p>"
            "<p>Your site loads fast and looks good, but when I checked how <strong>{{business_name}}</strong> "
            "appears in AI search results for {{business_category}} in {{city}}, you weren't "
            "showing up at all — even though <strong>{{competitor_1}}</strong> was.</p>"
            "<p>{{gap_report_text}}</p>"
            "<p>Interested in a quick breakdown of what's holding you back in AI visibility?</p>"
            "<p>Best, Cosmin | nrankai.com<br>"
            "Book a call: <a href=\"{{booking_url}}\">{{booking_url}}</a></p>"
        ),
    },
    # ── Dental outreach sequence (3 emails) ──────────────────────────────────
    {
        "segment": "dental_initial",
        "subject": "{{city}} dentist / ai search",
        "body_text": (
            "Dr. {{first_name}},\n\n"
            "I searched ChatGPT for '{{ai_query}}' — {{practice_name}} {{ai_result}}. "
            "{{ai_cited_instead}} came up instead.\n\n"
            "I dug a bit deeper and found the main issues: {{audit_finding_1}} and {{audit_finding_2}}.\n\n"
            "I put it all in a short report — no strings attached. Worth sending over?\n\n"
            "— Cosmin\n"
            "nrankai.com"
        ),
        "body_html": (
            "<p>Dr. {{first_name}},</p>"
            "<p>I searched ChatGPT for <em>'{{ai_query}}'</em> — "
            "<strong>{{practice_name}} {{ai_result}}</strong>. "
            "{{ai_cited_instead}} came up instead.</p>"
            "<p>I dug a bit deeper and found the main issues: "
            "{{audit_finding_1}} and {{audit_finding_2}}.</p>"
            "<p>I put it all in a short report — no strings attached. Worth sending over?</p>"
            "<p>— Cosmin<br>"
            "<a href=\"https://nrankai.com\">nrankai.com</a></p>"
        ),
    },
    {
        "segment": "dental_followup_1",
        "subject": "re: {{practice_name}}",
        "body_text": (
            "Dr. {{first_name}},\n\n"
            "Sent a note last week about {{practice_name}}'s visibility in AI search. "
            "Wanted to follow up with one specific data point:\n\n"
            "When a patient in {{city}} asks ChatGPT 'best dentist near me,' the practices "
            "that show up share one thing in common — their sites are structured so AI can "
            "read and cite them. Most dental sites aren't set up this way yet.\n\n"
            "The audit I ran on {{practice_name}} flagged exactly this. "
            "Happy to send it over if useful — takes 30 seconds to read.\n\n"
            "— Cosmin\n"
            "nrankai.com"
        ),
        "body_html": (
            "<p>Dr. {{first_name}},</p>"
            "<p>Sent a note last week about <strong>{{practice_name}}</strong>'s visibility "
            "in AI search. Wanted to follow up with one specific data point:</p>"
            "<p>When a patient in {{city}} asks ChatGPT <em>'best dentist near me,'</em> "
            "the practices that show up share one thing in common — their sites are structured "
            "so AI can read and cite them. Most dental sites aren't set up this way yet.</p>"
            "<p>The audit I ran on {{practice_name}} flagged exactly this. "
            "Happy to send it over if useful — takes 30 seconds to read.</p>"
            "<p>— Cosmin<br>"
            "<a href=\"https://nrankai.com\">nrankai.com</a></p>"
        ),
    },
    {
        "segment": "dental_followup_2",
        "subject": "closing the loop",
        "body_text": (
            "Dr. {{first_name}},\n\n"
            "Last one — I'll get out of your inbox after this.\n\n"
            "AI search is still early for dental practices, which means the window to get "
            "ahead of it is now, not in two years when everyone's doing it.\n\n"
            "If {{practice_name}}'s AI visibility ever becomes a priority, the audit report "
            "is waiting at nrankai.com — or just reply here.\n\n"
            "Best of luck,\n"
            "Cosmin\n"
            "nrankai.com"
        ),
        "body_html": (
            "<p>Dr. {{first_name}},</p>"
            "<p>Last one — I'll get out of your inbox after this.</p>"
            "<p>AI search is still early for dental practices, which means the window to get "
            "ahead of it is now, not in two years when everyone's doing it.</p>"
            "<p>If <strong>{{practice_name}}</strong>'s AI visibility ever becomes a priority, "
            "the audit report is waiting at "
            "<a href=\"https://nrankai.com\">nrankai.com</a> — or just reply here.</p>"
            "<p>Best of luck,<br>Cosmin<br>"
            "<a href=\"https://nrankai.com\">nrankai.com</a></p>"
        ),
    },
    {
        "segment": "low_rating",
        "subject": "One system that could improve {{business_name}}'s online reputation",
        "body_text": (
            "Hi,\n\n"
            "I noticed {{business_name}} has {{google_rating}} stars on Google. A simple "
            "automated review follow-up system can move that needle significantly — and it feeds "
            "directly into how AI tools rank and recommend local businesses.\n\n"
            "{{gap_report_text}}\n\n"
            "Would it be worth a 15-minute call to walk through how this works?\n\n"
            "Best, Cosmin | nrankai.com\n"
            "Book a call: {{booking_url}}"
        ),
        "body_html": (
            "<p>Hi,</p>"
            "<p>I noticed <strong>{{business_name}}</strong> has <strong>{{google_rating}}</strong> "
            "stars on Google. A simple automated review follow-up system can move that needle "
            "significantly — and it feeds directly into how AI tools rank and recommend local "
            "businesses.</p>"
            "<p>{{gap_report_text}}</p>"
            "<p>Would it be worth a 15-minute call to walk through how this works?</p>"
            "<p>Best, Cosmin | nrankai.com<br>"
            "Book a call: <a href=\"{{booking_url}}\">{{booking_url}}</a></p>"
        ),
    },
]


# ── 1. GET /email-templates/ ──────────────────────────────────────────────────

@router.get("/")
async def list_templates(
    db: AsyncSession = Depends(get_session),
    _: None = Depends(require_n8n_key),
):
    """Returneaza toate template-urile din DB."""
    result = await db.execute(select(EmailTemplate).order_by(EmailTemplate.segment))
    templates = result.scalars().all()
    return [
        {
            "id": t.id,
            "segment": t.segment,
            "subject": t.subject,
            "body_html": t.body_html,
            "body_text": t.body_text,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in templates
    ]


# ── 2. PUT /email-templates/{segment} ────────────────────────────────────────

@router.put("/{segment}")
async def upsert_template(
    segment: str,
    body: TemplateUpsertBody,
    db: AsyncSession = Depends(get_session),
    _: None = Depends(require_n8n_key),
):
    """Upsert template pentru segment. Creaza daca nu exista, update daca exista."""
    result = await db.execute(
        select(EmailTemplate).where(EmailTemplate.segment == segment)
    )
    template = result.scalar_one_or_none()

    if template:
        template.subject = body.subject
        template.body_html = body.body_html
        template.body_text = body.body_text
        action = "updated"
    else:
        template = EmailTemplate(
            segment=segment,
            subject=body.subject,
            body_html=body.body_html,
            body_text=body.body_text,
            created_at=datetime.now(timezone.utc),
        )
        db.add(template)
        action = "created"

    await db.commit()
    await db.refresh(template)
    return {"ok": True, "action": action, "segment": segment}


# ── 3. POST /email-templates/seed ────────────────────────────────────────────

@router.post("/seed")
async def seed_templates(
    db: AsyncSession = Depends(get_session),
    _: None = Depends(require_n8n_key),
):
    """Insereaza template-urile default (segmente generice + secventa dental). Skip daca segmentul exista deja."""
    inserted = 0
    skipped = 0

    for tpl in _DEFAULT_TEMPLATES:
        existing = await db.execute(
            select(EmailTemplate).where(EmailTemplate.segment == tpl["segment"])
        )
        if existing.scalar_one_or_none() is not None:
            skipped += 1
            continue

        db.add(EmailTemplate(
            segment=tpl["segment"],
            subject=tpl["subject"],
            body_html=tpl["body_html"],
            body_text=tpl["body_text"],
            created_at=datetime.now(timezone.utc),
        ))
        try:
            await db.flush()
            inserted += 1
        except IntegrityError:
            await db.rollback()
            skipped += 1

    await db.commit()
    return {"ok": True, "inserted": inserted, "skipped": skipped}
