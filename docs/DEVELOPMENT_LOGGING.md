# Local development logging standard

Each repository has one root DEVELOPMENT_LOG.local.md, excluded by .gitignore.
Never stage/force-add it, publish it, copy its history into committed docs, or
store credentials/personal/client/transaction data in it. The log must remain
readable Markdown, not raw terminal output.

## Required events

Record every new feature and every change, correction or retirement of a feature.
For work spanning both repositories with permission, update both logs with their
own changes and cross-references. Do not modify the sibling log otherwise.

## Entry format

Use a unique ID and actual timestamp with seconds/UTC offset, e.g.
YYYY-MM-DDTHH:mm:ss+01:00 (Africa/Lagos). Include:

- Status: EXISTING, ADDED, CHANGED, CORRECTED, PLANNED, DEFERRED, BLOCKED or RETIRED.
- Feature and clear user-facing functions.
- Change: what was added, altered or fixed; prior behaviour where relevant.
- Reason: why, if known; explicitly unknown otherwise.
- Files/routes/contracts affected.
- Verification: exact checks/results and any untested behaviour.
- Dependencies, migrations/data impact and compatibility notes where relevant.
- TODOs or follow-up decisions; distinguish delivered work from target requirements.

Append chronological entries; do not replace previous history to hide corrections.
A compact current inventory/roadmap can be updated, with a corresponding change
entry. Use links or file references instead of huge code blocks.

## Baseline and new clones

Baseline features are “observed existing,” not “added today.” Never invent original
implementation dates/times. Earlier date-only entries retain their known precision.
Tasks are built in both repositories and must be listed as existing, not planned.
Provider infrastructure alone is not proof of working delivery.

Ignored logs do not travel with Git. If absent in a new clone, create a dated
source-verified baseline and acknowledge unavailable history. Existing logs should
be extended, not recreated or truncated.
