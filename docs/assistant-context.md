# Assistant Context Pack

Use this before proposing or implementing changes.

## Product Scope
- ProMarshal is a project-operations platform with PM Board, Pulse, Cortex, Cadence, and Team Poll.
- Integrations are tool-agnostic by category (for example: `workitem`, `communication`), not provider hardcoding.

## Current Architecture Principles
- Single source of truth per domain; derived views are computed from canonical data.
- PM Board and Pulse health logic must use the same computation path.
- Redis is used for fast live state and caching; avoid duplicate conflicting sources.
- Deterministic behavior first; LLM augmentation only where explicitly designed.

## Non-Negotiables
- Do not reintroduce deprecated fallback paths once removed.
- Do not add provider-specific hardcoding when category-based logic exists.
- UI claims must reflect persisted or validated state.
- Persist-first, respond-second for state-changing actions.

## Before Changing Anything
1. Read `docs/architecture/invariants.md`.
2. Check `docs/decision-index.md` for related ADRs.
3. If design-impacting, create/update RFC in `docs/rfcs/`.
4. If bug fix, add a regression entry in `docs/testing/regression-matrix.md`.

