# Dependency Topic Insights

## Real-World Experience

### Do's
- Track external dependencies separately - you control internal ones, external ones control you
- Get SLAs or commitments for external dependencies - "they'll deliver" isn't a plan
- Build buffer around dependencies - if they're due Tuesday, plan as if you need it Friday
- Create visibility for dependency status - make it obvious when something is blocked
- Identify circular dependencies early - they're planning puzzles that need solving up front

### Don'ts
- Don't assume other teams share your timeline urgency - their priorities may differ
- Don't proceed without confirming dependency delivery dates - hope is not a strategy
- Don't hide dependency blockers - surface them immediately
- Don't forget data dependencies - access, quality, and format issues kill projects

### Warnings
- Dependencies between projects are often the root cause of multi-project failures
- "They said they'd do it" without a date or documented commitment is a red flag
- Late dependency deliveries cascade - a 1-week slip upstream often becomes 2-3 weeks downstream
- Vendor dependencies are high risk - contracts don't guarantee execution
- Integration dependencies are especially risky - APIs don't work together until they're tested together

### Recovery Tips
- If a dependency slips, assess immediately: Can you parallelize other work? Is there a workaround?
- When dependency status is unclear, escalate to get clarity - waiting passively wastes time
