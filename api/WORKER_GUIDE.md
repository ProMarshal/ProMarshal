# Worker Runtime Guide

This guide covers the current Dramatiq-only worker runtime.

## Supported Queue Names

- `chat_interactions`
- `workitem_events`
- `chat_commands`
- `cortex_runs`
- `action_item_extraction`

## Local Startup

### Start API + worker via unified entrypoint

```bash
cd api
./start.sh
```

### Start Dramatiq worker directly

```bash
cd api
DRAMATIQ_WORKER_PROCESSES=1 DRAMATIQ_WORKER_THREADS=8 \
python start_dramatiq_worker.py cortex_runs chat_interactions workitem_events chat_commands action_item_extraction
```

## Concurrency Notes

- Dramatiq uses process + thread concurrency:
  - processes: `DRAMATIQ_WORKER_PROCESSES`
  - threads per process: `DRAMATIQ_WORKER_THREADS`
- Effective max parallel message handling is approximately:
  - `processes * threads`
  - subject to I/O blocking and external dependency latency.

## Operational Checks

- Queue depth before/after deployments.
- Enqueue accepted logs by queue name.
- Worker error logs and retry behavior.
- PM summary `202 -> ready` flow.
- Cortex run lock/dispatch continuation behavior.

## Troubleshooting

### Queue unavailable fallback triggered

- Confirm Redis connectivity (`REDIS_URL`).
- Confirm Dramatiq worker process is running.

### Dramatiq worker starts but no jobs execute

- Ensure actor module is loaded via `app.workers.dramatiq_app`.
- Validate queue names passed to `start_dramatiq_worker.py`.
