#!/usr/bin/env python3
"""
Import LinkedIn prospect CSV exports into the prospects table.

Usage:
    python scripts/import_linkedin_leads.py leads1.csv leads2.csv ...

CSV must have columns (Vibe Prospecting export format):
    prospect_first_name, prospect_last_name, prospect_company_name,
    prospect_company_website, prospect_city, prospect_region_name,
    prospect_job_title, contact_professions_email, contact_professional_email_status

Generates a synthetic google_place_id = "linkedin_<md5(email)>" to satisfy
the unique constraint without real Google Place IDs.
"""
import csv
import hashlib
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "lead_audits.db"
CAMPAIGN_ID = f"dental_{datetime.now().strftime('%Y-%m')}"
SEGMENT = "dental"


def synthetic_place_id(email: str) -> str:
    return "linkedin_" + hashlib.md5(email.lower().encode()).hexdigest()


def clean(val: str) -> str:
    return val.strip() if val else ""


def load_csv(path: str) -> list[dict]:
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def import_leads(csv_paths: list[str]) -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    accepted = 0
    duplicates = 0
    skipped = 0
    now = datetime.now(timezone.utc).isoformat()

    for path in csv_paths:
        print(f"\n→ Reading {path}")
        try:
            rows = load_csv(path)
        except FileNotFoundError:
            print(f"  ✗ File not found: {path}")
            continue

        print(f"  {len(rows)} rows found")

        for row in rows:
            email = clean(
                row.get("contact_professions_email") or
                row.get("contact_professions_email_address") or ""
            )
            if not email or "@" not in email:
                skipped += 1
                continue

            # Skip catch-all emails if desired (comment out to include them)
            status = clean(row.get("contact_professional_email_status", ""))
            if status == "invalid":
                skipped += 1
                continue

            business_name = clean(
                row.get("prospect_company_name") or
                row.get("business_name") or ""
            )
            if not business_name:
                skipped += 1
                continue

            url = clean(
                row.get("prospect_company_website") or
                row.get("business_domain") or ""
            )
            if url and not url.startswith("http"):
                url = "https://" + url

            first = clean(row.get("prospect_first_name", ""))
            last = clean(row.get("prospect_last_name", ""))
            full_name = f"{first} {last}".strip() or clean(
                row.get("prospect_full_name", "")
            )

            city = clean(row.get("prospect_city", ""))
            state = clean(row.get("prospect_region_name", ""))
            job_title = clean(row.get("prospect_job_title", ""))

            place_id = synthetic_place_id(email)

            try:
                cur.execute(
                    """
                    INSERT INTO prospects (
                        campaign_id, url, business_name, business_category,
                        location_city, location_state, google_place_id,
                        email_address, has_website, segment, status,
                        opportunity_score, created_at, updated_at,
                        notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        CAMPAIGN_ID,
                        url or None,
                        business_name,
                        "Dental Practice",
                        city or None,
                        state or None,
                        place_id,
                        email,
                        1 if url else 0,
                        SEGMENT,
                        "pending",
                        0,
                        now,
                        now,
                        f"{job_title} | {full_name}" if job_title else full_name,
                    ),
                )
                accepted += 1
            except sqlite3.IntegrityError:
                duplicates += 1

    conn.commit()
    conn.close()

    print(f"\n{'='*40}")
    print(f"✅ Accepted  : {accepted}")
    print(f"⚠️  Duplicates: {duplicates}")
    print(f"⏭️  Skipped   : {skipped}")
    print(f"Campaign ID : {CAMPAIGN_ID}")
    print(f"{'='*40}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/import_linkedin_leads.py file1.csv [file2.csv ...]")
        sys.exit(1)

    import_leads(sys.argv[1:])
