# Decision Index

Index of major decisions and where they apply.

## Active ADRs
- `ADR-0001`: Documentation and decision governance baseline (`docs/adr/ADR-0001-doc-governance-baseline.md`)
- `ADR-0002`: PM health live read model + Redis cache (`docs/adr/ADR-0002-pm-health-live-read-model-redis-cache.md`)
- `ADR-0003`: Category-based integration gating (`docs/adr/ADR-0003-category-based-integration-gating.md`)
- `ADR-0004`: Slack DM routing precedence and terminal hand-off (`docs/adr/ADR-0004-slack-dm-routing-precedence-terminal-handoff.md`)
- `ADR-0005`: Session storage boundary (`docs/adr/ADR-0005-session-storage-boundary.md`)
- `ADR-0006`: PM recommendation engine composition and runtime state (`docs/adr/ADR-0006-pm-recommendation-engine-composition-and-runtime-state.md`)
- `ADR-0007`: Cortex follow-up write target grounding (`docs/adr/ADR-0007-cortex-followup-write-target-grounding.md`)
- `ADR-0008`: Cortex canonical due-date filter contract (`docs/adr/ADR-0008-cortex-due-date-filter-contract.md`)
- `ADR-0009`: Cadence expiry and summary source-of-truth hardening (`docs/adr/ADR-0009-cadence-expiry-summary-source-of-truth-hardening.md`)
- `ADR-0010`: Action Items domain and commitment guard (`docs/adr/ADR-0010-action-items-domain-and-commitment-guard.md`)
- `ADR-0011`: Shared HTTP actor resolution and identity binding (`docs/adr/ADR-0011-shared-http-actor-resolution-and-identity-binding.md`)
- `ADR-0012`: Dedicated scheduler process topology (future track) (`docs/adr/ADR-0012-dedicated-scheduler-process-topology-future-track.md`)
- `ADR-0013`: Queue runtime migration from RQ to Dramatiq (`docs/adr/ADR-0013-queue-runtime-migration-rq-to-dramatiq.md`)
- `ADR-0014`: Team Poll Cortex initiation, pending relevance gating, and post-Cadence reminder (`docs/adr/ADR-0014-team-poll-cortex-initiation-relevance-and-post-cadence-reminder.md`)
- `ADR-0015`: Scheduler lane retention cleanup policy (`docs/adr/ADR-0015-scheduler-lane-retention-cleanup-policy.md`)
- `ADR-0016`: API auth and webhook trust model (`docs/adr/ADR-0016-api-auth-and-webhook-trust-model.md`)
- `ADR-0017`: Project deletion owner controls and cleanup boundary (`docs/adr/ADR-0017-project-deletion-owner-controls-and-cleanup-boundary.md`)
- `ADR-0018`: Cadence/digest weekend and owner follow-up policies (`docs/adr/ADR-0018-cadence-digest-weekend-and-owner-followup-policies.md`)
- `ADR-0019`: Slack identity healing and disconnect reset (`docs/adr/ADR-0019-slack-identity-healing-and-disconnect-reset.md`)
- `ADR-0020`: Canonical work-item domain hard cutover and live migration (`docs/adr/ADR-0020-canonical-work-item-domain-hard-cutover-live-migration.md`)
- `ADR-0021`: Charter read path Redis cache (`docs/adr/ADR-0021-charter-read-path-redis-cache.md`)
- `ADR-0022`: Cadence DM queue isolation and session lease (`docs/adr/ADR-0022-cadence-dm-queue-and-session-lease.md`)

## Domain Mapping
- PM Board / Pulse:
  - Invariants: `docs/architecture/invariants.md`
  - Flow docs: `docs/architecture/flows/pm-board-pulse-health-flow.md`
  - Contract: `docs/contracts/pm-board-pulse-health-contract.md`
  - ADRs: `ADR-0002`, `ADR-0003`, `ADR-0006`
  - Plans: `docs/plans/pm/`
- Cortex runtime and prompt/response policy:
  - Flow docs: `docs/architecture/flows/cortex-slack-runtime-flow.md`
  - Contract: `docs/contracts/cortex-contract.md`
  - ADRs: `ADR-0004`, `ADR-0005`, `ADR-0007`, `ADR-0008`
  - Plans: `docs/plans/cortex/`
- Cadence:
  - Flow docs: `docs/architecture/flows/cadence-runtime-flow.md`
  - Contract: `docs/contracts/cadence-contract.md`
  - ADRs: `ADR-0004`, `ADR-0005`, `ADR-0009`, `ADR-0014`, `ADR-0018`, `ADR-0022`
  - Plans: `docs/plans/platform/`, `docs/plans/team-poll/` (cross-flow touchpoints)
- Team Poll:
  - Flow docs: `docs/architecture/flows/team-poll-flow.md`
  - Contract: `docs/contracts/team-poll-contract.md`
  - ADRs: `ADR-0004`, `ADR-0005`, `ADR-0014`
  - Plans: `docs/plans/team-poll/`
- Integrations and routing:
  - Flow docs: `docs/architecture/flows/jira-webhook-and-brain-sync-flow.md`
  - ADRs: `ADR-0003`, `ADR-0004`
  - Plans: `docs/plans/integrations/`
- Platform scheduling/runtime:
  - Invariants: `docs/architecture/invariants.md`
  - Flow docs: `docs/architecture/flows/scheduler-control-plane-flow.md`
  - ADRs: `ADR-0002`, `ADR-0005`, `ADR-0012`, `ADR-0013`, `ADR-0015`
  - Plans: `docs/plans/platform/`
- Planner / Charter:
  - Invariants: `docs/architecture/invariants.md`
  - Contract: `docs/contracts/planner-charter-contract.md`
  - ADRs: `ADR-0021`
  - RFCs: `docs/rfcs/RFC-charter-read-path-redis-cache-2026-04-03.md`
  - Plans: `docs/plans/platform/charter-redis-cache-plan-2026-04-03.md`
- Project lifecycle/deletion controls:
  - Contract: `docs/contracts/project-lifecycle-contract.md`
  - ADRs: `ADR-0017`
  - Plans: `docs/plans/platform/project-deletion-owner-settings-plan-2026-03-27.md`
- Platform security/auth hardening:
  - ADRs: `ADR-0011`, `ADR-0016`
  - Plans: `docs/execution/action-items-intelligence-v1-implementation-checklist-2026-03-13.md` (deferred platform track section)
- Action Items:
  - Contract: `docs/contracts/action-items-contract.md`
  - ADRs: `ADR-0010` (domain), `ADR-0011` (decoupled platform actor-binding hardening track), `ADR-0018` (schedule/follow-up policy)
  - Plans: `docs/execution/action-items-intelligence-v1-implementation-checklist-2026-03-13.md`

## How To Extend
1. Create a new ADR from `docs/adr/ADR-TEMPLATE.md`.
2. Add it under Active ADRs.
3. Link impacted contracts and plans.
4. If behavior changed, update `docs/architecture/invariants.md` and affected contract(s).
## Decision History
- Baseline rollup: `docs/execution/decision-history-and-plan-baseline-2026-03-07.md`
- Deferred cross-feature backlog register: `docs/execution/deferred-cross-feature-backlog-register-2026-03-14.md`
- Scheduler dispatcher consolidation plan: `docs/execution/scheduler-dispatcher-consolidation-plan-2026-03-16.md`
## Decision Register

| Decision | Status | Primary Source | Supporting Source |
|---|---|---|---|
| PM Board + Pulse share one health source via composed summary path | Implemented | `docs/adr/ADR-0002-pm-health-live-read-model-redis-cache.md` | `docs/contracts/pm-board-pulse-health-contract.md`, `docs/architecture/invariants.md` |
| PM summary cache-miss path is async-first (`202 processing` + status poll) with worker recompute and sync fallback | Implemented | `docs/adr/ADR-0002-pm-health-live-read-model-redis-cache.md` | `docs/contracts/pm-board-pulse-health-contract.md`, `docs/architecture/invariants.md` |
| Health URL filter (`healthStatus`) is scoped only to `Pulse > Project Health` | Implemented | `docs/contracts/pm-board-pulse-health-contract.md` | `docs/architecture/invariants.md`, `docs/testing/regression-matrix.md` |
| Integration-dependent UI gating must be category-based (not provider hardcoded) | Implemented | `docs/adr/ADR-0003-category-based-integration-gating.md` | `docs/architecture/invariants.md` |
| Slack DM routing precedence: Cadence/Team Poll terminal handling before Cortex | Implemented | `docs/adr/ADR-0004-slack-dm-routing-precedence-terminal-handoff.md` | `docs/contracts/team-poll-contract.md`, `docs/contracts/cortex-contract.md` |
| Session boundary: Redis runtime session state with project+actor isolation | Implemented | `docs/adr/ADR-0005-session-storage-boundary.md` | `docs/architecture/invariants.md`, `docs/execution/session-isolation-execution-board.md` |
| Cortex self-scope guardrail for task-read (`my tasks`) with strict identity resolution | Implemented | `docs/contracts/cortex-contract.md` | `docs/architecture/invariants.md`, `docs/testing/regression-matrix.md` |
| Strict assignee matching precedence (ID-first; email/name fallback only if ID absent) | Implemented | `docs/contracts/cortex-contract.md` | `docs/architecture/invariants.md`, `docs/testing/regression-matrix.md` |
| Team Poll question rewrite uses LLM primary with retries, deterministic fallback after exhaustion | Implemented | `docs/contracts/team-poll-contract.md` | `docs/architecture/invariants.md` |
| Team Poll scope-drift lexical gate is disabled to avoid false rejects | Implemented | `docs/architecture/invariants.md` | `docs/execution/decision-history-and-plan-baseline-2026-03-07.md` |
| PM recommendation engine execution board (P0+) | In Progress | `docs/plans/pm/pm-recommendation-execution-board.md` | `docs/execution/execution-status-checkpoint-2026-03-09.md` |
| PM recommendations compose into `pm-board-summary`; `/recommendations` is supporting/internal only | Accepted (Pre-Implementation) | `docs/adr/ADR-0006-pm-recommendation-engine-composition-and-runtime-state.md` | `docs/contracts/pm-board-pulse-health-contract.md`, `docs/plans/pm/pm-recommendation-execution-board.md` |
| Scheduler control plane is centralized (project_schedules + schedule_runs, lease + idempotency, cron routes tick scheduler engine) | Implemented | docs/plans/platform/scheduler-rearch-plan.md | docs/architecture/invariants.md, docs/contracts/cadence-contract.md, docs/adr/ADR-0002-pm-health-live-read-model-redis-cache.md |
| Cadence timeout and daily-summary correctness is source-session safe with dedicated expiry scheduling; `session_index` is not a single point of correctness failure | Implemented | `docs/adr/ADR-0009-cadence-expiry-summary-source-of-truth-hardening.md` | `docs/contracts/cadence-contract.md`, `docs/architecture/flows/cadence-runtime-flow.md`, `docs/testing/regression-matrix.md` |
| Shared Agent Runtime is the primary execution path for Cortex and Cadence | Implemented | `docs/architecture/invariants.md` | `docs/plans/platform/shared-agent-runtime-plan.md`, `docs/contracts/cortex-contract.md` |
| Capability Bundle Registry is the extension/control plane for provider/tool onboarding | Implemented | `docs/architecture/invariants.md` | `docs/plans/cortex/p11-deterministic-baseline-orchestrator-enrichment-plan-v2.md`, `docs/guides/p11-admin-guide-extending-tasks-and-tools.md` |
| Task-read provider dispatch follows repository abstraction (provider-agnostic read plane) | Implemented | `docs/architecture/invariants.md` | `docs/contracts/cortex-contract.md`, `docs/execution/p11-v2-implementation-slices-checklist.md` |
| `channel_index` is authoritative for DM active-project context routing | Implemented | `docs/architecture/invariants.md` | `docs/plans/integrations/channel-index-multi-provider-cadence-routing-implementation-plan.md`, `docs/adr/ADR-0004-slack-dm-routing-precedence-terminal-handoff.md` |
| Cadence active-session scope is currently global at runtime (`cadence_single_active_scope=global`) | Implemented (Current Runtime) | `docs/architecture/invariants.md` | `docs/contracts/cadence-contract.md`, `docs/execution/session-isolation-execution-board.md` |
| Cortex follow-up non-idempotent writes require deterministic target grounding (`current_message` / `session_snapshot` / `fresh_read`) | Implemented | `docs/adr/ADR-0007-cortex-followup-write-target-grounding.md` | `docs/contracts/cortex-contract.md`, `docs/testing/regression-matrix.md` |
| Cortex task-read due-date intent uses canonical `due_date_mode` filter (`any|missing|present`) across search/analyze provider paths | Implemented | `docs/adr/ADR-0008-cortex-due-date-filter-contract.md` | `docs/contracts/cortex-contract.md`, `docs/testing/regression-matrix.md` |
| Action Items is a separate domain with queue-backed auto-detection and centralized commitment guard policy | Implemented | `docs/adr/ADR-0010-action-items-domain-and-commitment-guard.md` | `docs/contracts/action-items-contract.md`, `docs/architecture/invariants.md`, `docs/testing/regression-matrix.md` |
| Shared HTTP actor binding is a separate platform hardening track decoupled from Action Items v1 critical path | Accepted | `docs/adr/ADR-0011-shared-http-actor-resolution-and-identity-binding.md` | `docs/execution/action-items-intelligence-v1-implementation-checklist-2026-03-13.md`, `docs/contracts/action-items-contract.md` |
| Unified API trust model (JWT for user-facing routes, internal secret/timestamp for server-to-server, standardized webhook verifier dependency) | Accepted (Rollout Complete in Code; Production Cutover Pending) | `docs/adr/ADR-0016-api-auth-and-webhook-trust-model.md` | `docs/rfcs/RFC-api-auth-security-hardening-2026-03-25.md`, `docs/plans/platform/api-auth-security-plan-2026-03-23.md` |
| Project deletion is owner-only with staged required/best-effort cleanup boundary and idempotent lock-based orchestration | Implemented | `docs/adr/ADR-0017-project-deletion-owner-controls-and-cleanup-boundary.md` | `docs/plans/platform/project-deletion-owner-settings-plan-2026-03-27.md`, `docs/testing/regression-matrix.md` |
| Cadence and Action Item Digest support per-project weekend skip and owner follow-up suppression policies | Implemented | `docs/adr/ADR-0018-cadence-digest-weekend-and-owner-followup-policies.md` | `docs/contracts/cadence-contract.md`, `docs/contracts/action-items-contract.md`, `docs/testing/regression-matrix.md` |
| Dedicated scheduler process topology is a future platform track; API dispatcher remains current trigger path during transition | Proposed (Future) | `docs/adr/ADR-0012-dedicated-scheduler-process-topology-future-track.md` | `docs/execution/scheduler-dispatcher-consolidation-plan-2026-03-16.md`, `docs/deployment-architecture-decisions.md` |
| Queue runtime migrates from RQ to Dramatiq with phased per-queue cutover and final RQ compatibility cleanup | Implemented | `docs/adr/ADR-0013-queue-runtime-migration-rq-to-dramatiq.md` | `docs/rfcs/RFC-rq-to-dramatiq-migration-2026-03-20.md`, `docs/execution/rq-to-dramatiq-migration-plan-2026-03-20.md` |
| Team Poll owner initiation routes via Cortex by default; pending poll relevance uses deterministic + embedding + LLM fallback; post-Cadence reminder is best-effort and non-blocking | Implemented | `docs/adr/ADR-0014-team-poll-cortex-initiation-relevance-and-post-cadence-reminder.md` | `docs/contracts/team-poll-contract.md`, `docs/contracts/cadence-contract.md`, `docs/architecture/invariants.md` |
| Scheduler cleanup/retention runs under centralized tick with explicit product/maintenance lane orchestration and bounded retention policies | Implemented | `docs/adr/ADR-0015-scheduler-lane-retention-cleanup-policy.md` | `docs/plans/platform/scheduler-lane-retention-cleanup-plan-2026-03-22.md`, `docs/rfcs/RFC-scheduler-lane-retention-cleanup-2026-03-22.md`, `docs/architecture/invariants.md` |
| Single-source scope registry with Stage-2 precedence protection | Implemented | `docs/architecture/invariants.md` | `docs/contracts/cortex-contract.md`, `docs/testing/regression-matrix.md` |
| Slack resolver identity healing preserves strict ID-first routing and self-heals stale member mappings using controlled email fallback | Implemented | `docs/adr/ADR-0019-slack-identity-healing-and-disconnect-reset.md` | `docs/testing/regression-matrix.md`, `api/tests/test_slack_identity_healing.py` |
| Jira parent-link mutation flows through shared update contract with rule-validated hierarchy checks | Implemented | `docs/adr/ADR-0020-canonical-work-item-domain-hard-cutover-live-migration.md` | `docs/contracts/cortex-contract.md`, `docs/architecture/invariants.md`, `docs/testing/regression-matrix.md` |
| Charter planner read path uses Redis-derived read-through cache for status/entities/current-stage with Mongo canonical source and deterministic invalidation | Implemented | `docs/adr/ADR-0021-charter-read-path-redis-cache.md` | `docs/contracts/planner-charter-contract.md`, `docs/rfcs/RFC-charter-read-path-redis-cache-2026-04-03.md`, `docs/plans/platform/charter-redis-cache-plan-2026-04-03.md` |
| Cadence DM turns execute via dedicated Dramatiq queue with per-session Redis lease fencing to prevent concurrent session mutation races | Implemented | `docs/adr/ADR-0022-cadence-dm-queue-and-session-lease.md` | `docs/contracts/cadence-contract.md`, `docs/architecture/invariants.md`, `docs/plans/platform/cadence-concurrency-scaling-plan-2026-04-14.md` |


