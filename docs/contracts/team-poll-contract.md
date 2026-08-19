# Team Poll Contract

## Inputs
- Owner-triggered poll intent:
  - DM slash/command route (existing command handlers)
  - Owner free-text route through Cortex Team Poll tools when
    `team_poll_owner_free_text_via_cortex=true` (default)
  - Legacy owner free-text ingress path remains flag-gated fallback:
    `api/app/team_poll/ingress.py`
- Member/owner DM responses for active poll sessions:
  - `handle_team_poll_dm(...)` in `api/app/team_poll/interaction_handler.py`
  - shared pending interaction router:
    `api/app/integrations/pending_interactions_router.py`
- Owner skip actions from:
  - Free text (`is_owner_skip_text(...)`)
  - Slack button payload (`handle_owner_skip_button_action(...)`)

## Outputs
- Poll lifecycle updates (create/status/close/respond/skip/finalize) persisted through Team Poll session store/orchestrator paths.
- User-facing acknowledgements via `compose_team_poll_response(...)`.

## Guarantees
- Terminal poll routing in integrations layer:
  - Active poll DM route returns early when handled.
  - Owner legacy ingress route returns early only when enabled and handled.
- Pending poll relevance gate for free-text response mode:
  - deterministic safety checks first (owner controls, high-confidence workitem mutation bypass)
  - embedding high-relevant accept path
  - non-high-relevant path uses LLM relevance fallback classifier (possible answer vs non-answer)
  - non-answer path returns pending-poll reminder (no auto-consume)
- Owner skip is equivalent for button and free-text:
  - Both call `mark_owner_skipped(...)`
  - Both use action `owner_skip_success`
  - Both return acknowledgement text "Your response was skipped for this poll."
- Invalid member response format is rejected with explicit validation message (no silent acceptance):
  - `normalize_response_for_mode(...)` + `member_validation_error` path.
- Post-Cadence reminder sidecar:
  - After interactive Cadence completion (`outcome.response_status=responded`), unresolved Team Poll member sessions may receive one reminder (best-effort), gated by cooldown and remaining poll TTL.
  - Expired/no-response Cadence terminal paths are excluded from immediate post-cadence reminder.

## Allowed Fallbacks
- Question rewrite:
  - LLM first with retries/backoff
  - deterministic fallback when retries exhausted (`team_poll_question_rewriter_fallback`)
- Response composition:
  - If `team_poll_llm_response_enabled` is off, fallback text is returned.
  - `owner_skip_success` is deterministic and bypasses composer.
- Relevance classification:
  - Embedding generation failure does not break route; LLM fallback remains authoritative for non-high-relevant cases when enabled.
  - If LLM fallback is unavailable or low-confidence, reminder path is used (fail-safe non-consumptive behavior).
- Post-cadence reminder:
  - Redis cooldown gate failure is fail-open (Cadence terminal flow is never failed by reminder sidecar).

## Not Fully Verified Yet
- End-to-end semantic quality of rewritten owner question text (style/clarity) is not guaranteed by this contract; only routing/validity/fallback behavior is guaranteed.

## Runtime Hardening Boundary (Phase 4)
- Team Poll keeps current routing and response semantics while shared runtime hardening matures in Cortex.
- Any shared runtime adoption for Team Poll must preserve:
  - terminal active-poll DM routing
  - owner skip parity (button vs free-text)
  - explicit invalid member response handling
- Broader convergence requires parity pack evidence and contract-first updates before implementation change.
- See execution boundary note:
  - `docs/execution/phase4-cadence-team-poll-rollout-boundary-2026-03-12.md`
