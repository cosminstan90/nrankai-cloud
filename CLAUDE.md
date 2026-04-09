# nrankai-cloud — Context pentru Claude Code

## Ce este acest proiect
API FastAPI pentru nrankai.com — un serviciu de automatizare pentru beauty clinics si med spas din US.
Primeste leads de la n8n (via Outscraper Google Maps), le auditeaza tehnic si GEO, genereaza rapoarte personalizate si le trimite inapoi la n8n pentru outreach via Zoho Mail.

**Production URL:** api.nrankai.com
**VPS path:** /home/asdwfe/apps/stancosmin_cloud/

## Arhitectura
- Framework: FastAPI + Uvicorn
- DB: SQLite (lead_audits.db) via SQLAlchemy async
- Auth: Bearer token (N8N_API_KEY pentru n8n, WORKER_API_KEY pentru geo_tool worker) -- vezi auth.py
- Templates: Jinja2 in templates/
- Background tasks: FastAPI BackgroundTasks + tasks.py
- Deploy: git push -> SSH VPS -> git pull -> systemctl restart

## Structura fisierelor
nrankai-cloud/
  main.py                  # FastAPI app, routers, lifespan, security middleware
  database.py              # SQLAlchemy models + engine + get_session
  schemas.py               # Pydantic schemas
  auth.py                  # Bearer token auth (require_n8n_key, require_worker_key)
  tasks.py                 # Background workers (stale_job_recovery_loop, process_prospects_batch)
  routes/
    lead_audits.py         # Core audit flow -- NU MODIFICA (in productie)
    prospects.py           # Prospect management + /dashboard UI
    tools.py               # Utility tools (site-design)
    email_templates.py     # Email template management + /seed
  workers/
    site_design_detector.py   # Analiza tehnica URL fara LLM
    callback_sender.py        # Retry logic pentru n8n callbacks (3 attempts)
  templates/
    index.html             # Landing page
    report.html            # Raport audit lead (public, token-based)
    prospects.html         # Prospects dashboard (AlpineJS + Tailwind)

## Stack extern
- geo_tool (repo separat, app.nrankai.com) -- GEO audit worker; polleaza /api/lead-audits/next
- n8n pe Proxmox (192.168.0.81:5678) -- orchestrare workflows via webhooks
- Zoho Mail SMTP -- outreach emails (hello@nrankai.com)
- Outscraper -- Google Maps scraping, trimite leads la n8n

## Reguli de cod
1. NU modifica routes/lead_audits.py -- e core si e in productie
2. Modele noi se adauga la FINALUL database.py
3. Scheme noi se adauga la FINALUL schemas.py
4. Background tasks se adauga la FINALUL tasks.py
5. Fiecare router nou se inregistreaza in main.py cu prefix explicit
6. httpx pentru toate requesturile externe (nu requests sync)
7. Toate erorile tratate cu try/except, niciodata naked exceptions
8. Variabile de mediu DOAR din .env via os.getenv(), niciodata hardcodate
9. Timestamps: datetime.now(timezone.utc) -- nu datetime.utcnow() (deprecated)
10. Background tasks nu pot folosi sesiunile din Depends(get_session) -- foloseste AsyncSessionLocal() proprie

## Variabile .env importante
BASE_URL, N8N_API_KEY, WORKER_API_KEY, CLOUD_WEBHOOK_SECRET,
BOOKING_URL, PAGESPEED_API_KEY, ALLOWED_ORIGINS

## Flow principal de date
Outscraper -> n8n -> POST /prospects/bulk -> process_prospects_batch()
  -> site_design_detector -> callback_sender -> n8n -> Zoho Mail outreach

## Cum se ruleaza local
uvicorn main:app --reload --port 8080

## Cum se deployeaza
git push
# pe VPS: cd /home/asdwfe/apps/stancosmin_cloud && git pull && systemctl restart nrankai-cloud

## Note n8n importante
- emailSend node typeVersion 2: campul este emailFormat: html (nu emailType)
- HTTP Request node (typeVersion 4.2) auto-spliteaza array responses
- API key hardcodat in workflows (n8n Variables necesita plan platit) -- regenereaza la deploy nou
- emailSend output nu contine input data -- referentiaza upstream cu dollar-sign(NodeName).item.json.field
