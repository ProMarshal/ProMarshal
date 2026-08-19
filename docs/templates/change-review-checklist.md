# Change Review Checklist

Use this before merging any non-trivial change.

- Linked ADR(s) or RFC
- Invariants reviewed (`docs/architecture/invariants.md`)
- Capability contract updated (if behavior changed)
- Regression test added/updated (if bug fix)
- Multi-project/multi-user impact reviewed
- Caching/invalidation behavior reviewed
- Failure modes reviewed (empty, partial, retry, race)
- Rollback path defined

