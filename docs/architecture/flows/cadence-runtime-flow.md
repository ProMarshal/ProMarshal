# Cadence Runtime Flow (Code-Verified)

Last verified against code: 2026-03-22.

## Scope

Cadence active-session DM routing, runtime execution, and scheduler-triggered reminders.

## Entry Points

- Slack ingress route: `api/app/integrations/router.py`
- Cadence orchestrator: `api/app/cadence/orchestrator.py`
- Cadence session store: `api/app/cadence/session_store.py`
- Scheduler cadence executor: `api/app/scheduler/executors.py` (`execute_cadence_reminder`)

## Flow A: Active DM Session Handling

```text
Slack DM event
   |
   +--> integrations/router.py:
   |      find_active_sessions_by_identity(provider=slack,...)
   |      if exactly one active cadence session -> handle_cadence_dm(...)
   |      return terminal (no Cortex fallthrough)
   |
   v
cadence/orchestrator.py handle_cadence_dm(...)
   |
   +--> State routes first:
   |      awaiting_overall_input / backlog decision / backlog pick
   |
   +--> Otherwise:
          send ACK async
          build AgentContext + cadence system prompt
          cadence_agent_runtime.run(...)
          inspect updated session state
          send next-task card or completion path prompt
          on summary-send completion path:
             best-effort post-cadence Team Poll reminder hook
             (interactive responded sessions only)
```

## Flow B: Reminder Scheduling

```text
/api/cron/tick
   |
   +--> daily lane includes cadence_reminder
   +--> ensure_cadence_schedules_for_projects() (seed path)
   +--> SchedulerEngine.tick(job_type="cadence_reminder")
           |
           +--> execute_cadence_reminder(...)
                   |
                   +--> cadence lock acquire
                   +--> expire stale sessions
                   +--> route_project_by_tier(...)
                   +--> release lock
```

Legacy compatibility:
- `/api/cron/cadence-reminders` remains available for backward compatibility and routes to the same `cadence_reminder` job type.
- When dispatcher mode is enabled, legacy endpoint calls are skipped with `reason="dispatcher_mode"`.

## Flow C: Expiry Scheduling (Independent)

```text
/api/cron/cadence-expiry
   |
   +--> ensure_global_schedules()
   +--> SchedulerEngine.tick(job_type="cadence_expiry")
           |
           +--> execute_cadence_expiry(...)
                   |
                   +--> expire_stale_sessions(...)
                   +--> trigger summary check for finalized correlation groups
```

## Key Mechanics Verified

- DM cadence route is terminal when active session exists and route executes.
- Cadence currently uses shared runtime path for awaiting-status progression.
- Cadence schedule rows are seeded/managed through scheduler service and engine, not feature-local cron loops.
- Cadence timeout finalization can run independently of daily reminder dispatch cadence.
