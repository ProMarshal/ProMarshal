# ProMarshal Setup Guide

This is the complete, from-scratch setup guide for running ProMarshal — local development, every environment variable, external service registration (Google, Slack, Jira, email, LLM providers), and production deployment via Docker.

## Contents

- [1. Prerequisites](#1-prerequisites)
- [2. Architecture at a Glance](#2-architecture-at-a-glance)
- [3. Local Development Setup](#3-local-development-setup)
- [4. Environment Variable Reference](#4-environment-variable-reference)
- [5. External Service Setup](#5-external-service-setup)
- [6. Critical Gotchas](#6-critical-gotchas)
- [7. Scheduler / Reminders Trigger](#7-scheduler--reminders-trigger)
- [8. Production Deployment (Docker)](#8-production-deployment-docker)
- [9. Running Tests](#9-running-tests)
- [10. Troubleshooting](#10-troubleshooting)

---

## 1. Prerequisites

| Requirement | Notes |
|---|---|
| Node.js 18+ | Frontend (Next.js) |
| Python 3.10+ | Backend (FastAPI); CI runs on 3.11 |
| MongoDB | Local instance or Atlas — **hard requirement**, the API will not boot without it |
| Redis (or Valkey) | **Required in practice** — the API boots without it, but Cortex, Cadence, the scheduler, and caching all depend on it |
| Docker | Recommended for Redis locally, required for the production Compose stack |
| Google Cloud project | For Google OAuth (primary login method) |
| Slack App | Optional — only needed if you want Slack integration |
| Jira OAuth App (Atlassian) | Optional — only needed if you want Jira integration |
| An LLM provider key | At least one of OpenAI / Anthropic / Groq, depending on which features you want |

## 2. Architecture at a Glance

Three deployable services plus two data stores:

```
web (Next.js)  --->  api (FastAPI)  --->  MongoDB
                          |
                          v
                       Redis  <---  worker (Dramatiq)
```

- **web** — Next.js app, handles UI + NextAuth session (Google OAuth / Email OTP)
- **api** — FastAPI backend, owns all business logic, integrations, and the scheduler
- **worker** — Dramatiq background worker, processes queued jobs (Slack/Jira events, Cortex agent runs, action item extraction)
- **MongoDB** — system of record (projects, users) plus a per-project "Brain" collection for tasks/sessions
- **Redis** — job queue backend, session cache, dedupe/locking for Cortex and Slack ingress

See [`docs/architecture/system-architecture.md`](./architecture/system-architecture.md) for the full, code-verified architecture writeup.

## 3. Local Development Setup

### 3.1 Clone

```bash
git clone https://github.com/ProMarshal/ProMarshal.git
cd ProMarshal
```

### 3.2 MongoDB

Use a local instance or a free [MongoDB Atlas](https://www.mongodb.com/cloud/atlas) cluster. You'll need a connection string for `MONGODB_URI`.

### 3.3 Redis

```bash
docker run -d -p 6379:6379 redis:latest
```

### 3.4 Backend (API)

```bash
cd api
python -m venv venv
venv\Scripts\activate       # Windows
# source venv/bin/activate  # Mac/Linux

pip install -r requirements.txt

cp .env.example .env
# Edit .env — see section 4 for what each variable does

python run.py
```

Backend runs on `http://127.0.0.1:8000`. Interactive API docs at `http://127.0.0.1:8000/docs` (disabled automatically when `ENVIRONMENT=production`).

### 3.5 Worker (background jobs)

ProMarshal uses **Dramatiq**, not RQ or Celery. In a separate terminal:

```bash
cd api
venv\Scripts\activate       # Windows
# source venv/bin/activate  # Mac/Linux

python start_dramatiq_worker.py
```

Run with no arguments to process all supported queues (`chat_interactions`, `workitem_events`, `chat_commands`, `cortex_runs`, `action_item_extraction`, `cadence_dm`), or pass specific queue names as arguments to restrict it. Keep this terminal running — Slack/Jira events, Cortex agent turns, and action item extraction all flow through this worker.

### 3.6 Frontend (Web)

```bash
cd web
npm install

cp .env.example .env.local
# Edit .env.local — see section 4
```

Then start the dev server:

```bash
npm run dev
```

Frontend runs on `http://localhost:3000`.

### 3.7 Scheduler trigger

Reminders/cadence/team polls need something to call `POST /api/cron/tick` periodically. See [section 7](#7-scheduler--reminders-trigger).

At this point you have a working local stack. Continue to section 5 to register Google/Slack/Jira apps and enable integrations.

---

## 4. Environment Variable Reference

### 4.1 `api/.env` (backend)

| Variable | Required? | Purpose |
|---|---|---|
| `MONGODB_URI` | **Required** | MongoDB connection string. App fails to boot without it. |
| `BRAIN_DB_NAME` | Required | Name of the per-project "Brain" database (e.g. `brain-dev` locally, `brain` in prod). |
| `REDIS_URL` | **Required in practice** | Queue backend, session cache, Cortex/Slack dedupe locks. API boots without it but most features will fail at request time. |
| `JWT_SECRET_KEY` | **Required** | Signs backend-issued JWTs. |
| `ENCRYPTION_KEY` | **Required** | Fernet key used to encrypt Slack/Jira OAuth tokens at rest. Generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. |
| `INTERNAL_SERVICE_SECRET` | **Required** | Shared secret between `web` and `api` for internal service-to-service calls. **Must be identical in both `api/.env` and `web/.env.local`** — see [Critical Gotchas](#6-critical-gotchas). |
| `CRON_SECRET` | Required for scheduler | Bearer token that authorizes calls to `/api/cron/tick`. |
| `FRONTEND_URL` / `BACKEND_URL` | Required | Used to build OAuth redirect URIs and CORS config. |
| `ALLOWED_ORIGINS` | Required | CORS allowlist, comma-separated. |
| `SLACK_CLIENT_ID` / `SLACK_CLIENT_SECRET` / `SLACK_SIGNING_SECRET` | Optional | Enables Slack integration. See [5.3](#53-slack-app). |
| `JIRA_CLIENT_ID` / `JIRA_CLIENT_SECRET` | Optional | Enables Jira integration. See [5.4](#54-jira-oauth-app). |
| `OPENAI_API_KEY` | Optional | Powers OpenAI-backed features (default reasoning/summary model). |
| `ANTHROPIC_API_KEY` | Optional | Powers Claude-backed conversation features. |
| `GROQ_API_KEY` | Optional | Powers Groq-backed reminder message generation; falls back to static templates if unset. |
| `DEEPGRAM_API_KEY` | Optional | Meeting audio transcription. No-ops if unset. |
| `SERPER_API_KEY` | Optional | Web search tool for the planner agent. No-ops if unset. |
| `EMAIL_PROVIDER` + `ZEPTOMAIL_API_URL`/`ZEPTOMAIL_API_KEY` or `SENDGRID_API_KEY` | Optional, but **required for Email-OTP login** | If unconfigured, OTP emails silently fail to send (login still works via Google OAuth). |
| `PM_CONVERSATION_PROVIDER` / `PM_CONVERSATION_MODEL` | Optional | Which LLM handles brainstorm/chat (defaults to Anthropic). |
| `PM_REASONING_PROVIDER` / `PM_REASONING_MODEL` | Optional | Which LLM handles finalize checks/conflict detection (defaults to OpenAI). |

Full list with defaults: [`api/.env.example`](../api/.env.example) and [`api/app/core/config.py`](../api/app/core/config.py).

### 4.2 `web/.env.local` (frontend)

| Variable | Required? | Purpose |
|---|---|---|
| `AUTH_SECRET` | **Required** | NextAuth session encryption. Generate with `openssl rand -base64 32`. |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | **Required for Google login** | See [5.2](#52-google-oauth). |
| `PYTHON_API_URL` | **Required** | Server-side URL the Next.js app uses to call the FastAPI backend (e.g. `http://127.0.0.1:8000` locally). |
| `NEXT_PUBLIC_PYTHON_API_URL` | **Required** | Client-side (browser) URL for the backend. Baked into the build at build time. |
| `INTERNAL_SERVICE_SECRET` | **Required** | Must match `api/.env`'s value exactly — see [Critical Gotchas](#6-critical-gotchas). |
| `NEXTAUTH_URL` | Required in production | Canonical URL of the frontend deployment. |

See [`web/.env.example`](../web/.env.example).

### 4.3 Worker

The worker process shares `api/.env` when run locally via `python start_dramatiq_worker.py`. In Docker Compose it has its own env file — see [`deploy/env/worker.env.example`](../deploy/env/worker.env.example).

---

## 5. External Service Setup

### 5.1 MongoDB

Local: `mongodb://localhost:27017/promarshal`. Atlas: create a free cluster, create a database user, and use the `mongodb+srv://` connection string Atlas gives you.

### 5.2 Google OAuth

1. Go to [Google Cloud Console](https://console.cloud.google.com/) → APIs & Services → Credentials.
2. Create an OAuth 2.0 Client ID (type: Web application).
3. Authorized redirect URI: `http://localhost:3000/api/auth/callback/google` (local) or `https://<your-frontend-domain>/api/auth/callback/google` (production).
4. Copy the Client ID and Secret into `web/.env.local`.

### 5.3 Slack App

Each self-hosted instance needs its **own** Slack app — there's no shared/hardcoded app. Create one at [api.slack.com/apps](https://api.slack.com/apps):

1. **OAuth & Permissions** → set the redirect URL to:
   `{BACKEND_URL}/api/integrations/slack/callback`
2. Add these **Bot Token Scopes**: `channels:read`, `channels:history`, `groups:read`, `groups:history`, `im:read`, `im:history`, `im:write`, `mpim:read`, `mpim:history`, `chat:write`, `users:read`, `users:read.email`, `team:read`, `app_mentions:read`, `commands`.
3. **Event Subscriptions** → enable, set Request URL to:
   `{BACKEND_URL}/api/integrations/slack/events`
4. **Interactivity & Shortcuts** → enable, set Request URL to:
   `{BACKEND_URL}/api/integrations/slack/interactions`
5. **Slash Commands** (if used) → Request URL:
   `{BACKEND_URL}/api/integrations/slack/commands`
6. Copy the **Client ID**, **Client Secret**, and **Signing Secret** from Basic Information into `api/.env` (`SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET`, `SLACK_SIGNING_SECRET`).
7. Install the app to your workspace to test.

`{BACKEND_URL}` must be a publicly reachable HTTPS URL for Slack to deliver events — use a tunnel (e.g. `ngrok`) for local development.

### 5.4 Jira OAuth App

Each self-hosted instance needs its own Atlassian OAuth 2.0 (3LO) app, created at [developer.atlassian.com/console/myapps](https://developer.atlassian.com/console/myapps):

1. Create an OAuth 2.0 (3LO) app.
2. **Authorization** → callback URL:
   `{BACKEND_URL}/api/integrations/jira/oauth`
3. **Permissions** → add scopes: `read:jira-work`, `read:jira-user`, `write:jira-work`, `read:email-address:jira`, `read:project:jira`, `read:board-scope:jira-software`, `read:sprint:jira-software`, `write:sprint:jira-software`, `manage:jira-webhook`, `offline_access`.
4. Copy the **Client ID** and **Secret** into `api/.env` (`JIRA_CLIENT_ID`, `JIRA_CLIENT_SECRET`).
5. Jira webhooks are registered per-project automatically (via `manage:jira-webhook`) using a per-project URL token — no manual webhook config needed.

### 5.5 Email provider (for OTP login)

Google OAuth works without this. If you want the Email-OTP login path to actually send emails, configure one of:
- **ZeptoMail**: set `EMAIL_PROVIDER=zeptomail`, `ZEPTOMAIL_API_URL`, `ZEPTOMAIL_API_KEY`.
- **SendGrid**: set `EMAIL_PROVIDER=sendgrid`, `SENDGRID_API_KEY`.

Without either, OTP requests are logged and silently return failure — no error surfaces to the user, so don't skip this if you plan to support OTP login.

### 5.6 LLM providers

At minimum, configure one of `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GROQ_API_KEY` depending on which features matter to you:
- **Planner/Cortex conversation** — defaults to Anthropic (`PM_CONVERSATION_PROVIDER=anthropic`)
- **Reasoning/finalize checks** — defaults to OpenAI (`PM_REASONING_PROVIDER=openai`)
- **Cadence reminder message generation** — Groq; falls back to static templates if unset
- **Deepgram** — optional, meeting transcription only
- **Serper** — optional, web search tool for the planner agent only

---

## 6. Critical Gotchas

These will produce confusing failures if missed — read before you file a bug:

1. **`INTERNAL_SERVICE_SECRET` must be identical in `api/.env` and `web/.env.local`.** The web app calls internal API endpoints (user creation, token exchange) using this shared secret. A mismatch causes a `403` on every login attempt (both Google and OTP), which looks like a broken auth system but is actually a config mismatch.
2. **Redis is not optional in practice.** The API boots without it, but Cortex, Cadence, the scheduler, and Slack ingress dedupe/locking all fail at request time without it. Treat it as required.
3. **OTP login requires an email provider.** If `EMAIL_PROVIDER` isn't configured, OTP requests fail silently (logged server-side, no error to the user). Use Google OAuth instead, or configure email.
4. **Slack/Jira apps are per-instance.** There is no shared ProMarshal-hosted Slack/Jira app — you must register your own for each integration you want, as described in section 5.
5. **`docker-compose.prod.yml` as shipped pulls images from a private AWS ECR registry** you won't have access to. To self-host with Docker, build the images yourself — see [section 8](#8-production-deployment-docker).

---

## 7. Scheduler / Reminders Trigger

Task reminders, cadence check-ins, and team polls are driven by an external call to:

```
POST /api/cron/tick
Authorization: Bearer <CRON_SECRET>
```

Nothing calls this automatically — you need to trigger it periodically yourself. Options:
- A system cron job: `*/5 * * * * curl -s -X POST https://your-backend/api/cron/tick -H "Authorization: Bearer $CRON_SECRET"`
- A Kubernetes `CronJob`
- A GitHub Actions workflow on a `schedule` trigger, using repo secrets for the URL/token
- Any managed scheduler service (e.g. cron-job.org, EventBridge Scheduler)

The endpoint is idempotent and safe to call concurrently — see `api/app/scheduler/engine.py` for lease/idempotency handling.

---

## 8. Production Deployment (Docker)

`docker-compose.prod.yml` defines three services (`api`, `web`, `worker`) plus a local Valkey (Redis-compatible) container. **As shipped it references private ECR images** (`803946367259.dkr.ecr.ap-south-1.amazonaws.com/...`) that only the ProMarshal team can pull. To self-host, build your own images from the included Dockerfiles instead:

```bash
docker build -t promarshal-api -f api/Dockerfile api
docker build -t promarshal-worker -f api/Dockerfile.worker api
docker build -t promarshal-web -f web/Dockerfile web
```

Then edit `docker-compose.prod.yml`, replacing each `image:` line with the corresponding local tag (`promarshal-api`, `promarshal-web`, `promarshal-worker`), or use a `build:` block instead of `image:` for each service.

1. Populate env files from the examples:
   ```bash
   cp deploy/env/api.env.example deploy/env/api.env
   cp deploy/env/web.env.example deploy/env/web.env
   cp deploy/env/worker.env.example deploy/env/worker.env
   ```
2. Fill in real values (see section 4). `ENCRYPTION_KEY`, `JWT_SECRET_KEY`, `INTERNAL_SERVICE_SECRET`, and Slack/Jira/LLM credentials must be **identical across `api.env` and `worker.env`**.
3. Start the stack:
   ```bash
   docker compose -f docker-compose.prod.yml up -d
   ```
4. Check health:
   ```bash
   curl http://127.0.0.1:8000/health
   ```

MongoDB is not included in this Compose stack — use Atlas or point `MONGODB_URI` at your own instance.

---

## 9. Running Tests

```bash
cd api
python -m unittest discover -s tests -p "test_*.py"
```

CI (`.github/workflows/api-tests.yml`) additionally runs a few schema/policy validation scripts before the main suite — see that workflow file if you want to replicate it exactly locally.

---

## 10. Troubleshooting

| Symptom | Likely cause |
|---|---|
| Login fails with 403 on every attempt | `INTERNAL_SERVICE_SECRET` mismatch between `api/.env` and `web/.env.local` |
| App boots but everything hangs/times out | Redis not running or `REDIS_URL` misconfigured |
| Email OTP requests "succeed" but no email arrives | No email provider configured (`EMAIL_PROVIDER` unset) |
| Slack events never reach the backend | Request URL not publicly reachable (use a tunnel like `ngrok` locally), or `SLACK_SIGNING_SECRET` mismatch |
| Reminders/cadence never fire | Nothing is calling `POST /api/cron/tick` — see section 7 |
| `docker compose up` fails pulling images | You're using the stock `docker-compose.prod.yml` pointing at a private ECR registry — build your own images first (section 8) |
