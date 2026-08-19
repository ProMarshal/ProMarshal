# ProMarshal — Universal Coding Rules

> **These rules apply to ALL coding assistants** (Codex, Claude Code, Antigravity, Cursor, Windsurf, etc.)
> and **MUST be followed for every code reviews and code change**, no exceptions.

---

## Code Quality Standards

1. **No shallow or temporary fixes.** Every change must be production-grade, fully thought through, and resilient. Do not apply band-aid patches that mask root causes.
2. **No placeholder implementations.** If you implement something, implement it completely. Do not leave half-built logic behind.
3. **No TODO/FIXME comments as a substitute for implementation.** If something needs to be done, do it now or explicitly flag it to the user — do not silently defer.
4. **Do not remove or weaken existing functionality** unless explicitly asked. If a change has side effects on other features, flag it before proceeding.
5. **Do not introduce new dependencies** without justification. Prefer using what's already in the stack.
6. **Write clean, readable, maintainable code.** Avoid over-engineering, but also avoid under-engineering. Code should be easy to understand and extend.

---

## Change Discipline Checklist

Before making any change, work through this checklist:

1. **Clarify scope and success criteria** before writing code.
2. **Identify system boundaries touched** — UI, API, DB, jobs, caching, integrations.
3. **Enumerate state sources and lifecycles** — session, storage, DB, in-memory, Redis.
4. **Analyze re-entry/rehydration paths** — mount/unmount, retries, refresh, resume.
5. **Check dependency triggers** — effects, caches, timeouts, subscriptions.
6. **Validate failure modes** — empty responses, partial writes, network errors, race conditions.
7. **Ensure user-facing claims reflect persisted state** — never show success before data is saved.
8. **Preserve backward compatibility** unless explicitly approved to break it.
9. **Add minimal logging** for new failure modes or state transitions.
10. **Provide a rollback/escape hatch** when feasible.
11. **Validate multi-user, multi-project, and future-scalable** correctness.
12. **Two-iteration bug-fix cap before debug-first RCA.** If a bug is not resolved after 2 fix attempts, stop iterative patching and switch to root-cause analysis with explicit debug instrumentation/logging/trace capture. Apply the next fix only after evidence identifies the failure point, then re-verify with focused tests and runtime logs.

---

## Frontend Rules

- For any effect or async call, audit dependencies and confirm re-run safety.
- Guard against duplicate initialization (idempotent init per key).
- Avoid restoring "loading" or transient UI states from storage.
- Only show UI states backed by persisted or validated data.
- Do not introduce new UI patterns that conflict with existing design conventions.

---

## Backend Rules

- Validate inputs early and return precise, descriptive errors.
- **Persist first, respond second** for stateful actions — never return success before the data is saved.
- Use retries only with adaptive/backoff logic (not blind repeats).
- Do not claim completion unless persistence succeeds.
- Handle edge cases: empty collections, missing fields, expired tokens, concurrent writes.

---

## Data & Workflow Rules

- Store source of truth in one place; document any derived caches.
- Reconcile draft/finalized states consistently across modes.
- Any data migration or schema change must be backward-compatible or include a migration plan.

---

## Project-Specific Constraints

These are critical implementation rules for ProMarshal:

- **No emoji icons in code** — do not add emoji to user-facing messages or UI elements in code.
- **Token encryption** — all integration tokens (Slack, Jira) must be encrypted before storing in the database.
- **Session-based deduplication** for paid tier — use `slack_sessions`, no separate interaction ledger.
- **Project ID format** — `proj-XXXX-YYYY` (random 4-digit pairs), stored in `projects.project_id`.
- **Brain collections** — named using `project_id` for data isolation. Each project has its own collection.
- **Jira token refresh** — must include retry logic with exponential backoff (1s, 2s, 4s).
- **Entity types** — use `entity_type` field dynamically from MongoDB, not hardcoded.

---

## Integration Webhook Security

Every inbound webhook endpoint MUST use `require_webhook_auth("<integration>")` as a FastAPI dependency.
Adding a webhook route without this dependency is a blocking review issue.

Checklist for adding a new integration webhook endpoint:
1. Create `api/app/integrations/<name>/webhook_verifier.py`
2. Extend `HmacWebhookVerifier` (or `BaseWebhookVerifier` for JWT-based providers)
3. Add signing secret/key config to `api/app/core/config.py` and `api/.env.example`
4. Register verifier in `api/app/main.py` startup
5. Use `Depends(require_webhook_auth("<name>"))` on inbound webhook routes
6. Enforce fail-closed behavior in staging/production when secret is missing (`503`), with soft-fail allowed only in dev/local/test

---

## Communication Rules

- If a change has wider impact than requested, **flag it before proceeding**.
- If you're unsure about intent, **ask for clarification** — do not guess.
- If you discover a bug while working on something else, **report it separately** — do not silently fix it or ignore it.
- When explaining changes, be specific about **what changed, where and why**, not just what the code does.
- When proposing fixes, do **not** optimize only for the triggering test/example wording. Infer the underlying intent class and design a **global, scalable rule** that covers diverse phrasing, regions, and edge cases. Avoid hardcoding user-provided example words as the core fix strategy.

---

*This file is the single source of truth for coding rules. All assistant-specific config files (AGENTS.md, CLAUDE.md, .cursorrules, etc.) reference this file.*
