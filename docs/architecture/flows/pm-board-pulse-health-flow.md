# PM Board + Pulse Health Flow (Code-Verified)

Last verified against code: 2026-03-07.

## Scope

This flow covers:
- PM Board health cards and supporting widgets
- Pulse Project Health page hydration and filtering
- PM Board -> Pulse health deep-link behavior.

## Entry Points

- Frontend route: `web/app/(dashboard)/projects/page.tsx`
- Main component: `web/components/projects/projects-page.tsx`
- Pulse health component: `web/components/projects/project-health-hierarchy.tsx`
- Backend summary endpoint: `api/app/projects/router.py` (`GET /api/projects/{id}/pm-board-summary`)
- Backend health endpoint: `api/app/projects/router.py` (`GET /api/projects/{custom_project_id}/project-health`)

## Flow (ASCII)

```text
ProjectsPage mounts / nav changes
   |
   +--> Trigger PM summary fetch (initial/project_switch/manual/poll/retry)
            |
            +--> Client in-memory cache hit? yes -> applyPmBoardSummaryData()
            |                               no
            v
      GET /api/projects/{mongo_id}/pm-board-summary?trigger=...
            |
            +--> Redis summary cache hit? yes -> return cached payload
            |                          no
            v
      Compute live health from Brain tasks + gather task/review/forecast sections
            |
            +--> set Redis cache
            v
      return composed summary payload
            |
            v
      applyPmBoardSummaryData() in ProjectsPage
            |
            +--> Project Health cards updated
            +--> Today's Focus/alerts/action/forecast data updated
            +--> Pulse initial health seed (`healthHierarchy`) updated


User clicks PM card (On Track / At Risk / Critical)
   |
   +--> openPulseProjectHealthByStatus()
            |
            +--> URL set to nav=Pulse&tab=Project Health&healthStatus=<status>
            v
      Pulse Project Health page loads with health filter
```

## Key Mechanics Verified

- URL hygiene removes `healthStatus` outside `Pulse > Project Health`:
  - `projects-page.tsx` URL hygiene effect.
- PM summary fetch dedupe/abort:
  - `pmBoardSummaryInFlightRef`, `pmBoardSummaryAbortRef` in `projects-page.tsx`.
- PM Board polling behavior:
  - 5-minute interval + visibility event refresh while PM Board is visible.
- Pulse fallback:
  - If no usable `initialHealthData`, `project-health-hierarchy.tsx` fetches `/project-health`.

