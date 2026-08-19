# Contributing to ProMarshal

Thanks for your interest in contributing. This document covers how to get set up and how to submit changes.

## Getting Started

See [README.md](./README.md) for the full local setup (backend, frontend, Redis, worker, OAuth). In short:

```bash
# Backend
cd api
python -m venv venv && venv\Scripts\activate  # or source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python run.py

# Frontend
cd web
npm install
cp .env.example .env.local
npm run dev
```

## Coding Standards

All code changes must follow [RULES.md](./RULES.md) — the single source of truth for coding standards in this repo, including:
- No shallow/placeholder fixes; implement things completely
- No new dependencies without justification
- Webhook endpoints must use `require_webhook_auth(...)`
- Integration tokens must be encrypted before storage

Please read it before opening a PR of any meaningful size.

## Making Changes

1. Fork the repo and create a branch from `main` (e.g. `fix/task-sync-race` or `feat/linear-integration`).
2. Make your change, following the conventions of the surrounding code.
3. Add or update tests in `api/tests/` for backend changes.
4. Run the relevant test suite before opening a PR:
   ```bash
   cd api
   python -m unittest discover tests
   ```
5. Open a pull request with a clear description of what changed and why.

## Reporting Bugs

Open a GitHub issue with steps to reproduce, expected vs. actual behavior, and relevant logs/screenshots. If it's a security issue, see [SECURITY.md](./SECURITY.md) instead — do not open a public issue.

## Code of Conduct

This project follows the [Code of Conduct](./CODE_OF_CONDUCT.md). By participating, you agree to uphold it.
