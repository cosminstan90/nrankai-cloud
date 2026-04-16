# nrankai — Plan Complet Outreach

## Arhitectura sistemului

Există **două pipeline-uri independente**, ambele folosind același backend (api.nrankai.com):

```
┌─────────────────────────────────────────────────────────────────────┐
│  PIPELINE A — Free Audit Form (inbound)                             │
│                                                                     │
│  Visitor → form nrankai.com → POST /api/lead-audits/submit          │
│    → geo_tool worker (local) picks up job                           │
│    → runs full GEO audit                                            │
│    → result posted back                                             │
│    → n8n workflow-2 (poller, every 2 min) detects completion        │
│    → n8n sends approval email to YOU (hello@nrankai.com)            │
│    → YOU click Approve/Skip                                         │
│    → n8n workflow-3 sends cold email to visitor                     │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│  PIPELINE B — Bulk Outreach (outbound)                              │
│                                                                     │
│  Lead Source (Vibe Prospecting MCP / Outscraper)                    │
│    → POST /prospects/bulk                                           │
│    → process_prospects_batch() [background]                         │
│      → site_design_detector (scores website, detects stack)         │
│      → callback_sender → n8n webhook "prospect-scored"             │
│    → n8n prospect_intake_flow:                                      │
│      → check skip (no email / no segment)                           │
│      → check unsubscribe (CAN-SPAM)                                 │
│      → GET /prospects/{id}/email-preview                            │
│      → send outreach email                                          │
│      → mark-contacted                                               │
│      → wait 3 days                                                  │
│      → check-opened                                                 │
│      → if not opened + has phone → SMS (TODO)                       │
│    → n8n warm_lead_followup (daily 10:00):                          │
│      → warm leads in 4–12 day window → follow-up email             │
│    → Admin dashboard (/admin):                                      │
│      → mark replied / mark booked                                   │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│  SHARED INFRA                                                       │
│  api.nrankai.com   — FastAPI backend (VPS)                          │
│  app.nrankai.com   — geo_tool audit engine (local machine)          │
│  n8n               — 192.168.0.81:5678 (local Proxmox)              │
│  daily_digest      — email zilnic la 08:00                          │
└─────────────────────────────────────────────────────────────────────┘
```

---

## n8n Workflows — Inventar Complet

| Fișier | Pipeline | Trigger | Scop |
|--------|----------|---------|------|
| `workflow-1-job-submitter.json` | A | Manual | Test: submit job manual |
| `workflow-2-completion-poller.json` | A | Every 2 min | Detectează audite gata → trimite approval email |
| `workflow-3-approval-cold-email.json` | A | Webhook GET `/lead-approve` | Trimite cold email după approval |
| `prospect_intake_flow.json` | B | Webhook POST `/outscraper-results` + POST `/prospect-scored` | Intake leads + outreach email + 3-day followup |
| `warm_lead_followup.json` | B | Daily 10:00 | Follow-up pentru warm leads (ziua 4–12) |
| `daily_digest.json` | Admin | Daily 08:00 | Email zilnic cu stats pipeline |

### Import în n8n
1. n8n → Workflows → **Import from file**
2. Importă fiecare fișier din `n8n_workflows/`
3. Înlocuiește placeholder-ele (vezi secțiunea Credentials de mai jos)
4. Activează workflow-urile (toggle ON)

### Credentials n8n necesare

| Placeholder | Ce e | Unde îl găsești |
|-------------|------|----------------|
| `REPLACE_WITH_N8N_API_KEY` | Bearer token pentru /prospects/* și /api/lead-audits/* | `.env` → `N8N_API_KEY` |
| `REPLACE_WITH_ZOHO_SMTP_CREDENTIAL_ID` | ID credential SMTP Zoho în n8n | n8n → Credentials → ID din URL |
| `REPLACE_WITH_BASE64_ADMIN_CREDENTIALS` | `btoa('ADMIN_USERNAME:ADMIN_PASSWORD')` | Rulează în browser console |

---

## Infrastructura Email

### Regula de bază
**Nu trimiți cold email de pe nrankai.com.**
Foloseşti domenii dedicate de sending — nrankai.com e pentru comunicare legitimă (replies, welcome).

### Domenii recomandate (Cloudflare Registrar — ~$10/an)
```
Domeniu principal:  nrankai.com       → replies, welcome emails, website
Sending domeniu 1:  nrankaimail.com   → Pipeline B outreach
Sending domeniu 2:  getrankai.com     → Pipeline B outreach (după warmup)
```

### Setup per domeniu de sending (Cloudflare DNS)
```
SPF:   TXT  @                "v=spf1 include:_spf.brevo.com ~all"
DKIM:  CNAME mail._domainkey  [valoarea din Brevo la setup domeniu]
DMARC: TXT  _dmarc            "v=DMARC1; p=none; rua=mailto:dmarc@nrankai.com"
```
Fără SPF+DKIM → spam guaranteed.

### SMTP — Stack recomandat
```
Recepție replies:   Zoho Mail Free  (hello@nrankai.com, inbox real)
Trimitere outreach: Brevo Free      (300 emails/zi, IP curat, fără card)
```

**Setup Brevo (5 min):**
1. brevo.com → cont gratuit
2. Senders & IP → Domains → Add domain → adaugă `nrankaimail.com`
3. Urmează instrucțiunile DNS (DKIM + SPF)
4. SMTP & API → SMTP Keys → Generate
5. În n8n → New Credential → SMTP:
   - Host: `smtp-relay.brevo.com`, Port: `587`
   - User: `emailul tău Brevo`
   - Password: `SMTP key generat`

### Warmup — Obligatoriu pentru domenii noi

| Săptămâna | Emails/zi total | Note |
|-----------|----------------|------|
| 1 | 20 | Manual sau Warmup Inbox free |
| 2 | 40 | |
| 3 | 80 | |
| 4 | 120 | |
| 5+ | 200+ | Poate incepe outreach real |

**Tool gratuit:** warmupinbox.com (free plan: 1 cont, automat)
**Tool complet:** Instantly.ai ($37/lună — include domenii + warmup + sending + tracking)

---

## VPS Deploy — Comenzi Exacte

### Acces SSH
```bash
ssh asdwfe@api.nrankai.com
cd /home/asdwfe/apps/stancosmin_cloud
```

### Deploy complet (prima dată după branch nou)
```bash
# 1. Opreşte serviciul
sudo systemctl stop nrankai-cloud

# 2. Pull codul nou
git pull

# 3. Migrații DB — coloane noi pe tabelul prospects
python3 - <<'EOF'
import sqlite3
conn = sqlite3.connect('lead_audits.db')
c = conn.cursor()
migrations = [
    "ALTER TABLE prospects ADD COLUMN replied_at DATETIME",
    "ALTER TABLE prospects ADD COLUMN booked_at DATETIME",
    "ALTER TABLE prospects ADD COLUMN deal_value REAL",
    "ALTER TABLE prospects ADD COLUMN notes VARCHAR(1000)",
]
for sql in migrations:
    try:
        c.execute(sql)
        print(f"OK: {sql}")
    except Exception as e:
        print(f"SKIP: {e}")
conn.commit()
conn.close()
print("Done.")
EOF

# 4. Adaugă variabile noi în .env
nano .env
# Adaugă:
# ADMIN_USERNAME=admin
# ADMIN_PASSWORD=<parola_puternica>

# 5. Porneşte serviciul
sudo systemctl start nrankai-cloud

# 6. Verifică
sudo systemctl status nrankai-cloud
curl -s https://api.nrankai.com/api/health
```

### Deploy normal (update cod)
```bash
cd /home/asdwfe/apps/stancosmin_cloud
git pull
sudo systemctl restart nrankai-cloud
sudo systemctl status nrankai-cloud
```

### Verificare admin dashboard
```
https://api.nrankai.com/admin
→ browser popup: ADMIN_USERNAME / ADMIN_PASSWORD
```

---

## Lead Generation — Vibe Prospecting (MCP în Claude Code)

Vibe Prospecting înlocuieşte Outscraper pentru Pipeline B.
Avantaje: emailuri directe, date LinkedIn-verificate, fără scraping.

### Workflow în Claude Code
```
1. Deschide sesiune Claude Code
2. Cere: "Caută 200 beauty clinics în Florida cu website și email"
3. Claude:
   - fetch-entities: beauty clinics US, has_website: true, has_email: true
   - enrich-prospects: adaugă emailuri
   - formatează în schema /prospects/bulk
   - POST direct la https://api.nrankai.com/prospects/bulk
4. Pipeline B porneste automat (scoring → email)
```

### Schema pentru POST /prospects/bulk
```json
{
  "campaign_id": "vibe-florida-beauty-2026-04",
  "callback_url": "http://192.168.0.81:5678/webhook/prospect-scored",
  "leads": [
    {
      "url": "https://example-spa.com",
      "business_name": "Example Med Spa",
      "business_category": "Medical Spa",
      "location_city": "Miami",
      "location_state": "FL",
      "google_place_id": "unique_id_required",
      "email_address": "owner@example-spa.com",
      "phone": "+13055551234",
      "google_rating": 4.5,
      "review_count": 87
    }
  ]
}
```

**IMPORTANT:** `google_place_id` trebuie să fie unic per lead (previne duplicate).
Dacă Vibe Prospecting nu are place_id, foloseşte `business_name + city` ca hash unic.

---

## Operare Zilnică

### Dashboard admin
```
https://api.nrankai.com/admin
```
- Verifică **Warm Leads Queue** dimineaţa
- Click **Booked** sau **Replied** când un prospect confirmă
- Timeline chart arată volumul ultimelor 12 săptămâni

### Email zilnic (08:00)
Primeşti automat pe `hello@nrankai.com`:
- Total prospects, open rate, warm leads în aşteptare
- Link direct la admin dashboard

### Rulare campanie nouă
```
1. Claude Code + Vibe Prospecting → search leads → POST /prospects/bulk
2. Aşteaptă ~5-10 min (scoring în background)
3. n8n prospect_intake_flow se declanşează automat per prospect
4. Emails se trimit automat
5. La 4-12 zile după trimitere → warm_lead_followup.json trimite follow-up
```

---

## Checklist Setup Iniţial

```
□ VPS deploy (comenzile de mai sus)
□ Înregistrează nrankaimail.com pe Cloudflare
□ Setup Brevo + adaugă domeniu + DNS (SPF/DKIM/DMARC)
□ Porneşte warmup pe nrankaimail.com (Warmup Inbox)
□ Adaugă ADMIN_USERNAME + ADMIN_PASSWORD în .env VPS
□ Import toate workflow-urile în n8n (192.168.0.81:5678)
□ Înlocuieşte placeholderele în fiecare workflow
□ Activează workflow-urile în n8n
□ Test: trimite 1 lead manual prin /prospects/bulk
□ Verifică că emailul ajunge şi tracking-ul funcţionează
□ Aşteaptă 4 săptămâni warmup
□ Lansează prima campanie reală (Vibe Prospecting)
```
