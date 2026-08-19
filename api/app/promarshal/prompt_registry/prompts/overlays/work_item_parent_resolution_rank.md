ProMarshal Work Item Parent Resolution Rank Overlay:
- Rank parent candidates for a new subtask by semantic relevance to `request_context.user_message` and `request_context.child_title` first.
- Never rank subtask-type candidates as parent options.
- Use recency only as a tie-breaker when relevance is close.
- Prefer specific parent candidates over broad containers when specificity is meaningfully better.
- Return strict JSON only with shape:
  `{"ranked":[{"external_id":"KEY-1","reason":"...","confidence":0.0}]}`
- Return up to 10 candidates ordered best-first.
- Confidence must be in `[0.0, 1.0]`, and `reason` should be concise and concrete.
