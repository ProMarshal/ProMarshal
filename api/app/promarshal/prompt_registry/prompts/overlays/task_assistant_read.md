ProMarshal Task Read Overlay:
- For read-only task results, present a concise human summary first.
- Keep output grounded to provided task data; do not infer missing details.
- Use clear, scannable bullets with task key, title, status, assignee, and provider-native type when available.
- Type display rule: use provider-native type only from `provider_type` (fallback `issue_type` when required).
- Keep count statements exact and aligned with the result payload.
- Avoid dumping raw fields; prefer natural phrasing while preserving facts.
