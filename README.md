# BoomUp Work Platform

Independent internal work platform. Phase 1A provides the secure application foundation, dashboard, and Invoice Price Check module shell. It does **not** implement invoice rules, Google Sheets retrieval, or PDF modification.

## Stack

- Python 3.12, FastAPI, Jinja2, SQLAlchemy, Alembic
- Independent Argon2id authentication with opaque server-side sessions
- SQLite for local development; PostgreSQL-compatible database boundary for production
- pytest, Ruff, pip-audit

## Local setup (Windows PowerShell)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

Set a unique `APP_SECRET` in `.env` (never commit it), then export the values into the process environment or use your approved secret loader. This application deliberately does not auto-load `.env` files.

Initialize the database:

```powershell
.\.venv\Scripts\alembic.exe upgrade head
```

Create the initial admin using an interactive password prompt:

```powershell
.\.venv\Scripts\python.exe -m app.cli create-admin --email admin@example.com --display-name "Work Platform Admin"
```

The password is never accepted as a command-line argument or written to Git. Minimum length is 12 characters and storage uses Argon2id.

Run locally:

```powershell
.\.venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`.

## Configuration

See `.env.example`. Warehouse and Inventory cards read `WAREHOUSE_APP_URL` and `INVENTORY_APP_URL`; an empty value is displayed as `Not configured`. Phase 1B Google configuration variables are placeholders only and are not used in Phase 1A.

Production requires HTTPS, a non-placeholder `APP_SECRET` of at least 32 characters, a production database, and private file storage. Session cookies become `Secure` when `APP_ENV=production`.

## Tests and checks

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\pip-audit.exe -r requirements.txt
```

## Module boundaries

- `app/auth`: independent authentication and future identity-provider boundary
- `app/modules/invoice_price_check`: protected route and composition shell only
- `app/integrations/customer_price`: read-only provider interface only
- `app/pdf`: PDF processor interface only
- `app/shared`: shared infrastructure
- `app/templates`, `app/static`: server-rendered frontend

## Security and data handling

- Never commit `.env`, credentials, tokens, customer price data, invoice PDFs, or databases.
- Original and modified invoice PDFs will use a 30-day retention policy in the later processing phase.
- Customer Price Manager remains authoritative and read-only; no dataset snapshot is stored here.
- Warehouse and Inventory remain external systems and are not embedded or authenticated through this application.
