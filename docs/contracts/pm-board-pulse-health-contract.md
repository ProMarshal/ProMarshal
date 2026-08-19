# PM Board / Pulse Health Contract

## Inputs
- Project-scoped summary payload from:
  - `GET /api/projects/{project_id}/pm-board-summary`
- Async status payload (used when summary compute is queued):
  - `GET /api/projects/{project_id}/pm-board-summary-status`
- Supporting recommendation surfaces:
  - `GET /api/projects/{project_id}/recommendations` (internal/supporting read)
  - `POST /api/projects/{project_id}/recommendations/feedback` (supporting feedback write)
  - `GET /api/projects/{project_id}/recommendations/thresholds` (supporting/admin read)
  - `PUT /api/projects/{project_id}/recommendations/thresholds` (supporting/admin write)
- Health is computed from Brain collection work items (`entity_type == "work_item"`):
  - `_compute_project_health_live(...)` in `api/app/projects/router.py`
  - `HealthService.calculate_health(...)` in `api/app/projects/health_service.py`
- Optional UI filters:
  - URL/query: `healthStatus` (Pulse > Project Health only)
  - Pulse table filters: key/name, assignee, status, due date, health

## Outputs
- PM Board:
  - Work item status cards (completed / on_track / at_risk / critical)
  - Today focus, alerts/recommendations, action inbox from composed payload
- Pulse:
  - Work Item hierarchy/table from same health payload (`initialHealthData={healthHierarchy}`)
  - Optional health-filtered detailed breakdown

## Guarantees
- PM Board and Pulse health data are sourced from the same composed backend response and same `health` object mapping.
- On Redis cache miss, `GET /api/projects/{project_id}/pm-board-summary` may return `202` with `status=processing` while backend queue recompute runs; clients should poll `GET /api/projects/{project_id}/pm-board-summary-status` and then re-read summary.
- Recommendation display for PM Board alerts and Pulse task action must resolve from the same backend-owned recommendation source once the recommendation subsystem is implemented.
- `GET /api/projects/{project_id}/pm-board-summary` remains the authoritative PM/Pulse UI response for health plus recommendation display.
- Any `GET /api/projects/{project_id}/recommendations` route is supporting/internal only and must not replace `pm-board-summary` as the PM/Pulse frontend fetch contract.
- PM Board -> Pulse deep link sets URL state first:
  - `openPulseProjectHealthByStatus(...)`, `openPulseProjectHealthFocus(...)`
- `healthStatus` query param is scoped to Pulse Work Item and removed elsewhere:
  - URL hygiene effect in `web/components/projects/projects-page.tsx`
- During deep-link transition, stale PM local state is blocked from rewriting URL:
  - `pulseHealthNavIntentRef` guard

## Side Effects
- Redis summary cache read/write:
  - `api/app/projects/pm_board_cache.py`
  - key: `pm_board_summary:v1:{custom_project_id}`
- Redis dirty marker used to bypass stale snapshot fallback after workitem mutations:
  - key: `pm_board_summary:dirty:v1:{custom_project_id}`
  - set by mutation flows before async recompute enqueue; cleared after successful PM summary recompute.
- Redis async job status/lock keys for PM summary recompute:
  - `pm_board_summary_job:v1:{custom_project_id}`
  - `pm_board_summary_job_lock:v1:{custom_project_id}`
- RQ queue enqueue on cache miss:
  - queue: `cortex_runs`
  - worker entrypoint: `app.projects.router.process_pm_board_summary_job`
- Target recommendation-runtime side effects after planned implementation:
  - recommendation runtime Redis keys store derived state only
  - recommendation recompute/invalidation must refresh PM summary composition on the next read
- Recommendation telemetry side effects:
  - Telemetry persistence is currently disabled (no-op hook); recommendation display and feedback flows remain functionally unchanged.
- Supporting recommendations cache side effects:
  - `GET /api/projects/{project_id}/recommendations` may return short-TTL cached payloads (`60-120s`) from Redis derived state.
  - recommendation orchestration write paths invalidate this short cache to reduce stale windows.
  - threshold override writes invalidate supporting recommendations cache + PM summary cache to apply new thresholds on next read.
- Cache bypass on manual refresh:
  - `trigger=manual_refresh` path in `api/app/projects/router.py`
- Cache invalidation on task/integration update flows:
  - `api/app/integrations/router.py`
  - `api/app/routes/jira_webhooks.py`
  - `api/app/tasks/brain_sync.py`

## Allowed Fallbacks
- Empty states when no tasks/work items are available.
- If summary fetch is delayed/aborted, UI retains safe loading/“sync delayed” behavior and retries via PM board refresh flow.

## Not Fully Verified Yet
- Full parity of all alert/recommendation ranking logic between PM Board cards and Pulse analytics widgets was not fully audited line-by-line.

## Approved Next Contract Changes (Not Yet Implemented)
- Canonical work-item cutover (ADR-0020):
  - health source query semantics move from Brain `entity_type == "task"` to canonical `entity_type == "work_item"`.
  - canonical typing semantics used by backend logic:
    - `work_item_type` (platform semantics)
    - `provider_type` (provider-native fidelity)
  - no long-lived dual-read compatibility contract for `task` + `work_item` queries.
  - live migration runs via background job + progress endpoint; migration window may present partial dataset visibility until backfill advances.
  - canonical type-dependent runtime activation follows coverage wait gate (`work_item_type` backfill >= 99.9%).
- Backend recommendation ownership moves to a provider-agnostic recommendation engine over normalized task/comment/cadence signals.
- Frontend recommendation generation in `projects-page.tsx` is transitional and should be replaced by backend-composed recommendation payloads.
- Planned Redis derived-state contract for recommendations:
  - `reco:project:{project_id}`
  - `reco:task:{project_id}:{task_key}`
  - `reco:meta:{project_id}`
  - `reco:dirty:{project_id}:{task_key}`
  - `reco:dirty:set:{project_id}`
- Planned scheduler ownership:
  - centralized scheduler runs full recommendation recompute
  - event-driven triggers mark impacted tasks for incremental recompute
