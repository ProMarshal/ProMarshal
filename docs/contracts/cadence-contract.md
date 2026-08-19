# Cadence Contract

## Inputs
- Scheduler dispatcher tick (`/api/cron/tick`) executes `SchedulerEngine.tick(job_type="cadence_reminder")` in the daily lane.
- Legacy compatibility endpoint (`/api/cron/cadence-reminders`) can still trigger `cadence_reminder`, but is skipped when dispatcher mode is enabled.
- Project cadence schedule + tier + connected PM provider + active members.
- Slack DM events and Slack interaction payloads (buttons/modals) bound to cadence session identity.
- Cadence schedule policy flags from `project_schedules.schedule_spec`:
  - `skip_weekends` (default `false`)
  - `ignore_followup_for_owner` (default `false`)

## Outputs
- Cadence session documents persisted in project Brain collections (`entity_type="cadence_session"`).
- Session lookup/projection rows persisted in shared `session_index`.
- Slack cadence prompts, follow-ups, backlog decisions, and closure notices.
- Daily cadence summary documents (`entity_type="cadence_daily_summary"`) when summary pipeline conditions are met.

## Runtime Guarantees
- Queue isolation: Slack DM cadence processing is enqueued to Dramatiq `cadence_dm` and acknowledged quickly at webhook ingress (`{"ok": true}`); Cadence turn execution happens in worker runtime.
- Weekend policy:
  - When cadence schedule policy `skip_weekends=true`, cadence reminder sends are skipped on Saturday/Sunday in project-local timezone.
  - Weekend skip is enforced in scheduler next-run computation and executor runtime guard.
- Owner suppression policy:
  - When cadence schedule policy `ignore_followup_for_owner=true`, owner user is excluded from cadence reminder/follow-up delivery audience.
- Scheduler lock: cadence reminder execution uses Redis lock `cadence:cron:task-reminders:lock` (legacy key name); when lock is held, run is skipped with `reason="cadence_lock_active"`.
- Identity-first routing: Slack DM cadence routing resolves active sessions by `(provider, workspace_id, external_user_id)` and is terminal when matched (no Cortex fallthrough).
- Expiry handling: stale active cadence sessions are marked completed (`expired_reason="timeout"`) by cadence expiry processing with best-effort expiry notice. Expiry processing is available independently of reminder dispatch cadence (`cadence_expiry` scheduler job).
- Single-active guard: dispatch path checks for existing active cadence session per configured scope (`global|project`) and blocks duplicate session creation.
- Interaction idempotency: Slack cadence modal submissions and mutation-like button actions are deduped via Redis key + TTL (`cadence_interaction_idempotency_ttl_seconds`), with in-memory fallback. Pure modal-open action (`open_update_modal`) remains re-openable after close.
- Session-turn serialization: cadence DM worker path applies per-session Redis lease fencing (`cadence:{session_id}:lock`, `cadence:{session_id}:fence`) to prevent overlapping session mutation turns.
- Persist-first session state: task updates/comments/status transitions are written through `SessionRepository` before subsequent state transitions/messages.
- Jira-backed mutation parity: Cadence runtime supports comment, status update, and task assignment mutations through registered capability adapters (`workitem_add_comment`, `workitem_update_status`, `workitem_assign`).
- Cadence LLM generation/extraction/classifier calls are routed through shared `app.llm` gateway contracts (feature-local direct LiteLLM completion calls are not the runtime path).
- Post-cadence Team Poll reminder sidecar:
  - after interactive terminal completion (`outcome.response_status="responded"`), Cadence summary-send path can trigger a best-effort unresolved Team Poll reminder for that same Slack identity.
  - reminder sidecar is non-blocking and must not fail Cadence terminal completion.
  - timeout/expired (`no_response_expired`) terminal paths are excluded from immediate post-cadence reminder.

## Data Model Guarantees
- Source of truth for cadence session state is MongoDB (project Brain collection) via `SessionRepository`.
- `session_index` is the shared lookup/index plane and is updated on create/update paths from the same payload.
- Timeout finalization and daily summary generation use source cadence sessions as correctness fallback when `session_index` rows drift.
- Active session checks always require `status != "completed"` and `expires_at > now`.
- Session completion writes explicit terminal outcome fields (`outcome.terminal`, `response_status`, `reason_codes`).

## Allowed Fallbacks
- Redis unavailable:
  - Scheduler lock degrades to best-effort unlocked run.
  - Interaction idempotency degrades to process-local memory dedupe.
  - Session lease degrades fail-open for cadence DM turn execution (availability first).
- Ambiguous/unsupported task provider selection:
  - Cadence check-in skips dispatch for that project/member set (no false success response).
- Slack identity unresolved for a member:
  - Session is created and marked with identity failure outcome; DM is not attempted.

## Explicit Non-Guarantees
- Exact LLM wording for cadence prompts/summaries is not deterministic; only state transitions and persistence are contractual.
- Cross-provider transition semantics are only guaranteed where a cadence adapter capability exists.

## Not Fully Verified Yet
- End-to-end parity for every future non-Jira cadence task provider adapter (registry supports it, but each provider implementation must be validated).
- Message-level UX consistency across all rare timeout/closure race windows under heavy concurrent Slack retries.

## Runtime Hardening Boundary (Phase 4)
- Cadence currently preserves its existing semantics while Cortex runtime hardening progresses.
- Shared runtime improvements are adopted only when they are semantics-preserving for Cadence contracts.
- Temporary exception path is explicitly allowed for Cadence quality-plan gating behavior until parity cutover criteria are met.
- Before broader convergence, parity tests must remain green for:
  - terminal DM/session routing
  - mutation loop progression semantics
  - timeout/clarification outcome contracts
- See execution boundary note:
  - `docs/execution/phase4-cadence-team-poll-rollout-boundary-2026-03-12.md`

## Approved Next Contract Changes (Not Yet Implemented)
- Canonical work-item cutover (ADR-0020):
  - cadence mutation capability IDs migrate to canonical `workitem_*` semantics.
  - dirty-mark/recompute triggers must follow canonical capability semantics and must not depend on legacy `task_*` IDs.
  - cadence session embedded task snapshots remain immutable historical artifacts and are not migration targets.
