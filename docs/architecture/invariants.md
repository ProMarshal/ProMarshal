# Architecture Invariants

Last verified against code: 2026-04-14.

Only items below were validated directly in code. Any unverified assumptions are listed separately.

## 1) PM Board/Pulse Data Source and Health Consistency

- PM Board summary and Pulse inputs must come from the composed backend endpoint:
  - `GET /api/projects/{project_id}/pm-board-summary`
  - Source: `api/app/projects/router.py` (`get_pm_board_summary`)
- Health payload is computed live from Brain `entity_type == "work_item"` and returned as `health`:
  - `_compute_project_health_live(...)` in `api/app/projects/router.py`
  - `HealthService.calculate_health(tasks)` in `api/app/projects/health_service.py`
- Frontend must apply health cards/hierarchy from this same `health` payload:
  - `applyPmBoardSummaryData(...)` in `web/components/projects/projects-page.tsx`
  - Pulse Project Health receives `initialHealthData={healthHierarchy}` in `ProjectsPage`.
- Health rules are centralized in one location:
  - `api/app/projects/health_rules.py` (`HealthRules.calculate_health`)
  - Includes blocker checks, overdue/start-date rules, due-date windows, completion handling.

## 2) PM Board Cache Contract (Redis)

- PM Board summary cache key format is fixed: `pm_board_summary:v1:{custom_project_id}`:
  - `api/app/projects/pm_board_cache.py`
- Cache is read-through and bounded by TTL (`pm_board_summary_cache_ttl_seconds`, min 5s):
  - `get_cached_pm_board_summary(...)`, `set_cached_pm_board_summary(...)`
- Manual refresh trigger bypasses Redis cache:
  - `bypass_cache = normalized_trigger in {"manual_refresh"}` in `api/app/projects/router.py`
- On Redis cache miss, PM summary route can respond `202 processing` and enqueue recompute:
  - `GET /api/projects/{project_id}/pm-board-summary` (accepted processing contract)
  - status probe: `GET /api/projects/{project_id}/pm-board-summary-status`
  - async recompute worker entrypoint: `app.projects.router.process_pm_board_summary_job`
- Cache invalidation must occur on task/workitem-changing integration flows:
  - Jira webhooks and integration updates invalidate PM cache (`api/app/integrations/router.py`, `api/app/routes/jira_webhooks.py`, `api/app/tasks/brain_sync.py`).

## 3) URL/Navigation Hygiene for Health Filters

- `healthStatus` query param is valid only for `nav=Pulse` and `tab=Project Health`:
  - Enforced in `web/components/projects/projects-page.tsx` (URL sync + hygiene effects).
- PM Board -> Pulse deep link must set URL first and local state follows URL:
  - `openPulseProjectHealthByStatus(...)`, `openPulseProjectHealthFocus(...)`.
- During navigation intent, stale PM Board local state must not overwrite URL:
  - `pulseHealthNavIntentRef` guard in URL sync effect.

## 4) Integration Category Gating (Frontend)

- PM Board quick-action/tool-connected behavior is category-based, not provider-name-based:
  - `hasConnectedCategory(project, category)` in `web/components/projects/projects-page.tsx`
  - Used with `communication` and `workitem`.
- Integration records currently persist categories in project integrations:
  - Slack connect writes `category: "communication"` (`api/app/integrations/router.py`)
  - Jira connect/selection writes `category: "workitem"` (`api/app/integrations/router.py`)

## 5) Session Isolation and Binding

- Cortex session key format is canonical and actor/project-bound:
  - `build_session_key(project_id, user_id, source, anchor)` returns `"{project_id}:{user_id}:{anchor}"` (`api/app/cortex/sessions.py`)
- `session_index` is the shared lookup/index plane for session-to-project resolution across Cadence/Cortex/Team Poll flows.
  - Repository access: `api/app/sessions/repository.py`
  - Collection indexes: `api/app/core/database.py`
- Active Cortex sessions are stored in Redis (`cortex:session:v1:*`) with expiry and refresh on activity:
  - `api/app/cortex/session_store_redis.py`
  - `CortexSessionManager` in `api/app/cortex/sessions.py`
- Worker enforces session isolation between drain key and ingress key:
  - Mismatch reroutes to correct session queue (`api/app/cortex/worker.py`, primary ingress isolation guard).

## 6) Team Poll/Cadence Routing Safety

- Team Poll active-session DM routing is terminal when handled (must not fall through to Cortex):
  - `api/app/integrations/router.py` (`team_poll_route_hit_identity` path returns immediately)
- Owner poll legacy ingress handling is terminal when handled, but is flag-gated:
  - `team_poll_owner_free_text_via_cortex=true` (default) routes owner free-text initiation through Cortex tools.
  - `team_poll_owner_free_text_via_cortex=false` enables legacy `team_poll_owner_route_terminal` ingress branch.
- Team Poll owner/member identity checks are explicit:
  - `owner_user_id == actor_user_id` checks in `api/app/team_poll/interaction_handler.py`

## 7) Reliability/Abort Behavior (Frontend PM/Pulse Fetch)

- In-flight dedupe + abort-safe fetch path is required for PM Board summary fetch:
  - `pmBoardSummaryInFlightRef`, `pmBoardSummaryAbortRef` in `web/components/projects/projects-page.tsx`
- Frontend must handle async processing responses from PM summary:
  - `202 processing` response from `/pm-board-summary` sets processing mode
  - status polling via `/pm-board-summary-status`
  - re-fetch summary on `status=ready`
- Abort-like conditions (`effect_cleanup`, `stale_request`, timeout, AbortError) are treated as non-fatal and should not surface as user errors.

## 8) Cortex Self-Scope and Assignee Identity Invariants

- Task-read self-scope prompts (for example, "my tasks") must resolve actor identity before read execution.
- Jira task-read tools enforce self-scope guardrail and converge resolved self-scope to specific assignee filtering.
- Assignee matching policy in task-read repository is strict:
  - If assignee user/account ID is available, matching is ID-first.
  - Email/name matching is only fallback when assignee ID is not available.
- This invariant exists to keep equivalent "my tasks" prompts stable across phrasing variants.

## 9) Team Poll Rewrite and Skip Invariants

- Team Poll active DM routing remains terminal and must not fall through to Cortex.
- Owner skip via button and owner free-text skip must resolve to the same action outcome and acknowledgement contract.
- Team Poll question rewrite policy:
  - LLM rewrite with retries/backoff is primary.
  - Deterministic rewrite is fallback only when retries are exhausted or output is invalid.
  - Scope-drift lexical rejection gate remains intentionally disabled (false-reject mitigation).
## 10) Centralized Scheduler Control Plane

- Scheduled execution is centralized through `SchedulerEngine` and scheduler collections, not feature-specific cron loops.
  - Engine: `api/app/scheduler/engine.py`
  - Repository: `api/app/scheduler/repository.py`
  - Models/timecalc: `api/app/scheduler/models.py`, `api/app/scheduler/timecalc.py`
- Cron endpoints trigger scheduler ticks per job type and keep backward-compatible endpoint contracts.
  - `api/app/routes/cron.py`
- Active job types include `cadence_reminder`, `cadence_expiry`, `sessions_cleanup`, `slack_hourly_reader`, `team_poll_cycle`.
- Scheduler state is centralized in Mongo collections:
  - `project_schedules` (timing, lease, next run)
  - `schedule_runs` (run lifecycle + idempotency)
- Multi-instance safety guarantees depend on both:
  - lease acquisition/release (`acquire_lease`, `release_lease`)
  - per-slot run idempotency (`idempotency_key` unique in `schedule_runs`)
  - Source: `api/app/scheduler/repository.py`
- Global health snapshot cron is intentionally disabled; PM health is live-computed and Redis-cached.
  - `POST /api/cron/calculate-project-health` returns `noop`
  - `api/app/routes/cron.py`
- Schedule ownership is service-managed (seeding/upsert), not per-feature ad hoc state.
  - `api/app/scheduler/service.py`

## 11) Shared Agent Runtime Execution Path

- Cortex and Cadence both execute LLM/tool turns through shared runtime orchestration instead of separate ad hoc execution loops.
  - Runtime: `api/app/agent_runtime/runtime.py` (`SharedAgentRuntime`)
  - Context contract: `api/app/agent_runtime/context.py` (`AgentContext`)
- Cortex path uses shared runtime in orchestrator turn handling.
  - `api/app/cortex/orchestrator.py` (`shared_agent_runtime.run(...)`)
- Cadence path uses a Cadence-scoped shared runtime instance with Cadence tool descriptors.
  - `api/app/cadence/agent_tools.py` (`cadence_agent_runtime`)
  - `api/app/cadence/orchestrator.py`
- Tool execution policy guarantees (approval/idempotency/replay guards) are enforced in shared executor path.
  - `api/app/agent_runtime/executor.py`

## 12) Capability Bundle Registry as Extension Plane

- New tool/provider capabilities are installed through capability bundles and startup installation, not by adding provider-specific branches in shared orchestrators.
  - Bundle model: `api/app/domain/capabilities/bundle_models.py`
  - Registry: `api/app/domain/capabilities/bundle_registry.py`
  - Startup install: `api/app/domain/capabilities/startup.py`
- Bundle registration is the control point for planner patterns and cadence adapters to remain tool-agnostic in shared runtime paths.

## 13) Provider-Agnostic Task Read Repository Dispatch

- Task-read operations are routed via provider dispatcher/repository abstraction, not direct provider-specific data access from orchestrator logic.
  - Dispatcher: `api/app/tasks/read_repository/dispatcher.py`
  - Base contract: `api/app/tasks/read_repository/base.py`
  - Provider repositories (current):
    - `api/app/tasks/read_repository/providers/jira_repository.py`
    - `api/app/tasks/read_repository/providers/linear_repository.py`
- This separation is required to preserve future provider onboarding without core orchestrator rewrites.

## 14) Channel Index Authoritative DM Project Context

- Active DM project context resolution uses `channel_index` as authoritative source; Redis/default-project fast path acts as performance path but must not override authoritative Mongo context.
  - Resolver + precedence: `api/app/integrations/slack/project_resolver.py`
  - Index helper: `api/app/integrations/channel_index.py`
  - Collection/indexes: `api/app/core/database.py` (`channel_index` indexes)
- Slack member identity resolution is strict ID-first; controlled email fallback is allowed only on strict-match miss and must persist healed `members[].integration_ids.slack_user_id` mapping when validated.
  - Resolver healing path: `api/app/integrations/slack/project_resolver.py`
  - Member sync/connect healing: `api/app/integrations/slack/member_sync.py`, `api/app/integrations/router.py`
- Cadence/Team Poll terminal DM routing depends on this resolution path before Cortex handoff.
  - `api/app/integrations/router.py`

## 15) Cadence Active-Session Scope (Current Runtime)

- Current runtime enforces Cadence single-active scope as `global`.
  - Declared default: `api/app/core/config.py` (`cadence_single_active_scope: "global"`)
  - Normalization override: `apply_cortex_defaults` sets `self.cadence_single_active_scope = "global"`.
- This must be treated as current behavior contract until an explicit cutover changes it.
- Historical execution docs that reference project-scoped production target are rollout history, not current runtime truth.

## 30) Cadence DM Queue Isolation and Session Lease

- Cadence DM session turns are queue-backed and must not execute as long-running in-process FastAPI event-loop tasks.
  - Slack DM router enqueues Cadence turns to Dramatiq `cadence_dm` queue.
  - Runtime enqueue path uses shared queue backend abstraction.
- Per-session concurrency is serialized by Redis lease + fencing token:
  - lock key: `cadence:{session_id}:lock`
  - fence key: `cadence:{session_id}:fence`
- Lease behavior contract:
  - contention (`token>0`, lock not acquired): skip overlapping turn for same session.
  - lease backend unavailable (`token=0`): fail-open and continue Cadence turn execution.
- Cadence terminal routing precedence over Cortex remains unchanged; queue isolation changes execution topology, not routing semantics.

## 18) Cadence Timeout/Summary Correctness Boundary

- Cadence source session documents in project Brain collections remain the canonical correctness plane for timeout finalization and daily-summary eligibility.
- `session_index` is an acceleration/index plane and may be used for fast-path lookups, but timeout/summary behavior must remain correct when index rows drift.

## 16) Cortex Follow-Up Mutation Target Grounding

- Follow-up non-idempotent writes must not rely on transcript-only interpretation when target identifiers are present.
- Shared runtime must carry structured target references (`target_refs`) from successful tool outcomes.
- Non-idempotent write execution is fail-closed when identifier-like targets are not grounded to current message or session snapshot context.
- Prompt/runtime context may include a compact structured session snapshot to reduce token-heavy transcript dependence while preserving deterministic target provenance.

## 17) Canonical Due-Date Filter Semantics for Task Reads

- Task-read intent parsing must normalize due-date scope into canonical `due_date_mode` (`any|missing|present`).
- Search/list/analyze provider paths must apply the same due-date semantics from shared filter contract rather than model-only inference.
- Equivalent due-date prompts should not diverge by tool path (count/list parity for same normalized filters).

## 19) Shared Pending-Interaction Routing Precedence

- Pending-interaction routing is centralized and evaluated before general Cortex free-chat fallback.
  - Router: `api/app/integrations/pending_interactions_router.py`
- Handler precedence is deterministic: `team_poll` first, then `action_item`.
- Failure policy is explicit and feature-scoped:
  - Team Poll pending handler is fail-closed.
  - Action Item pending handler is fail-open.

## 20) Canonical Comment-Mutation Event Contract for Action-Item Extraction

- Comment-origin extraction payloads are normalized to a canonical event contract before queue enqueue.
  - Contract: `api/app/action_items/comment_mutation_event.py`
- Task comment mutation producers and webhook ingress use this contract (with compatibility fields retained during transition):
  - Task mutation path: `api/app/tasks/service.py`
  - Jira webhook comment-create path: `api/app/routes/jira_webhooks.py`
- Extraction worker validates normalized event payloads and skips malformed events deterministically.
  - Consumer: `api/app/action_items/extraction.py`

## 21) Shared LLM Gateway Adoption for Non-Recommendation Feature Paths

- Non-recommendation feature LLM calls are routed through shared `app.llm` gateway contracts.
- Implemented paths include:
  - Team Poll summary/question rewriter
  - ProMarshal response composer
  - Cadence LLM service and Cadence orchestrator extraction/classifier callsites
- Direct feature-local `litellm.acompletion(...)` callsites are not used in these migrated paths.

## 22) Single-Source Scope Registry and Stage-2 Precedence

- Deterministic scope vocabulary/reason-codes/signals are owned by canonical scope registry:
  - `api/app/domain/scope/registry.py`
- Stage-1 deterministic scope gate consumes canonical registry:
  - `api/app/domain/scope/gate.py`
- Legacy facade remains a thin adapter over canonical gate outputs (no duplicate analytics lexical logic):
  - `api/app/promarshal/scope_policy.py`
- Stage-2 scope classifier is fallback for uncertain/weak cases and must not downgrade strong deterministic project-related matches.
- Greeting classification is deterministic pass-through and must not be downgraded by Stage-2.

## 23) Pending Team Poll Relevance and Post-Cadence Reminder Safety

- Pending Team Poll DM responses in `free_text` mode are guarded by a relevance decision chain:
  - deterministic safety checks first (owner controls + high-confidence workitem mutation bypass)
  - embedding high-relevant accept path
  - LLM fallback classifier for non-high-relevant cases
  - fail-safe reminder path when classifier is unavailable/low-confidence/non-answer
- Pending poll reminder response is non-consumptive (does not mark response persisted).
- Post-cadence reminder sidecar is best-effort only and does not affect Cadence terminal success:
  - trigger only for interactive responded cadence sessions (`outcome.response_status=responded`)
  - unresolved Team Poll member sessions only
  - deterministic multi-poll selection when multiple pending rows exist
  - cooldown key is capped by remaining poll TTL
  - Redis outage is fail-open for reminder gate.

## 24) Action Item Create/List Runtime Guarantees

- Action-item `display_key` prefix is canonical `ACTION-<n>` with project-scoped atomic sequence allocation.
- Resolver paths for action-item refs must treat `ACTION-*` as canonical and must not depend on `ACT-*` / `AI-*` legacy prefixes.
- Cortex action-item create path uses staged owner resolution:
  - deterministic owner-intent parse first
  - LLM owner-hint extraction fallback only when explicit intent lacks strong deterministic hint
  - strong unresolved owner hints fail closed; weak unresolved `not_found` hints may degrade to unassigned.
- Action-item list default status scope is open when status is omitted; `status=closed` maps to `{done,cancelled}`.
- Auto-detected title extraction is deterministic-first with shared LLM rewrite enhancement (confidence/retry bounded) and deterministic fallback.

## 25) Work-Item Parent Linking Mutation Boundary

- Parent linking updates for Jira work items must flow through the shared task mutation path (DTO -> TaskService -> JiraAdapter -> Jira OAuth), not a side-channel write path.
- Parent-link validity checks must run before Jira write commit using shared parent-rule resolution/filtering contracts; invalid hierarchy links return deterministic validation outcomes.
- Optional-parent work-item creates remain non-blocking; create success may include best-practice guidance to map a parent later, but must not trigger implicit pending-parent clarification.

## 25) Scheduler Lane and Retention Cleanup Guarantees

- `/api/cron/tick` remains the single external scheduler trigger and control-plane entrypoint.
- Dispatcher orchestration is lane-aware in route-level job loops:
  - product-oriented jobs execute before maintenance cleanup jobs.
- Global maintenance schedules are single shared schedule rows (`project_id=null`) in `project_schedules`.
- Per-project summary cleanup schedule rows are project-scoped (`project_id=<proj-...>`).
- `pending_interactions` expiry has TTL fallback (`expires_at` TTL index) while terminal delete-on-resolution remains primary behavior.
- `schedule_runs` retention cleanup relies on age-based prune queries with dedicated `created_at` cleanup index.

## 26) Project Lifecycle Owner Controls and Hard-Delete Boundary

- Project settings and destructive project lifecycle controls are owner-only capabilities.
  - Frontend owner gating is UX-only; backend authorization remains the security boundary.
- `DELETE /api/projects/{project_id}` enforces owner authorization and supports deterministic response contract:
  - `204` delete success
  - `403` non-owner
  - `404` project missing/inaccessible
  - `409` `delete_in_progress` for concurrent delete on the same project
- Project hard-delete orchestration uses per-project lock + idempotent replay-safe sequencing.
- Required cleanup must complete before primary `projects` row delete:
  - `project_schedules` cleanup
  - Brain collection drop (`{custom_project_id}`)
  - filesystem cleanup (`uploads/{custom_project_id}/`)
- Best-effort cleanup must not block final delete but must emit structured diagnostics:
  - metadata/index collections (`session_index`, `pending_interactions`, `channel_index`, planner/docs/slack metadata)
  - Redis runtime/cache keys (including value-based `cortex:default_project:*` sweep)
  - optional integration side effects (Jira webhook deregistration attempt, Neo4j project-scope cleanup)
- In-flight project-scoped executors must gracefully skip deleted/missing projects with deterministic `project_not_found` semantics.

## 27) Cadence/Digest Schedule Policy Invariants

- Project-scoped daily cadence and action-item digest schedules support per-project policy flags in `schedule_spec`:
  - `skip_weekends` (cadence + digest)
  - `ignore_followup_for_owner` (digest/follow-up policy)
- Weekend policy is enforced at both levels:
  - scheduler daily next-run calculation
  - executor runtime guard for cadence/digest sends
- Follow-up owner suppression applies only to owner-targeted escalation/reminder delivery and does not disable actor-side follow-up reminders.

## 28) Planner/Charter Read Cache Contract (Redis)

- Planner read-path caches are Redis-derived only; Mongo Brain remains canonical for planner entities/discussions.
- Read-through keys:
  - `planner_status:v1:{project_id}`
  - `planner_entities:v1:{project_id}:{stage}`
  - `planner_current_stage:v1:{project_id}`
- Cached read endpoints:
  - `GET /api/planner/{project_id}/status`
  - `GET /api/planner/{project_id}/entities/{stage}`
  - `GET /api/planner/{project_id}/current-stage`
- Mutation routes invalidate planner status/current-stage plus impacted stage/all-stage entities.
- Redis cache failures are fail-open for planner read routes (fallback to canonical planner service reads).

## 29) Planner/Charter Bootstrap and Stage-Selection Determinism

- Charter page initialization uses a single bootstrap read contract:
  - `GET /api/planner/{project_id}/bootstrap`
- Bootstrap is the primary source for initial charter mode, topics, status/current stage, and documents during page mount.
- Frontend stage selection must not depend on competing asynchronous initializers (`status` vs `current-stage`) that can overwrite each other after first paint.
- Stage navigation must remain non-blocking; history probing can refine mode asynchronously but should not delay tab switching.

## Approved Next Invariants (Not Yet Verified in Code)

### 16) PM Recommendation Composition Contract
- PM recommendation display for PM Board and Pulse must continue to flow through `GET /api/projects/{project_id}/pm-board-summary`; recommendation rollout must not create a second competing PM/Pulse frontend fetch path.
- A separate `GET /api/projects/{project_id}/recommendations` route, if implemented, is supporting/internal only and must not replace summary composition as the PM/Pulse UI contract.
- Backend recommendation generation becomes the source of recommendation text/ranking; frontend clients render backend payloads and must not remain the long-term recommendation rule owner.

### 17) PM Recommendation Runtime State
- Mongo Brain task entities, related comments, and cadence/session-derived signals remain canonical inputs for recommendation generation.
- Redis recommendation keys are derived runtime state only and must not become the long-term source of truth.
- Planned recommendation key families:
  - `reco:project:{project_id}`
  - `reco:task:{project_id}:{task_key}`
  - `reco:meta:{project_id}`
  - `reco:dirty:{project_id}:{task_key}`
  - `reco:dirty:set:{project_id}`
- PM summary cache invalidation/composition must reflect recommendation runtime updates on the next summary read.

### 18) PM Recommendation Scheduling and Rule Ownership
- Full recommendation recompute must run through the centralized scheduler control plane, not through feature-specific cron loops.
- Incremental recommendation recompute may be event-driven, but it must reuse the same backend recommendation engine and schema as full recompute.
- Recommendation rules should operate on a normalized provider-agnostic model so provider-specific fields do not leak into core rule evaluation.
- Shared LLM gateway extraction may enrich recommendation signals, but deterministic backend recommendation logic remains the final authority for severity and action.

### 19) Canonical Work-Item Domain Hard Cutover (ADR-0020)
- Runtime canonical entity semantics move to `entity_type="work_item"` with provider-fidelity and platform-semantic split:
  - `provider_type` (raw provider-native type)
  - `work_item_type` (canonical semantic class used by shared logic)
- Shared/core behavior (health/recommendations/orchestration policy) must not branch on provider-native type labels directly when canonical semantics are available.
- Cutover policy is live migration with background job + progress endpoint, no global write freeze, and no long-lived dual-read compatibility path.
- Phase-4 runtime activation for canonical type-dependent behavior requires backfill coverage gate (`work_item_type` >= 99.9%).
- Project metadata storage boundary:
  - `projects` collection remains control-plane focused.
  - heavy project-specific work-item metadata/config is stored in project-scoped Brain docs.
- Neo4j migration is in-scope for this cutover track.
- Embedded cadence session task snapshots are immutable historical records and are not migration targets.
## Not Fully Verified Yet (Explicitly Flagged)

- Global guarantee that every write endpoint in the backend is persist-first/respond-second (not fully audited across all modules).
- Full provider-agnostic enforcement in all Cadence mutation paths (several codepaths still include Jira-specific defaults and adapters).
- End-to-end guarantee that every LLM-facing response path is fully grounded against committed tool outputs (partially enforced in specific paths, but not fully audited globally).


