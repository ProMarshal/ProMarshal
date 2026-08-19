# Dependency Topic

## PMI Definition
Dependencies are logical relationships between project activities or between projects. They define the sequence in which work must be performed and identify external factors the project relies upon.

## What to Understand
- Internal dependencies (within project)
- External dependencies (outside project control)
- Predecessor and successor relationships
- Critical path dependencies
- Shared resource dependencies

## What to Infer
From scope and stakeholders, infer:
- System integration dependencies
- Data dependencies
- Cross-team dependencies
- Vendor/third-party dependencies
- Infrastructure dependencies

## What to Probe (Only if Missing)
- "What needs to happen before this project can start/finish?"
- "Are there other teams or systems you're waiting on?"
- "What external deliverables does this project depend on?"

## Intelligent Behaviors
- Map dependencies to timeline risks
- Flag circular dependencies
- Identify uncontrolled external dependencies
- Suggest dependency tracking approach
- Connect to risk assessment

## Common Pitfalls to Flag
- External dependencies without agreements
- Single-source dependencies (no backup)
- Dependencies on unmapped projects
- Missing SLAs for external dependencies
- Not tracking dependency status

## Dependency Types
| Type | Description |
|------|-------------|
| Finish-to-Start | B cannot start until A finishes |
| Start-to-Start | B cannot start until A starts |
| Finish-to-Finish | B cannot finish until A finishes |
| Start-to-Finish | B cannot finish until A starts |

## Common Dependency Categories
| Category | Examples |
|----------|----------|
| Technical | API availability, infrastructure setup |
| Data | Data migration, data access |
| Resource | Shared team members, equipment |
| External | Vendor deliverables, partner integrations |
| Regulatory | Approvals, certifications |
| Business | Budget approval, stakeholder sign-off |
