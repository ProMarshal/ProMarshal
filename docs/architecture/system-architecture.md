# ProMarshal System Architecture (Code-Verified, Detailed)

Last verified against code: 2026-03-22.

This document includes only behavior confirmed directly in code.
Approved future-state architecture changes are listed separately in a clearly marked section and are not code-verified yet.

## 1) Process Topology

### 1.1 Frontend (Next.js)
- Route entry: `web/app/(dashboard)/projects/page.tsx`
- Main state/orchestration UI: `web/components/projects/projects-page.tsx`
- Pulse Project Health table/hierarchy UI: `web/components/projects/project-health-hierarchy.tsx`

### 1.2 API (FastAPI)
- App/lifespan: `api/app/main.py`
- Startup sequence in lifespan:
  1. `install_builtin_bundles()`
  2. `connect_to_mongo()`
  3. `initialize_redis()`
  4. Optional Groq init (`initialize_llm_service`) when configured
- Routers mounted in `main.py`:
  - auth, users, projects, integrations, slack commands, slack interactions, jira webhooks, cron, slack hourly reader, planner, planner agent.

### 1.4 API Ingress Trust Model (Current Runtime)
- User-facing protected routes resolve actor identity from backend JWT (`Authorization: Bearer ...`) via shared auth dependency (`get_current_user`).
- Internal server-to-server routes use `X-Internal-Secret` + `X-Request-Timestamp` replay-window validation.
- Inbound Slack/Jira webhook routes use standardized verifier dependency (`require_webhook_auth("<provider>")`) registered at startup.
- Production/docs behavior:
  - FastAPI docs/openapi endpoints are disabled when `ENVIRONMENT=production`.
  - `/health` remains liveness-only.
  - `/readyz` is readiness-only and verifies MongoDB + Redis before returning 200.

### 1.3 Workers (Dramatiq)
- Worker boot script: `api/start_dramatiq_worker.py`
- Supported queues (declared): `chat_interactions`, `workitem_events`, `chat_commands`, `cortex_runs`, `action_item_extraction`
- Queue bootstrap: `api/app/core/redis_queue.py`
  - Queue names and default timeouts:
    - `chat_interactions` (300s)
    - `workitem_events` (180s)
    - `chat_commands` (300s)
    - `cortex_runs` (`settings.cortex_run_timeout`)
    - `action_item_extraction` (300s)

## 2) Data Plane Design

## 2.1 Mongo databases
- Default DB (from connection URI default database) for control-plane + app metadata:
  - `projects`
  - `users`
  - `channel_index`
  - `session_index`
  - `project_schedules`
  - `schedule_runs`
  - other auth/support collections (for example `otps`)
- Brain DB (`settings.brain_db_name`) for project-scoped operational entities:
  - one collection per project, named by `project_id` string (`get_brain_collection(project_id)` in `core/database.py`)
  - stores work items and session payload docs by `entity_type`.

Detailed inventory and lifecycle table:
- `docs/architecture/database-collections-reference.md`

## 2.2 Key Mongo index families (verified)
- `projects`:
  - unique `project_id`
  - Slack resolver hot-path compound indexes (team/status/channel/member email/member slack ID)
- `channel_index`:
  - unique `(provider, workspace_id, external_user_id)`
  - lookup by `(provider, workspace_id, active_project_id, updated_at)`
- `session_index`:
  - unique `(source, session_id)` and `(source, session_key)`
  - active-session/identity correlation compound indexes for cadence/team poll routing and cleanup
- scheduler:
  - `project_schedules` unique `schedule_id`, unique `(project_id, job_type)`, due lookup index
  - `schedule_runs` unique `run_id`, unique `idempotency_key`, run history index by schedule and created_at.

## 2.3 Redis keyspaces (verified in code)
- PM summary cache:
  - `pm_board_summary:v1:{custom_project_id}` (`projects/pm_board_cache.py`)
- Cortex session runtime:
  - `cortex:session:v1:{session_key}` (`cortex/session_store_redis.py`)
- Cortex queue reliability keys (`cortex/queue_runtime.py`):
  - run lock: `cortex:run:{session_key}`
  - per-session queue: `cortex:queue:{session_key}`
  - inflight item: `cortex:inflight:{session_key}`
  - requeue guard: `cortex:requeued:{queued_msg_id}`
  - dispatch guard: `cortex:dispatch:{session_key}`
- Slack ingress dedupe/response gates (`integrations/router.py`):
  - event dedupe: `cortex:slack:event:{event_id}`
  - semantic dedupe: `cortex:slack:semantic:{semantic_key}`
  - response dedupe: `cortex:slack:response:{gate_key}`
- Slack resolver default-project compatibility key (`integrations/slack/project_resolver.py`):
  - `cortex:default_project:{team_id}:{slack_user_id}`.

Approved next keyspaces (not yet in code):
- Recommendation runtime state:
  - `reco:project:{project_id}`
  - `reco:task:{project_id}:{task_key}`
  - `reco:meta:{project_id}`
  - `reco:dirty:{project_id}:{task_key}`
  - `reco:dirty:set:{project_id}`

## 3) Shared Control Planes

## 3.1 Scheduler control plane
- Core tick engine: `api/app/scheduler/engine.py`
- Repository and concurrency primitives: `api/app/scheduler/repository.py`
- Provision helpers: `api/app/scheduler/service.py`
- Trigger routes: `api/app/routes/cron.py`
- Executor dispatch table: `api/app/scheduler/executors.py`
  - `cadence_reminder`
  - `sessions_cleanup`
  - `slack_hourly_reader`
  - `team_poll_cycle`

Tick lifecycle:
1. list due schedules by `job_type`
2. lease acquisition (`acquire_lease`)
3. idempotent run insert (`create_run` with due-bucket idempotency key)
4. executor run
5. run completion update
6. next-run computation and schedule update
7. lease release.

## 3.2 Shared agent runtime
- Runtime wrapper: `api/app/agent_runtime/runtime.py`
- Cortex uses shared runtime in orchestrator turn path.
- Cadence uses shared runtime via cadence agent runtime bindings.
- Runtime owns execution loop; routing/state-transition decisions stay in feature orchestrators.
- Team Poll owner free-text initiation defaults to Cortex tool arbitration (`team_poll_owner_free_text_via_cortex=true`),
  with legacy owner-ingress retained as flag-gated fallback.
- Cadence completion path includes a non-blocking post-cadence Team Poll reminder sidecar for unresolved pending poll responses (interactive responded sessions only).

## 3.3 Capability extension plane
- Built-in capability bundles are installed at startup.
- Bundle registry/models are in `api/app/domain/capabilities/*`.
- This is the extension point for adding provider/tool capability surfaces.
- Shared LLM gateway contracts now include embedding operations, used by Team Poll pending-relevance evaluation.

## 4) PM Board + Pulse Architecture (Detailed)

## 4.1 Single backend composition path
- PM composed endpoint:
  - `GET /api/projects/{mongo_project_id}/pm-board-summary`
  - implemented in `api/app/projects/router.py`
- It gathers (via `asyncio.gather`) health + task buckets + review signals + planner status + forecast signals.

## 4.2 Health computation source
- Live compute from Brain tasks (`entity_type == "task"`) via:
  - `_compute_project_health_live(...)`
  - `HealthService.calculate_health(tasks)`
- Compatibility endpoint still exists:
  - `GET /api/projects/{custom_project_id}/project-health`
  - same live compute source.

## 4.3 PM summary caching
- Read-through Redis cache (`pm_board_summary:v1:*`) in composed endpoint.
- Trigger `manual_refresh` bypasses cache.
- Cache TTL from `settings.pm_board_summary_cache_ttl_seconds` (lower-bounded to 5s in code).
- Invalidation called on Jira sync/webhook and task mutation sync paths.

## 4.4 Frontend fetch model
- PM/Pulse fetches the composed endpoint from `projects-page.tsx`.
- Guards implemented:
  - in-flight promise dedupe map
  - abort controller for stale request cancellation
  - request timeout guard
  - non-fatal abort/cleanup error handling.
- Refresh behavior:
  - immediate refresh on PM Board entry/project switch
  - 5-minute polling while PM Board is visible
  - 5-second retry loop if initial summary not loaded.

## 4.5 URL/state contract
- PM card click deep-link sets URL to Pulse Project Health + `healthStatus`.
- URL hygiene removes `healthStatus` outside `nav=Pulse & tab=Project Health`.
- Pulse health table consumes URL filter and keeps URL synchronized with UI filter state.

## 4.6 Approved recommendation subsystem changes (not yet in code)
- Recommendation generation will move to a backend-owned provider-agnostic recommendation subsystem.
- `GET /api/projects/{project_id}/pm-board-summary` remains the sole PM/Pulse UI-facing contract for recommendation display.
- A separate `GET /api/projects/{project_id}/recommendations` endpoint may exist as a supporting/internal surface only; it must not become the PM/Pulse frontend fetch path.
- Canonical recommendation inputs remain Brain tasks plus related comments and cadence/session-derived signals.
- Derived recommendation runtime state is stored in Redis; summary composition reads and/or projects that derived state into PM Board and Pulse responses.
- Current frontend recommendation builders in `projects-page.tsx` are transitional and should be retired once backend-composed recommendation payloads are shipped.

## 5) Slack Ingress Runtime Architecture

Main ingress endpoint: `POST /api/integrations/slack/events`.

Deterministic routing precedence in integration router:
1. Pending interaction pre-resolution route (`team_poll` then `action_item`).
2. Cadence DM identity route lookup and terminal handling.
3. Team Poll legacy owner-ingress route only when `team_poll_owner_free_text_via_cortex=false`.
4. Project resolution (`resolve_slack_project`) using team/member context and active project context.
5. Scope + clarification gates.
6. Stage-3 ack send (dedupe-gated).
7. Cortex handoff (`handoff_slack_event_to_cortex`).

Additional ingress protections:
- duplicate event suppression (event id + semantic key)
- duplicate response suppression using response gate slots.

Pending Team Poll free-text relevance handling:
- deterministic safety checks
- embedding similarity high-relevance accept
- shared-gateway LLM fallback classifier for non-high-relevant replies
- reminder fallback for non-answer/low-confidence responses.

## 6) Project Resolution and Context Binding

Resolver module: `api/app/integrations/slack/project_resolver.py`.

Resolution characteristics verified:
- DM fast-path first checks persisted active project context.
- Active project context is stored in `channel_index` via:
  - `read_active_project`
  - `write_active_project`
  - `clear_active_project`
- Membership matching is Slack-user-ID based in resolver's active-member path.
- If ambiguous in DM, resolver returns candidate project list; ingress sends selection guidance.

## 7) Session Architecture

## 7.1 Shared session index
- Repository: `api/app/sessions/repository.py`
- Purpose:
  - identity lookup for active sessions
  - project resolution for session payload rows
  - cross-feature active/timeout polling and cleanup queries.

## 7.2 Cortex session runtime
- Active session payload in Redis (`cortex:session:v1:*`).
- Session key built from `(project_id, user_id, source, anchor)`.
- Worker enforces per-session isolation and serialized execution via Redis run locks + queueing.

## 7.3 Cadence/Team Poll sessions
- Stored via `SessionRepository` in project-scoped Brain collection rows.
- Index metadata mirrored into `session_index` for lookup/routing.

## 8) Scheduler + Cron Surface

Primary cron route:
- `/api/cron/tick` -> centralized scheduler dispatcher (fast lane + daily lane + maintenance lane), including cadence reminder execution (`cadence_reminder`).

Legacy compatibility routes:
- `/api/cron/cadence-reminders` -> legacy cadence reminder trigger (skipped with `dispatcher_mode` when dispatcher is enabled)
- `/api/cron/slack-messages/hourly` -> legacy slack hourly reader trigger (skipped with `dispatcher_mode` when dispatcher is enabled)
- `/api/cron/sessions-cleanup` -> legacy sessions cleanup trigger (skipped with `dispatcher_mode` when dispatcher is enabled)
- `/api/cron/team-poll-cycle` -> legacy team poll trigger (skipped with `dispatcher_mode` when dispatcher is enabled)
- `/api/cron/calculate-project-health` -> compatibility `noop` (health snapshots disabled)

All cron routes enforce bearer auth using `settings.cron_secret`.

Detailed trigger/frequency matrix:
- `docs/architecture/cron-scheduler-reference.md`

Approved next scheduler extension (not yet in code):
- Recommendation full recompute will be owned by the centralized scheduler using `project_schedules` and `schedule_runs`.
- Incremental recommendation recompute remains event-driven, but uses the same recommendation engine and runtime-state contract.

## 9) Jira Webhook and Brain Sync

Webhook ingress:
- `POST /api/jira/webhooks/{project_id}/task-updated?token=...` in `routes/jira_webhooks.py`.
- Queue-first processing to `workitem_events` queue when available, inline fallback when unavailable.

Processing behavior:
- Enforce webhook auth via `require_webhook_auth("jira")` with per-project URL token verification.
- Resolve project routing directly by webhook path `project_id`.
- Upsert/delete Brain task rows.
- Invalidate PM summary cache for the resolved project.

## 10) End-to-End Topology (ASCII)

```text
                                 +--------------------------+
                                 |        Next.js UI        |
                                 | ProjectsPage + Pulse PH  |
                                 +------------+-------------+
                                              |
                       GET /api/projects/{id}/pm-board-summary
                                              |
                                              v
+----------------------+           +----------+-----------+          +------------------+
|     FastAPI API      |<--------->| Redis (cache/queue) |<-------->| Dramatiq Workers |
|  main.py routers     |           | pm cache + locks +  |          | cortex/jira/etc. |
+----------+-----------+           | dedupe + sessions    |          +--------+---------+
           |                       +----------------------+                   |
           |                                                               run jobs
           v                                                                   |
 +---------+------------------------------+                                    v
 | Mongo default DB                       |                        +-----------+-----------+
 | projects, users, channel_index,        |                        | Feature runtimes      |
 | session_index, project_schedules, runs |                        | Cortex/Cadence/Poll   |
 +---------+------------------------------+                        +-----------+-----------+
           |
           v
 +---------+------------------------------+
 | Brain DB (collection per project_id)   |
 | tasks, sessions, summary entities       |
 +----------------------------------------+
```

## 11) Slack DM to Cortex Sequence (ASCII)

```text
Slack DM event
   -> integrations/router.py
      -> route_pending_dm_interactions() handled => STOP
      -> cadence identity route? handled => STOP
      -> legacy team_poll owner ingress? handled => STOP (only if flag-disabled)
      -> resolve_slack_project() [channel_index/default project/member match]
      -> scope/clarification gates
      -> ack gate (optional async ack)
      -> handoff_slack_event_to_cortex()
         -> enqueue cortex_runs
            -> cortex/worker.py
               -> lock+queue isolation
               -> CortexOrchestrator.handle_turn()
                  -> shared_agent_runtime.run()
                     -> tool execution
                     -> response output
```
