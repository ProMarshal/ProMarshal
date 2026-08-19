# Agent Instructions

> **IMPORTANT: Before making any code changes, read and follow all rules in [`RULES.md`](./RULES.md).**
> Those rules are the single source of truth for coding standards across all assistants.

---

## Project Context

- **Product**: ProMarshal — AI-powered project management assistant
- **Backend**: FastAPI (Python 3.10+), MongoDB Atlas, Redis + RQ
- **Frontend**: Next.js (App Router), TypeScript, Tailwind CSS v4
- **Integrations**: Slack, Jira, OpenAI, Neo4j (optional)
- **Deployment**: Render (backend), Vercel/Netlify (frontend)

For full project context, architecture, and data models, see [`CLAUDE.md`](./CLAUDE.md).

---

## Mandatory Change Governance

Before any non-trivial code change (bug fix, refactor, feature, architectural update), follow this process:

1. Read [`docs/assistant-context.md`](./docs/assistant-context.md).
2. Validate against [`docs/architecture/invariants.md`](./docs/architecture/invariants.md).
3. Check existing decisions in [`docs/decision-index.md`](./docs/decision-index.md) and relevant ADRs in [`docs/adr/`](./docs/adr/).
4. If behavior/architecture changes, create or update an ADR using [`docs/adr/ADR-TEMPLATE.md`](./docs/adr/ADR-TEMPLATE.md).
5. Confirm impacted subsystem contract(s) in [`docs/contracts/`](./docs/contracts/).
6. For non-trivial design work, create/update an RFC from [`docs/rfcs/RFC-TEMPLATE.md`](./docs/rfcs/RFC-TEMPLATE.md) before implementation.
7. For every bug fix, add/update an entry in [`docs/testing/regression-matrix.md`](./docs/testing/regression-matrix.md).
8. Use [`docs/templates/change-review-checklist.md`](./docs/templates/change-review-checklist.md) before finalizing.

Do not proceed with implementation if these artifacts are missing or contradictory; resolve docs first, then code.

---

## Quick Reference

- Start backend: `cd api && uvicorn app.main:app --reload --port 8000`
- Start frontend: `cd web && npm run dev`
- Start worker: `cd api && rq worker slack_interactions jira_webhooks action_item_extraction`
