# Planner / Charter Contract

## Inputs
- Planner read endpoints:
  - `GET /api/planner/{project_id}/bootstrap`
  - `GET /api/planner/{project_id}/status`
  - `GET /api/planner/{project_id}/entities/{stage}`
  - `GET /api/planner/{project_id}/current-stage`
- Planner mutation endpoints that can affect status/entities/current-stage:
  - `POST /api/planner/{project_id}/chat`
  - `POST /api/planner/{project_id}/start-fresh/{stage}`
  - `POST /api/planner/{project_id}/reactivate/{stage}`
  - `POST /api/planner/{project_id}/finalize/{stage}`
  - `POST /api/planner/{project_id}/update-point/{stage}`
  - `POST /api/planner/{project_id}/add-point/{stage}`
  - `POST /api/planner/{project_id}/draft/{stage}`
  - `DELETE /api/planner/{project_id}/delete-point/{stage}/{point_index}`
  - `POST /api/planner/{project_id}/extract-from-documents`
  - `DELETE /api/planner/{project_id}/reset-charter`
  - `POST /api/planner/{project_id}/topics/{topic_id}/activate`
  - `POST /api/planner/{project_id}/charter-mode`

## Outputs
- Existing planner response payloads are unchanged.
- Cache layer is transparent to clients.
- Bootstrap contract (`GET /api/planner/{project_id}/bootstrap`):
  - `200 ready`: returns full bootstrap payload with:
    - `partial=false`
    - `project_id`
    - `charter_mode`
    - `topics`
    - `status`
    - `current_stage`
    - `entities_by_stage` (all active topics)
    - `documents`
  - `202 processing`: returns async envelope with:
    - `status=processing`
    - `job_id`
    - `retry_after_ms`
    - `bootstrap` (partial payload with `partial=true` and first-stage entities for immediate paint)
- Bootstrap status contract (`GET /api/planner/{project_id}/bootstrap-status`):
  - status lifecycle: `queued | processing | ready | failed`
  - returns `bootstrap` payload when `ready`

## Guarantees
- Mongo Brain remains canonical source of truth for planner entities/discussions.
- Planner `status`/`finalized_content` truth is derived from Brain entity documents (not discussion presence).
- Redis planner keys are derived short-lived read caches only.
- Read routes are read-through:
  - cache hit returns cached payload
  - cache miss/error falls back to planner service (Mongo-backed) and best-effort cache write
- `bootstrap` composes multiple reads server-side to reduce frontend fan-out and stage-race conditions.
- Bootstrap async runtime is deterministic and deduped by project lock.
- Redis unavailability is fail-open for planner reads (no cache hard dependency).

## Side Effects
- Redis planner cache keys:
  - `planner_status:v1:{project_id}`
  - `planner_entities:v1:{project_id}:{stage}`
  - `planner_current_stage:v1:{project_id}`
- Redis bootstrap runtime keys:
  - `planner_bootstrap_payload:v1:{project_id}`
  - `planner_bootstrap_status:v1:{project_id}`
  - `planner_bootstrap_lock:v1:{project_id}`
- Mutation routes invalidate planner keys to reduce stale windows.
- Ops metrics endpoint for planner cache diagnostics:
  - `GET /api/planner/ops/cache-metrics`

## Config
- `PLANNER_CACHE_ENABLED`
- `PLANNER_STATUS_CACHE_TTL_SECONDS`
- `PLANNER_ENTITIES_CACHE_TTL_SECONDS`
- `PLANNER_CURRENT_STAGE_CACHE_TTL_SECONDS`

## Allowed Fallbacks
- If Redis read/write/delete fails, planner routes continue with canonical Mongo-backed behavior.
