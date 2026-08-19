# Jira Webhook + Brain Sync Flow (Code-Verified)

Last verified against code: 2026-03-07.

## Scope

Inbound Jira issue webhooks, Brain upsert/delete behavior, and PM summary cache invalidation.

## Entry Points

- Webhook endpoint: `api/app/routes/jira_webhooks.py` (`POST /api/jira/webhooks/{project_id}/task-updated?token=...`)
- Queue access: `api/app/core/redis_queue.py` (`workitem_events`)
- Processing function: `process_jira_webhook(...)` in same router module.

## Flow (ASCII)

```text
Jira webhook -> /api/jira/webhooks/{project_id}/task-updated?token=...
   |
   +--> enqueue to `workitem_events` queue when available
   |      (fallback inline async processing if queue unavailable)
   |
   v
process_jira_webhook(payload, project_id)
   |
   +--> Resolve one ProMarshal project by `project_id` from webhook path
   |
   +--> Update the resolved project only:
          - issue_deleted -> delete Brain record
          - else -> normalize issue and upsert Brain record
          - invalidate PM summary Redis cache
```

## Key Mechanics Verified

- Webhook auth is enforced by `require_webhook_auth("jira")` using per-project URL token verification (`project_id` path + `token` query).
- Webhook routing is direct by `project_id` (single-project update, no site/project-key fan-out).
- PM summary Redis cache invalidation is called per impacted project after write/delete.
