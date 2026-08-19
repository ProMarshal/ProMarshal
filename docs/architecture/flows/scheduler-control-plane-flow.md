# Scheduler Control Plane Flow (Code-Verified)

Last verified against code: 2026-03-07.

## Scope

Centralized scheduler tick path, lease/idempotency protections, and executor dispatch.

## Entry Points

- Cron routes: `api/app/routes/cron.py`
- Scheduler engine: `api/app/scheduler/engine.py`
- Scheduler repository: `api/app/scheduler/repository.py`
- Schedule provisioning helpers: `api/app/scheduler/service.py`
- Executor registry: `api/app/scheduler/executors.py`

## Flow (ASCII)

```text
Cron endpoint call (/api/cron/*)
   |
   +--> validate bearer cron secret
   +--> optional schedule seeding (global/project-scoped)
   +--> SchedulerEngine.tick(job_type=...)
           |
           +--> list_due_schedules(job_type, now, limit)
           +--> for each due schedule:
                acquire_lease(schedule_id, owner, lease_until)
                create_run(idempotency_key)
                execute job_type executor
                complete_run(success/failed + metrics)
                compute_next_run_at(...)
                update_schedule_after_run(...)
                release_lease(...)
```

## Job Types Registered

- `cadence_reminder`
- `cadence_expiry`
- `sessions_cleanup`
- `slack_hourly_reader`
- `team_poll_cycle`

(from `EXECUTOR_REGISTRY` in `api/app/scheduler/executors.py`)

## Key Mechanics Verified

- Scheduler uses centralized `project_schedules` + `schedule_runs` model via repository.
- Lease ownership and idempotent run insert are both used during tick processing.
- Legacy health-calc cron endpoint is retained as compatibility `noop` because PM health is live-computed.
