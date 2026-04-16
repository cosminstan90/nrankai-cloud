# Prospecting Status
_Last updated: 2026-04-16_

---

## ✅ Infrastructură completă

| Component | Status | Detalii |
|-----------|--------|---------|
| VPS + FastAPI | ✅ Live | api.nrankai.com |
| Admin dashboard | ✅ Live | api.nrankai.com/admin |
| DB (SQLite) | ✅ Migrat | toate coloanele prezente |
| Brevo SMTP | ✅ Configurat | hello@nrankaimail.com |
| Domain nrankaimail.com | ✅ Verificat | SPF/DKIM/DMARC activ |
| n8n workflows | ✅ Importate | 3 workflows active |
| Warm lead follow-up | ✅ Testat | rulează zilnic 10:00 |
| Daily digest | ✅ Importat | rulează zilnic 08:00 |

---

## 📋 Leads exportate

### Batch 1 — Dentist Owners (calitate înaltă)
- **Fișier:** [us_dentist_owners_small_practices](https://share.explorium.ai/38aAcw)
- **Volum:** 72 prospecți
- **Filtre:** job_title owner-specific + has_email + company_size 1-10
- **Email status:** 100% validate (valid / catch-all)
- **Vibe dataset:** `ds-9525223e-49b3-4aa3-be0a-7bb533328ee2`

### Batch 2 — Dentist Owners (NAICS 6212)
- **Fișier:** [us_dentist_owners_batch2](https://share.explorium.ai/YhIak8)
- **Volum:** 46 prospecți (partial export — credite insuficiente)
- **Filtre:** job_title owner-specific + NAICS 6212 + company_size 1-10/11-50
- **Email status:** validate
- **Vibe dataset:** `ds-7812fe92-12df-44d0-8814-147b259721d2`

**Total disponibil: ~118 dentist-owners cu email**

---

## ⚠️ Email Warmup — BLOCKER

`hello@nrankaimail.com` este un domeniu **nou** — trebuie warmed up înainte de orice trimitere bulk.

### Plan warmup (4-6 săptămâni)
| Săptămâna | Emailuri/zi | Acțiune |
|-----------|------------|---------|
| 1-2 | 5-10 | Trimite manual către contacte reale (colegi, prieteni) |
| 3-4 | 20-30 | Warmup tool automat (Instantly / Lemwarm) |
| 5-6 | 50-75 | Creștem gradual spre target |
| 7+ | 100-150/zi | Batch-uri outreach reale |

**Tool recomandat:** [Instantly.ai](https://instantly.ai) sau [Lemwarm](https://lemwarm.com) — conectezi SMTP Brevo și automatizează warmup-ul.

---

## 🔜 Next Steps (în ordine)

1. **[ ] Warmup email** — pornește Instantly/Lemwarm cu hello@nrankaimail.com
2. **[ ] Importă leads în pipeline** — CSV → `POST /api/lead-audits/bulk` sau direct în DB
3. **[ ] Reîncarcă credite Vibe Prospecting** — pentru chiropractors + HVAC (~3000-4000 credite)
4. **[ ] Batch chiropractors** — NAICS 621310, job_title: chiropractor/owner, target 300-500 leads
5. **[ ] Batch HVAC** — NAICS 238220, job_title: owner, target 500-1000 leads
6. **[ ] Prima campanie** — după warmup complet (săpt. 5+), primul batch de 50-100 emailuri/zi

---

## 📊 Proiecție pipeline (după warmup)

| Metric | Valoare |
|--------|---------|
| Leads disponibile (target) | 500-700 |
| Open rate estimat | 25-30% |
| Reply rate | 1-2% |
| Booking rate | 0.3-0.5% |
| **Clienți estimați din primul batch** | **2-4** |
| Deal value mediu | $1,000-2,500/lună |
| **Revenue estimat** | **$2k-10k MRR** |

---

## 🔑 Credențiale & Links importante

| Resurse | Link / Info |
|---------|------------|
| Admin dashboard | https://api.nrankai.com/admin |
| Vibe Prospecting | https://app.vibeprospecting.ai |
| Brevo SMTP | smtp-relay.brevo.com:587 |
| n8n | http://192.168.0.81:5678 |
| nrankaimail.com DNS | Cloudflare |
| Vibe credite consumate | ~450-500 credite |

---

## 📁 Fișiere relevante

```
stancosmin_cloud/
├── routes/admin.py          # Dashboard API
├── routes/prospects.py      # Pipeline prospects
├── templates/admin.html     # Dashboard UI
├── n8n_workflows/
│   ├── warm_lead_followup.json
│   ├── daily_digest.json
│   └── workflow-combined-export.json
├── OUTREACH_PLAN.md         # Plan complet arhitectură
└── PROSPECTING_STATUS.md    # Acest fișier
```
