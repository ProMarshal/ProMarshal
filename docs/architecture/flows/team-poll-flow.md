# Team Poll Flow (Code-Verified)

Last verified against code: 2026-03-22.

## Scope

Owner/member DM handling for Team Poll plus scheduler lifecycle cycle.

## Entry Points

- Slack ingress route: `api/app/integrations/router.py`
- Team Poll DM handler: `api/app/team_poll/interaction_handler.py`
- Team Poll session store: `api/app/team_poll/session_store.py`
- Team Poll scheduler executor: `api/app/scheduler/executors.py` (`execute_team_poll_cycle`)

## Flow A: DM Reply Handling

```text
Slack DM event
   |
   +--> integrations/router.py pending pre-resolution
   |      route_pending_dm_interactions(...)
   |        team_poll handler first (priority)
   |        if active pending team poll -> handle_team_poll_dm(...)
   |        if handled -> terminal return
   |        if not handled -> may pass to later routing/Cortex
   |
   v
team_poll/interaction_handler.py handle_team_poll_dm(...)
   |
   +--> owner path?
   |      - owner status/close intent handling
   |      - owner skip text -> mark_owner_skipped()
   |
   +--> member path:
          normalize_response_for_mode(...)
          if free_text:
             evaluate_pending_poll_relevance(...)
             -> accept_poll_response: mark_session_responded(...)
             -> remind_pending_poll: reminder + re-prompt
             -> pass_to_cortex: return unhandled
          else:
             if valid -> mark_session_responded(...)
             else -> validation error response
   |
   +--> finalize_poll_if_ready(...)
```

## Flow B: Owner Poll Initiation Route

```text
Slack DM owner text
   |
   +--> default path (flag enabled):
   |      owner free-text reaches Cortex tool arbitration
   |      team_poll_create/team_poll_status/team_poll_close tools
   |
   +--> fallback path (flag disabled):
          integrations/router.py handle_owner_poll_ingress(...)
          if handled -> compose owner response + terminal return
```

## Flow C: Scheduler Lifecycle

```text
/api/cron/team-poll-cycle
   |
   +--> ensure_team_poll_schedules_for_active_projects()
   +--> SchedulerEngine.tick(job_type="team_poll_cycle")
           |
           +--> execute_team_poll_cycle(...) -> team_poll.scheduler_hooks
```

## Key Mechanics Verified

- Team Poll active-session route is terminal before Cortex.
- Owner skip button and free-text skip both route through `mark_owner_skipped` and acknowledgement generation.
- Team Poll sessions are stored through SessionRepository-backed session/index structure.
