# Cortex Contract

## Inputs
- Ingress payload (`ingress`, `raw_event`) processed by worker:
  - `api/app/cortex/worker.py` -> `orchestrator.handle_turn(...)`
- Session context keyed by canonical `session_key`:
  - Built from project/user/source anchor logic in `api/app/cortex/sessions.py`
- Execution context includes role/actor/project metadata consumed by runtime executor and tools.
- Tool surface is capability-bundle driven; Team Poll owner operations are exposed as Cortex tools:
  - `team_poll_create` (requires confirmation)
  - `team_poll_status`
  - `team_poll_close`
- Jira work-item mutation tools include explicit parent-link mutation capability:
  - `jira_update_parent_work_item` (non-idempotent, confirmation-required)

## Outputs
- Orchestrator result object (`ok`, response payload, error when failed):
  - Worker raises `cortex_orchestrator_failed:{error}` on non-OK turn.
  - Exception: delivered terminal policy blocks (for example `out_of_scope` with `metadata.delivered=true`) are treated as handled outcomes and do not raise job failure.
- Tool execution returns normalized success/error payloads through runtime executor.

## Side Effects
- Active Cortex session state persisted in Redis store (`cortex:session:v1:*`):
  - `api/app/cortex/session_store_redis.py`
  - `CortexSessionManager` in `api/app/cortex/sessions.py`
- Mutation tool calls use non-idempotent guard/replay cache:
  - `operation_id` auto-derived server-side
  - replay conflict checks (`operation_id_conflict`)
  - `api/app/agent_runtime/executor.py`
- Queue/dispatch locks and per-session queue behavior in Redis:
  - `api/app/cortex/queue_runtime.py`, `api/app/cortex/worker.py`
- Pending-interaction pre-routing can terminally handle DM messages before Cortex turn execution:
  - `api/app/integrations/pending_interactions_router.py`
  - Team Poll pending route may also return `pass_to_cortex` for high-confidence non-poll mutation intents.

## Guarantees
- Tool call execution is blocked for unknown/unregistered tools:
  - `tool_not_registered` branch in `execute_tool_call(...)`.
- Permission gate can block actions requiring approval:
  - `approval_required` branch in `execute_tool_call(...)`.
- Non-idempotent writes are guarded by replay cache and Redis availability policy:
  - `redis_guard_unavailable` when degraded mode is not allowed.
- Session isolation is enforced in worker: mismatched ingress session is rerouted to correct session queue, not processed in wrong session.
- Task-read self-scope guardrail:
  - For self-scope queries (for example "my tasks"), Jira read tools force self scope and resolve actor identity before query execution.
  - `jira_search_work_items` and `jira_analyze_work_items` convert self-scope to `assignee_mode=specific` once identity is resolved.
  - If identity cannot be resolved, tool returns deterministic validation error (`actor_identity_not_resolved`) instead of broad project-scope results.
- Strict assignee identity matching policy in task read repository:
  - When assignee user/account ID is available, read filters match by ID fields only.
  - Email/name matching is used only when assignee ID is absent.
  - Source: `api/app/tasks/read_repository/providers/jira_repository.py`.
- Follow-up write target grounding:
  - Non-idempotent writes with identifier-like target arguments must be grounded to at least one source:
    - current user message
    - structured session snapshot (`target_refs`)
    - fresh read clarification flow
  - If target scope is unresolved, write execution is blocked with clarification-required error.
- Canonical due-date read filtering:
  - Task-read intents that reference due-date presence/absence map to canonical `due_date_mode` (`any|missing|present`).
  - Provider read paths must apply the same canonical due-date filter semantics for count/list/analyze responses.
- Canonical provider-type read filtering:
  - Task-read intents that reference explicit work-item types map to normalized provider-type filters (`provider_types`).
  - Jira search/list paths apply the same provider-type filter semantics across Brain and Jira-live read sources.
  - Generic read nouns (`tasks`, `items`, `work items`, `requests`, `tickets`, `issues`, `cards`) remain untyped scope and must not force a provider-type filter.
  - Generic list scope defaults to all assignees (including unassigned) unless user explicitly requests self/specific assignee scope.
- Write clarification UX normalization:
  - User-facing clarification responses must avoid raw internal field tokens (`tool.field`) when a friendly label is available.
  - Internal token forms remain available in telemetry/details for debugging.
- Bounded repair semantics:
  - Write arg repair is bounded per turn (`cortex_max_repair_attempts_per_turn`) across proposal-repair and quality-repair paths.
  - When repair budget is exhausted, execution fails closed to clarification rather than entering additional repair loops.
- Proposal field provenance hints:
  - Proposal tool-call metadata may include per-field provenance/confidence (`field_meta`) used as quality prevalidation hints.
  - Only trusted provenance classes (`explicit_user_text`, `deterministic_parse`, `followup_user`) above configured confidence threshold can bypass semantic reassessment.
- Jira add-comment deterministic body/confirmation alignment:
  - Jira comment mutation paths normalize command-style user phrasing (for example `ask requestor to ...`) into direct comment text before mutation.
  - Tool result payload exposes `posted_comment_text` representing the exact text submitted to Jira, so downstream confirmations can mirror committed write content.
- Jira parent-link mutation contract:
  - Parent link updates run through shared task mutation contract (`UpdateTaskDTO.parent_external_id`).
  - Invalid parent-child hierarchy links must fail closed with validation-class tool errors before write commit.
  - Successful parent-link updates expose committed action summary `Mapped <child> under <parent>`.
- Two-stage scope policy:
  - Deterministic scope gate runs first (`classify_project_scope_gate`) and consumes canonical registry vocabulary/reason-codes/signals (`api/app/domain/scope/registry.py`).
  - Optional LLM scope classifier runs second when enabled.
  - Stage-2 classifier cannot downgrade strong deterministic project-related matches.
  - Greeting classification is deterministic pass-through and is not downgraded by Stage-2.
  - Out-of-scope refusal is enforced for non-write intents; task-write intents are exempt from hard refusal at this stage.
- Layer B transport fidelity guarantees (flag-gated path):
  - Assistant message `tool_calls` history is preserved through Layer B request mapping and gateway provider payload serialization.
  - Gateway transport applies an outer hard timeout ceiling in addition to provider timeout parameter.
  - This transport fidelity update does not change Runner/tool-loop ownership, confirmation policy, idempotency, or quality-gate semantics.
- Shared LLM gateway capability includes embeddings (used by Team Poll pending relevance paths):
  - request/response contracts in `api/app/llm/gateway.py`
  - LiteLLM embedding transport implementation in `api/app/llm/litellm_gateway.py`.

## Current Layer B Scope Boundary (Documented)
- Layer B gateway transport is canonical for `get_response()` when enabled.
- `stream_response()` still delegates to underlying LitellmProvider stream path (streaming convergence is outside current cutover scope).
- `output_schema` is currently not mapped into `LLMGatewayRequest`; structured-output parity for this parameter is not yet guaranteed under Layer B.
- Shadow parity helper in executor is currently no-op; parity validation is log/manual until shadow compare implementation is reintroduced.

## Allowed Fallbacks
- If Redis guard is unavailable and `cortex_allow_redis_degraded` is enabled, write path may continue in degraded mode (explicitly logged).
- If queue/dispatch is already active for a session, requests are queued for continuation.

## Not Fully Verified Yet
- Full end-to-end "no fabricated mutation claims" output guarantee across all LLM wording paths was not fully audited in this step.
- Complete scope-policy coverage for every intent/tool combination was not exhaustively validated here.

## Approved Next Contract Changes (Not Yet Implemented)
- Canonical work-item cutover (ADR-0020):
  - capability semantics migrate from task-centric IDs to canonical `workitem_*`.
  - long-lived `task_*` runtime compatibility is not part of the target contract.
  - canonical work-item writes include date operations (start/due set/clear) alongside create/assign/status/comment/description.
  - planner and policy fallback identifiers must not reintroduce legacy `task_*` IDs after cutover.
  - runtime read/write query semantics align to `entity_type="work_item"` after migration gates complete.
