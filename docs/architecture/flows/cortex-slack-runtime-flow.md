# Cortex Slack Runtime Flow (Code-Verified)

Last verified against code: 2026-03-22.

## Scope

This flow documents Slack ingress through Cortex handoff, queue execution, and runtime tool path.

## Entry Points

- Slack events API: `api/app/integrations/router.py` (`POST /api/integrations/slack/events`)
- Handoff classifier + queue bridge imports in integration router
- Worker: `api/app/cortex/worker.py`
- Orchestrator: `api/app/cortex/orchestrator.py`
- Shared runtime: `api/app/agent_runtime/runtime.py`

## Flow (ASCII)

```text
Slack event -> /api/integrations/slack/events
   |
   +--> Event dedupe (event_id + semantic key + response gate)
   |
   +--> If DM message:
   |      1) Pending interactions pre-resolution route
   |           - Team Poll pending handler first (terminal when handled)
   |           - Action Item pending handler second
   |           - Team Poll may return pass_to_cortex for non-poll mutation intents
   |      2) Cadence active-session identity route (terminal if handled)
   |      3) Team Poll legacy owner ingress only when
   |           team_poll_owner_free_text_via_cortex=false
   |
   +--> Resolve project/member context (Slack resolver + channel_index/default project)
   |
   +--> Scope gate + clarification gate
   |
   +--> Optional stage3 ACK (dedupe-gated)
   |
   +--> handoff_slack_event_to_cortex(...)
          |
          v
       RQ queue: cortex_runs
          |
          v
       process_cortex_run() in worker
          |
          +--> Session-key isolation guard (ingress vs drain key)
          +--> Run lock claim + heartbeat + inflight recovery
          +--> CortexOrchestrator.handle_turn(...)
                  |
                  +--> shared_agent_runtime.run(...)
                  +--> tool calls / policy guards / execution
                  +--> output routing back to Slack channel/thread
```

## Key Mechanics Verified

- Pending interaction routing is centralized before Cortex handoff in integration router.
- Team Poll owner free-text defaults to Cortex tool arbitration; legacy ingress is flag-gated.
- Session isolation in worker:
  - Mismatched ingress session key is re-queued to correct session queue.
- Queue safety:
  - run lock, dispatch claim, inflight recovery, continuation jobs in `cortex/worker.py`.
- Scope handling:
  - project-scope gate check and clarification route in integration ingress before handoff.
