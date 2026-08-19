# Action Items Contract

## Inputs
- API endpoints under project scope:
  - `POST /api/projects/{project_id}/action-items`
  - `GET /api/projects/{project_id}/action-items`
  - `PATCH /api/projects/{project_id}/action-items/{action_item_id}`
  - `POST /api/projects/{project_id}/action-items/{action_item_id}/close`
  - `DELETE /api/projects/{project_id}/action-items/{action_item_id}` (project owner only)
- Auto-detection ingestion events (queued):
  - cadence session completion summary payloads
  - normalized task-comment mutation payloads (`posted_comment_text`) from:
    - Cortex task comment writes
    - provider webhook adapters (Jira now, future tools through same adapter contract)
- Auto-detection extraction uses deterministic parsing baseline and may apply shared LLM gateway enhancement with confidence gating; deterministic fallback is mandatory on gateway/validation failure.
- Explicit user intent creates (UI/Cortex/Slack DM) with `source="manual"`.
- Cortex tool path (`action_item_create`) accepts both structured args and raw user utterance context; create-time normalization/resolution can use both.

## Outputs
- Project Brain `entity_type="action_item"` documents.
- Project-scoped display keys (`ACTION-<n>`) unique within project.
- Legacy action-item display prefixes (`ACT-*`, `AI-*`) are deprecated and not accepted for new resolution flows.
- PM board summary support field: open action-item count (composed in `/pm-board-summary` path).
- Optional temporal field: `target_datetime` (UTC) when event-time can be grounded from action text.

## Side Effects
- Writes occur in project Brain collection keyed by custom project id.
- PM board cache invalidation on successful create/update/close (`invalidate_pm_board_summary_cache(project_id)`).
- Auto-detect dedupe uses `source_event_key` on action-item records (7-day window policy), not a separate ingest-ledger collection in v1.
- Scheduler-backed escalation notifications for unresolved follow-ups (default 4h, configurable) in reminder phase.
- Clarification lifecycle state is persisted in shared `pending_interactions` collection (cross-feature, multi-project scoped by `project_id`) as ephemeral runtime state.
- Cleanup policy: pending-interaction records are deleted immediately on terminal outcomes (`resolved`, `cancelled`, `expired`, `stale_target`).
- Digest schedule policy (`project_schedules.schedule_spec`) supports:
  - `skip_weekends` (default `false`)
  - `ignore_followup_for_owner` (default `false`)

## State Model
- `status`: `open | done | cancelled`
- `needs_followup`: boolean flag orthogonal to status.
- `needs_followup_set_at`: timestamp anchor for follow-up aging/escalation.

Transitions:
- Create:
  - mandatory fields complete (`title`, `owner_user_id`) -> `status=open`, `needs_followup=false`
  - missing mandatory fields -> `status=open`, `needs_followup=true`, set `needs_followup_set_at`
- Explicit command create with missing/ambiguous owner:
  - do not finalize owner silently
  - issue clarification prompt to command actor
  - finalize as `open` only after resolved owner is mapped to project member `user_id`
- Follow-up resolution:
  - when leaving follow-up state, clear `needs_followup_set_at`
  - re-entry to follow-up state resets `needs_followup_set_at`
- Close:
  - `status in {done,cancelled}` sets `closed_at`, `closed_by_user_id`

List/read semantics:
- Default list scope is open items when no explicit status is provided.
- `status=closed` maps to `status in {done,cancelled}`.
- Closed-window filtering uses `closed_window_hours` and applies to closed-status reads.

Follow-up delivery and escalation:
- Auto-detected unresolved owner -> notify source actor first with clarification prompt.
- If source actor does not resolve within policy window, escalate unresolved item to PM (owner/admin view + proactive reminder path).
- Cadence-origin unresolved owner prompts are deferred until cadence session ends.
- Terminal clarification outcomes must persist audit attributes on action-item state (`clarification_id`, `resolved_by_user_id`, `resolution_channel`, transition timestamps) before pending-interaction doc deletion.
- When `ignore_followup_for_owner=true`, owner-targeted follow-up escalation/reminder delivery is suppressed; actor-side follow-up reminders remain active.

## RBAC Invariants
- Owner/admin can read and mutate all project action items.
- Member can read/mutate only own items.
- `owner_user_id` must be an active project member `user_id`.
- Non-members cannot access action-item APIs.

## HTTP Actor Binding (Current Transitional Contract)
- Action-item HTTP endpoints resolve actor identity via shared auth dependency (`get_current_user`) and router-level access checks.
- JWT bearer token identity is the authoritative actor source when `JWT_ENFORCEMENT_ENABLED=true`.
- During compatibility mode (`JWT_ENFORCEMENT_ENABLED=false`), fallback identity hints (`X-User-Id` / `user_id`) may be accepted by shared auth dependency.
- Request `user_id` is optional for create/update/close payloads; when supplied, mismatches against resolved actor identity are rejected (`403`).
- Router enforces canonical actor binding by writing `payload_dict["user_id"] = actor_user_id` before service calls.

## Feature Gate Contract
- Runtime feature gates are env-var backed:
  - `ACTION_ITEMS_ENABLED`
  - `ACTION_ITEMS_AUTO_DETECT_ENABLED`
  - `ACTION_ITEMS_DIGEST_ENABLED`
- Current default posture is enabled in app config.
- These flags must remain operational kill-switches (no-redeploy disable path).

## Identity and Source Invariants
- Explicit create across channels uses `source="manual"`.
- Auto-detected Slack DM extraction uses `source="slack_dm_extract"`.
- `action_item_id` is globally unique UUID.
- `display_key` uniqueness is project-scoped.

## Identity Resolution Contract
- Assignee/owner resolution must be tool-agnostic and resolve to canonical ProMarshal `user_id` first.
- Shared resolver location: `api/app/core/identity_resolver.py`.
- Current adoption:
  - Action-item Slack command flows (`create owner:*`, `assign`) use shared resolver.
  - Cadence summary auto-extraction resolves owner only when explicit owner intent is detected.
  - Cortex action-item create flow applies explicit owner-intent parsing with shared resolver and strong-intent follow-up on unresolved matches.
- Matching order is deterministic:
  1. ProMarshal `user_id`
  2. Integration provider IDs from `members[].integration_ids` (Slack/Jira/future tools)
  3. Name/email/email-local-part
  4. Unique prefix match on name
- Ambiguous or unresolved matches must return follow-up clarification (no silent auto-assignment).
- Cortex create owner-resolution staged behavior:
  1. Deterministic owner-intent parse (`parse_owner_intent`) classifies strong/weak/none.
  2. For explicit owner intent lacking strong deterministic hint, shared LLM gateway may extract owner hint.
  3. Resolved owner maps to canonical project member `user_id`.
  4. Weak unresolved `not_found` owner hints may degrade to unassigned create (no hard failure).
  5. Strong unresolved/ambiguous owner hints must fail closed with follow-up/validation guidance.

## Title Normalization Contract
- Cortex `action_item_create` title normalization is deterministic and utterance-aware:
  - strips command prefixes/suffixes (create/add action item boilerplate, assign-owner suffixes)
  - backfills temporal coverage from raw utterance when parsed title drops critical time/date hints
  - enforces max title length.
- Auto-detection extraction title candidate uses deterministic baseline with optional shared LLM gateway rewrite (confidence-gated + retry-bounded), with deterministic fallback on low confidence/error.

## Reliability and Idempotency
- Persist-first/respond-second for create/update/close.
- Ingestion idempotency key window is 7 days for auto-detect events.
- Sequential `display_key` allocation uses atomic project-scoped sequence counter (`project_id + sequence_name`) and must not use non-atomic "read-last+1" logic.
- One-time migration script for legacy display keys: `api/scripts/migrate_action_item_display_keys.py` (`--apply` to persist).
- Auto-detection execution is queue-backed (`action_item_extraction`), not inline.
- Due-date derivation policy for create paths:
  - explicit due input (`due_type in {today,this_week,by_date}` or explicit due datetime) takes precedence
  - otherwise, grounded temporal hints in raw text produce `target_datetime`
  - when `target_datetime` exists and no explicit due is provided, infer actionable due date deterministically (for example invite actions infer pre-event due such as same-day EOD)
  - inferred due must never be persisted in the past

## Comment Event Normalization Contract
- Action-item auto-detection consumes a canonical comment mutation shape (tool/provider agnostic) instead of provider-specific payloads.
- Adapter boundary:
  - provider-specific ingress (for example Jira webhook comment event) must normalize to canonical fields before enqueue.
  - extraction pipeline must not branch on provider names.

## Must-Not-Break
- PM board/pulse summary composition contract remains `/pm-board-summary` driven.
- Existing slash-command policy remains unchanged (control commands only).
- Commitment guard behavior must prevent success claims when write/schedule failed.

## Related
- RFC: `docs/rfcs/RFC-action-items-intelligence-v1-2026-03-13.md`
- Checklist: `docs/execution/action-items-intelligence-v1-implementation-checklist-2026-03-13.md`
- Invariants: `docs/architecture/invariants.md`
- Cortex contract: `docs/contracts/cortex-contract.md`

## Approved Next Contract Changes (Not Yet Implemented)
- Canonical work-item cutover (ADR-0020):
  - work-item related references must resolve against canonical `entity_type="work_item"` records after migration.
  - migration runbook must include cross-reference verification/repair for related work-item links.
  - `related_type="work_item"` remains canonical; resolver behavior must not assume legacy `entity_type="task"` lookup semantics post-cutover.
