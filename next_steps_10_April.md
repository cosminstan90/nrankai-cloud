# Next Steps — 10 April 2026

## Context rapid
Două proiecte active:
- **api.nrankai.com** → `D:\Projects\stancosmin_cloud\` (FastAPI, SQLite, lead gen pipeline)
- **app.nrankai.com** → `D:\Projects\geo_tool\` (FastAPI, SEO/GEO audit tool intern)

Security hardening Prioritatea 1 și 2 deja făcut (CORS, CSP, rate limiting, body size limits, startup validation).
Admin dashboard = **`api.nrankai.com/admin`** (datele sunt acolo, zero infra nouă).

---

## SESIUNEA 1 — Admin: Auth + Stats API

**Proiect:** `D:\Projects\stancosmin_cloud\`

**Citește înainte să scrii cod:**
- `database.py` — modele: Prospect, EmailTrackingEvent, Unsubscribe
- `routes/prospects.py` — structura existentă, helper `_prospect_to_dict`
- `main.py` — cum sunt înregistrate routerele, middleware existent
- `auth.py` — `require_n8n_key`, `require_worker_key`

**Prompt exact:**

```
Repo: nrankai-cloud (D:\Projects\stancosmin_cloud\)
Citește: database.py, routes/prospects.py, main.py, auth.py

TASK: Creează sistemul de bază pentru admin dashboard.

1. AUTH — middleware BasicAuth pentru /admin/*
   - Username + password din env vars: ADMIN_USERNAME, ADMIN_PASSWORD
   - Dacă nu sunt setate, /admin returnează 503 cu mesaj clar
   - Returnează 401 cu WWW-Authenticate header (browser popup nativ, zero JS)
   - Adaugă ADMIN_USERNAME și ADMIN_PASSWORD în .env.example

2. GET /admin/stats — endpoint JSON cu toate statisticile
   Returnează un singur obiect cu:

   a) pipeline_overview:
      - total_prospects, breakdown pe status (pending/scored/contacted/replied/booked/unsubscribed)

   b) email_funnel:
      - sent (status=contacted), opened (open_count > 0), clicked (click_count > 0)
      - open_rate și click_rate ca procente

   c) by_segment (array):
      - segment, count, contacted, open_rate, click_rate
      - sortat descrescător după count

   d) by_industry (array):
      - business_category, count, contacted, open_rate, avg_opportunity_score
      - top 20, sortat după count

   e) by_location (array):
      - location_city + location_state, count, contacted, open_rate
      - top 20 orașe, sortat după count

   f) by_campaign (array):
      - campaign_id (fără sufixul ::job_id), count, contacted, open_rate
      - sortat după created_at DESC, ultimele 10 campanii

   g) timeline (array, ultimele 12 săptămâni):
      - week (ISO string), prospects_added, emails_sent

   h) unsubscribes:
      - total, last_7_days, last_30_days

   Toate calculele în SQL (SQLAlchemy async), nu în Python loops.
   Auth: require_admin (noul middleware).
   Înregistrează router în main.py cu prefix="/admin".

REGULI:
- Mapped/mapped_column style dacă adaugi modele (nu Column())
- datetime.now(timezone.utc) nu utcnow()
- Nu modifica routes/lead_audits.py
- Background tasks deschid propria AsyncSessionLocal()
```

---

## SESIUNEA 2 — Admin: Dashboard HTML

**Proiect:** `D:\Projects\stancosmin_cloud\`

**Citește înainte:**
- `routes/admin.py` — endpoints create în Sesiunea 1
- `templates/prospects.html` — structura Tailwind + Alpine existentă (referință pentru stil)
- `main.py` — prefix routes

**Prompt exact:**

```
Repo: nrankai-cloud (D:\Projects\stancosmin_cloud\)
Citește: routes/admin.py, templates/prospects.html, main.py

TASK: Creează pagina HTML pentru admin dashboard la GET /admin

Template: templates/admin.html
Stack: Tailwind CDN + Alpine.js + Chart.js (cdn.jsdelivr.net/npm/chart.js)
Stilul: dark theme identic cu prospects.html (bg-slate-950, text-indigo-400 accents)
Auth: browser BasicAuth nativ (fără JS login form — 401 + WWW-Authenticate face asta automat)

SECȚIUNI în pagină (în ordine):

1. HEADER — "nrankai admin" + data curentă

2. KPI CARDS (row de 5):
   - Total Prospects | Emails Trimise | Open Rate % | Click Rate % | Booked
   - Fiecare card cu număr mare + label mic + comparație față de săptămâna trecută (↑↓)

3. EMAIL FUNNEL — bar orizontal vizual
   Prospects → Scored → Contacted → Opened → Clicked → Replied → Booked
   Cu numere absolute și procente la fiecare pas

4. WARM LEADS QUEUE — tabel prioritar
   Prospecți cu open_count > 0 AND status != 'replied' AND status != 'booked'
   Coloane: Business | City | Segment | Opens | Last opened | Acțiuni (Mark Replied / Mark Booked)
   Dacă 0 warm leads → mesaj verde "No warm leads pending"

5. BY SEGMENT — tabel
   Segment | Count | Contacted | Open Rate | Click Rate

6. BY INDUSTRY — tabel top 10
   Industry | Count | Contacted | Open Rate | Avg Score

7. BY LOCATION — tabel top 10
   City/State | Count | Contacted | Open Rate

8. CAMPANII — tabel
   Campaign ID | Date | Leads | Contacted | Open Rate

9. TIMELINE CHART — Chart.js bar chart
   Prospects adăugați + emails trimise per săptămână (12 săptămâni)

FUNCȚIONALITATE Alpine:
- Date încărcate la mount din GET /admin/stats
- Warm leads din GET /admin/warm-leads (endpoint nou simplu)
- markReplied(id) și markBooked(id) → POST /prospects/{id}/mark-status cu {status: 'replied'/'booked'}
- Loading state + error state
- Auto-refresh la 5 minute

IMPORTANT:
- Toate request-urile fetch includ credentials: 'include' pentru BasicAuth
- Nu folosi alert() — toast system ca în prospects.html
```

---

## SESIUNEA 3 — Reply/Booking tracking + ROI

**Proiect:** `D:\Projects\stancosmin_cloud\`

**Citește înainte:**
- `database.py` — modelul Prospect (câmpurile existente)
- `routes/prospects.py` — mark-contacted endpoint (referință)
- `routes/admin.py` — pentru a adăuga warm-leads endpoint

**Prompt exact:**

```
Repo: nrankai-cloud (D:\Projects\stancosmin_cloud\)
Citește: database.py, routes/prospects.py, routes/admin.py

TASK: Adaugă tracking pentru replied/booked și ROI de bază.

1. DATABASE — adaugă câmpuri noi în modelul Prospect (Mapped style):
   - replied_at: Mapped[Optional[datetime]]
   - booked_at: Mapped[Optional[datetime]]
   - deal_value: Mapped[Optional[float]] — cât plătește (USD/EUR)
   - notes: Mapped[Optional[str]] cu String(1000) — note manuale

2. POST /prospects/{id}/mark-status
   Body: { "status": "replied" | "booked", "deal_value": float (optional), "notes": str (optional) }
   - Validează că status e unul din cele două valori
   - Setează replied_at sau booked_at cu datetime.now(timezone.utc)
   - Dacă deal_value e trimis, salvează-l
   - Auth: require_n8n_key

3. GET /admin/warm-leads
   Returnează prospecți cu open_count > 0, status NOT IN ('replied', 'booked', 'unsubscribed')
   Sortat după last_opened_at DESC
   Limitat la 50
   Auth: require_admin

4. Adaugă în GET /admin/stats secțiunea roi:
   - total_booked: count WHERE booked_at IS NOT NULL
   - total_revenue: SUM(deal_value) WHERE deal_value IS NOT NULL
   - avg_deal_value: AVG(deal_value)
   - conversion_rate: booked / contacted * 100

REGULI standard: Mapped style, datetime.now(timezone.utc), nu modifica lead_audits.py
```

---

## SESIUNEA 4 — Daily Digest Email (n8n workflow)

**Proiect:** `D:\Projects\stancosmin_cloud\n8n_workflows\`

**Citește înainte:**
- `n8n_workflows/prospect_intake_flow.json` — structura și stilul JSON existent
- `routes/admin.py` — endpoint GET /admin/stats (ce date sunt disponibile)
- `CLAUDE.md` — reguli n8n (emailFormat, typeVersion, etc.)

**Prompt exact:**

```
Repo: nrankai-cloud
Citește: n8n_workflows/prospect_intake_flow.json, CLAUDE.md

TASK: Creează n8n_workflows/daily_digest.json — workflow care trimite zilnic un email
de sumar cu performanța pipeline-ului.

FLOW:
1. Schedule Trigger — în fiecare zi la 08:00 (cron: 0 8 * * *)
2. HTTP GET /admin/stats — cu Authorization: Basic (base64 ADMIN_USER:ADMIN_PASS)
   URL: https://api.nrankai.com/admin/stats
3. Code node — construiește HTML email frumos cu:
   - KPIs: total prospects, emails trimise ieri, open rate, warm leads în așteptare
   - Dacă există warm leads: lista primilor 5 (business name + city + opens)
   - Footer cu link direct la https://api.nrankai.com/admin
4. Send Email (Zoho SMTP) — la hello@nrankai.com
   Subject: "nrankai daily — {data} | {nr} warm leads | {open_rate}% open rate"

REGULI JSON n8n obligatorii (din CLAUDE.md):
- emailFormat: "html" (NU emailType)
- html: "={{ $json.body_html }}" când emailFormat e html
- IF node typeVersion 2 cu conditions.conditions[] + combinator: "and"
- Placeholder: REPLACE_WITH_N8N_API_KEY, REPLACE_WITH_ZOHO_SMTP_CREDENTIAL_ID
- "active": false
- IDs unice în format UUID-like
```

---

## SESIUNEA 5 — CLAUDE.md pentru geo_tool (lipsește complet)

**Proiect:** `D:\Projects\geo_tool\`

**Citește înainte:**
- `api/main.py` — structura aplicației
- `api/models/database.py` — modele
- `api/limiter.py` — limiter nou creat
- `api/middleware/auth.py` — BasicAuthMiddleware

**Prompt exact:**

```
Repo: nrankai-tool (D:\Projects\geo_tool\)
Citește: api/main.py, api/models/database.py, api/limiter.py, api/middleware/auth.py,
         api/routes/audits.py

TASK: Creează CLAUDE.md la rădăcina proiectului cu instrucțiuni clare pentru sesiunile
viitoare de Claude Code.

Include:
1. Descriere scurtă a proiectului (ce face, URL producție)
2. Structura directoarelor (ce e în api/, api/routes/, api/models/, api/workers/, api/middleware/)
3. Cum se pornește serverul local (restart_server.bat, port 8000)
4. Reguli de cod obligatorii:
   - Rate limiting: importă limiter din api/limiter.py (nu din main.py — circular import)
   - Adaugă request: Request ca primul param când folosești @limiter.limit()
   - Auth: BasicAuthMiddleware e global (set în .env), nu per-endpoint
   - DB: SQLAlchemy async, get_db() din api/models/database.py
   - Error handling: try/except pe toate apelurile AI externe (Anthropic, OpenAI, Mistral, Google)
5. Variabile de mediu importante (cu ce fac, nu valorile)
6. Pattern-uri comune (cum se adaugă un nou router, cum se face un apel AI safe)
7. Ce NU se modifică: api/models/database.py fără migration plan, prompt files din prompts/
```

---

## Ordine recomandată

| Sesiune | Timp est. | Output |
|---------|-----------|--------|
| 1 — Auth + Stats API | 1-2h | `/admin/stats` funcțional |
| 2 — Dashboard HTML | 2-3h | Dashboard vizibil la `/admin` |
| 3 — Reply/Booking + ROI | 1h | Tracking complet pipeline |
| 4 — Daily Digest (n8n) | 1h | Email zilnic automat |
| 5 — CLAUDE.md geo_tool | 30 min | Context clar pt sesiuni viitoare |

---

## Note pentru fiecare sesiune nouă

La începutul fiecărei sesiuni, spune explicit:
> "Lucrăm pe proiectul nrankai-cloud / nrankai-tool. Citește CLAUDE.md înainte să scrii cod."

Fișiere cheie de citit întotdeauna pentru nrankai-cloud:
- `CLAUDE.md` — reguli obligatorii
- `database.py` — modele actuale
- `auth.py` — pattern-uri de autentificare
