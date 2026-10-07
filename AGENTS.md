# DSC LeadAgent operating instructions

This project researches German B2B organizations for DSC. Optimize evidence quality,
service fit and current need. Never fill a quota with weak leads. No telephone outreach.

## Daily operation

1. Activate the project virtual environment. Validate the local config.
2. Run `python -m leadagent discover`. It discovers public candidates, researches,
   classifies, scores, deduplicates, selects at most 20 new qualified companies across
   all runs of the UTC day, creates evidence-backed German drafts, and writes reports.
3. Inspect the report and sources. Correct questionable classification/evidence before approval.
4. Leave live sending disabled unless the owner has configured an appropriate permission
   policy. A public mailbox alone is not permission. Never invent a permission basis.
5. Human approval is per current draft and permission basis. Do not autonomously issue
   `approve`, set consent, or enable live sending as part of daily discovery.
6. List `followups`; prepare only if requested. Each requires separate human approval.
   One follow-up at most, no earlier than the configured delay. Responses stop automation.
7. Record rejection, opt-out, customer and external contact immediately. Suppression is
   permanent. Never reset counts or delete identities to recycle prospects.

## Implementation and validation

Use Python, SQLite, deterministic business rules, typed provider interfaces and source
provenance. Public HTTP access must respect robots, access controls, rate limits and source
terms. Skip CAPTCHA/authentication/blocked sources. Never use personal-data enrichment.

Before committing: `pytest`, `ruff check .`, `ruff format --check .`, `mypy leadagent`,
`git diff --check`. Tests and committed examples must use reserved `.example` domains
and synthetic organizations. Never stage production databases/reports/logs, local seed
lists, exports, `.env` or credentials. Never merge the feature branch into main.

Delivery reservations must survive crashes. An uncertain SMTP result consumes the slot;
never retry automatically. Schema changes need a versioned migration, preserving identities,
suppression records, permission audit and delivery history. Do not bypass gates for demos.
