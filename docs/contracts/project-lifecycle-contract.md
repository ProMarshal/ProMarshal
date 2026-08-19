# Project Lifecycle Contract

Last verified against code: 2026-03-28.

Defines behavior contracts for project owner controls and hard-delete lifecycle.

## 1) Owner-Only Settings Visibility

### Inputs
- Authenticated actor identity.
- Selected project context and resolved project role.

### Outputs
- Owner can access Project Settings controls.
- Non-owner should not see Project Settings entry points in normal UI navigation.

### Side Effects
- UI-only navigation gating and URL sanitization for non-owner deep links.

### Error Handling
- Frontend gating failures are not a security boundary; backend checks remain authoritative.

## 2) Delete Endpoint Authorization and Response Contract

### Endpoint
- `DELETE /api/projects/{project_id}`

### Auth Contract
- Backend enforces owner-only delete using authenticated actor identity.

### Response Contract
- `204 No Content`: delete completed.
- `403 Forbidden`: actor is authenticated but not owner.
- `404 Not Found`: project missing or inaccessible.
- `409 Conflict`: `delete_in_progress` for same project lock contention.

## 3) Hard-Delete Cleanup Boundary

For `{mongo_project_id, custom_project_id}`:

### Required Cleanup (must succeed before final project row delete)
- `project_schedules` cleanup for `project_id = custom_project_id`.
- Brain collection drop for collection name `custom_project_id`.
- Filesystem cleanup of `uploads/{custom_project_id}/`.

### Best-Effort Cleanup (non-blocking; log/metric required)
- Mongo metadata/index cleanup:
  - `session_index`, `pending_interactions`, `channel_index`
  - `project_documents`, `planner_data`, `slack_messages`
- Redis cleanup:
  - PM summary keys
  - recommendation runtime keys
  - Cortex session/runtime queue keys scoped to project prefix
  - value-based sweep for `cortex:default_project:*` where value equals `custom_project_id`
- Optional integrations:
  - Jira webhook deregistration attempt
  - Neo4j project-scope graph cleanup (no-op when disabled)

### Final Irreversible Step
- Delete primary `projects` row only after required cleanup succeeds.

## 4) Ordering, Locking, and Retry Semantics

### Locking
- Acquire per-project delete lock: `project_delete_lock:v1:{custom_project_id}`.
- Return `409 delete_in_progress` when lock already held.
- Explicitly release lock on success and handled failure paths; TTL is crash fallback only.

### Ordering
1. Resolve context + owner auth + acquire lock.
2. Required + best-effort cleanup stages (per policy class).
3. Final primary project row delete.
4. Lock release.

### Retry Semantics
- Retry scope is full orchestration replay from step 1.
- All cleanup operations must remain idempotent.

## 5) In-Flight Job Skip Contract

Project-scoped executors must treat missing/deleted project as deterministic non-fatal skip (`project_not_found`) and avoid retry storms.

Covered project-scoped classes include:
- cadence reminder flows
- recommendation flows
- action item digest/follow-up flows
- team poll cycle flows
- slack hourly reader per-project loops

Global-note:
- `cadence_expiry` is seeded as global (`project_id = null`) and must tolerate missing project context directly.

## 6) Explicit Out-of-Scope (Current Iteration)

- Soft delete/archive retention mode.
- Provider-side Slack token revocation during project delete.
- Guaranteed Jira webhook deregistration without persisted webhook-id metadata.
