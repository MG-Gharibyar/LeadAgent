# DSC LeadAgent

A quality-first German B2B research CLI for Digital Skills Campus. It discovers public
company websites, records evidence, qualifies up to **20 new companies per UTC day**,
selects one DSC offer, drafts German outreach, and maintains durable contact history.
Discovery never sends email. Sending defaults to dry-run.

## First dry-run (synthetic data, no network or email)

Requires Python 3.11+ with pip and venv:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m leadagent --database data/demo.sqlite3 discover --provider fixture --input tests/fixtures/companies.json
python -m leadagent --database data/demo.sqlite3 leads
python -m leadagent --database data/demo.sqlite3 mail preview 1
python -m leadagent --database data/demo.sqlite3 mail send 1
python -m leadagent --database data/demo.sqlite3 stats
```

The demo uses reserved `.example` domains. No SMTP calls occur. Real SMTP explicitly
refuses these fixture domains. Use a separate database for real operation; never delete
a production database to get new leads. Generated reports are under `reports/` and are
ignored by Git. [Committed synthetic report](examples/synthetic-leads.md) and
[CSV](examples/synthetic-leads.csv) illustrate the four segments.

## Architecture

`providers` → `research`/`web` → `scoring` → `database` → `pipeline` → `outreach` →
`report`; explicit `mail` commands pass through permission and approval gates.

- Typed discovery/research interfaces support additional permitted APIs or directories.
- SQLite with versioned migrations (including preservation of early v1 databases), WAL, write transactions, identity aliases,
  audit events, run summaries and immutable delivery reservations.
- Complete Lead fields are stored as typed JSON payloads with indexed identity, quota
  and contact fields. Facts and scoring reasons retain excerpt, source URL and UTC
  retrieval timestamp. Drafts retain the exact personalization evidence used.
- Bounded public HTML research extracts explicit observations. Scripts and style data
  are ignored. No LLM, browser automation, private enrichment or telephone workflow.
- Structured application logs contain event names, run IDs and exception types rather
  than prospect text. Detailed contact data remains in the local SQLite database.

## Discovery providers and sources

1. `seeds`: public company websites from an owner-maintained local JSON file, for example
   `[{"website": "https://company.example/"}]`. Names/cities in seed files are hints;
   public pages must verify identity and German location.
2. `brave`: official Brave Search API, configured queries targeted to Germany and German
   content, with a daily rotating region. Requires `DSC_BRAVE_API_KEY`, API entitlement
   and permitted storage/use. Results identify URLs; search snippets are not scored as
   company facts. See [official API country/language documentation](https://api-dashboard.search.brave.com/app/documentation/web-search/codes).
3. `fixture`: reserved `.example` synthetic evidence, completely offline. Its simulated
   retrieval timestamps are rebased to the run time so the demonstration remains usable.

Public company homepages, legal notices, contact/service/team pages and public
career/partner pages are researched. Sources require operator review of applicable
terms; robots alone is not a grant of permission. There are no hardcoded scrapers for
professional directories or social networks. A directory can supply URLs through a
future permitted adapter, but is never itself a qualified prospect.

The HTTP client checks robots before each URL/redirect, fails closed when robots is
unavailable (404 means no published restrictions), honors crawl delays/request rates,
limits bytes/time/pages, pauses hosts on 401/403/429, rejects private/reserved network
addresses and skips human verification. It does not retry blocked sources. Configure
`allowed_hosts` when a strict domain allowlist is appropriate (include `www` aliases).
Use an outbound firewall that denies private networks: DNS validation cannot by itself
eliminate DNS rebinding between lookup and connection.

Real discovery:

```bash
# Add public company URLs to ignored seeds.local.json, or export DSC_BRAVE_API_KEY.
python -m leadagent discover                       # seeds by default
python -m leadagent discover --provider brave      # autonomous API discovery + research
python -m leadagent report
python -m leadagent leads
python -m leadagent show 1
```

Research is deliberately conservative: German public postal address plus explicit
country, matching company identity in an Impressum, one supported segment, a written
contact route where available, and sufficient sourced fit/intent. Unsupported,
ambiguous or blocked sites can produce fewer than 20 leads, including zero. Parser
rules are not proof of a technical deficiency. Human review of sources remains required.
The v1 parser does not render JavaScript or infer hidden infrastructure. Dated expansion
facts can be provided by a future research adapter; no expansion is guessed from copy.

## Qualification and service selection

Four segments: practices, tax advisory, law firms and IT system houses/MSPs. Classification
requires an unambiguous public segment description. No points for presumed Windows use,
absence of a security team or assumed regulatory obligations.

Fit totals 100: segment 25, visible SME team 15, Windows/M365 20, backup/managed IT 15,
digital client services 10, mapped DSC compatibility 15. Intent accumulates explicit
requests 45, partner opportunities 35, IT/security hiring 30, transformation 25,
external IT dependency 20, dated expansion 15, multiple locations 10, security concerns
15 and MSP partner fit 20; capped at 100. Default final score is **70% fit + 30% intent**,
minimum **70**. Each award includes the rule, reason, points and evidence. Weights and
thresholds are configurable; ambiguous identity/location and disqualifications prevent
qualification. Quality and intent sort first; segment targets only break score ties.

Primary offers: practice backup/recovery or Windows assessment; tax/law Windows or
Microsoft 365 assessment; MSP partner assessment. Providers already advertising
security assessments/pentests are excluded from a presumed-gap partner pitch. An explicit
request can select penetration testing, training, managed IT, backup or M365 security.
Messages quote one supported observation, offer one concrete scope/outcome and use a
written, low-pressure CTA. They make no invented incident or compliance claims.

## Written outreach and human gate

```bash
python -m leadagent mail preview 1
# Record ONLY an actual permission basis, with an auditable reference/explanation.
python -m leadagent permission 1 REQUESTED_INFORMATION --actor 'Hasib Gharibyar' --basis 'Inbound scope request, recorded ticket reference'
python -m leadagent approve 1 --actor 'Hasib Gharibyar'
python -m leadagent mail send 1                    # DRY_RUN, even after approval
python -m leadagent mail send-approved             # DRY_RUN batch
```

`UNKNOWN` and `PROHIBITED` can never be permitted. Defaults allow only `CONSENTED` and
`REQUESTED_INFORMATION`, each with a recorded basis. Other documented states are stored,
but sending requires explicit policy configuration. Publicly listed email addresses
are never treated as consent. These states are operational records, not a determination
that a particular marketing message is lawful. The owner must establish the appropriate
policy and basis before enabling real delivery.

To enable SMTP, export the variables in `.env.example` into your process environment;
this application never automatically loads `.env`. Configure
`mail.automatic_sending_enabled: true`, retain an appropriate permission allowlist, then
explicitly use `mail send 1 --live`. Both the config switch and the flag are required.
Sender is `kontakt@digitalskills-campus.de`. SMTP uses authenticated STARTTLS or TLS;
plaintext is refused. Text and simple HTML are sent as multipart email.

Approval records the human actor and a hash of recipient, subject, text, HTML, kind and
personalization evidence, plus a permission snapshot. Editing/research/permission changes
invalidate approval; approvals expire after seven days by default. Live sending also
checks qualification and evidence age (30 days by default). Global reservations enforce
the configured send interval (60 seconds). `send-approved --live` skips blocked/rate-limited
entries and exits nonzero; it never bypasses the interval or waits to fill a batch.

Every live attempt is reserved durably before SMTP and is unique per company/message kind.
Accepted messages record permission, reviewer, message ID and subject/body snapshots.
`ACCEPTED` means SMTP accepted the message, not inbox delivery. Failed or uncertain
attempts consume the contact slot and disable follow-ups; no automated retry. A process
crash can leave `RESERVED`, which also blocks re-sending and follow-ups. Inspect such
records with `show`; manual operator investigation is required, not a count reset.

## Contact fatigue and responses

```bash
python -m leadagent contacted 1 --actor 'Hasib Gharibyar' --basis 'Existing external written correspondence recorded'
python -m leadagent followups                     # list only
python -m leadagent followups --prepare           # eligible only; fresh draft, no send
python -m leadagent mail approve 1 --actor 'Hasib Gharibyar'  # separate follow-up approval
python -m leadagent reject 1 --actor 'Hasib Gharibyar' --reason 'Negative reply'
python -m leadagent unsubscribe 1 --actor 'Hasib Gharibyar' --reason 'Opt-out reply'
python -m leadagent block 1 --actor 'Hasib Gharibyar' --reason 'Do not contact'
python -m leadagent responded 1 --actor 'Hasib Gharibyar'
python -m leadagent interested 1 --actor 'Hasib Gharibyar'
python -m leadagent customer 1 --actor 'Hasib Gharibyar'
```

One initial contact, one follow-up at most, ten days minimum by default. Follow-ups require
confirmed initial SMTP acceptance or recorded external contact and their own approval.
Responses stop automatic follow-ups. Rejection, opt-out and customer flags permanently
suppress outreach. No further message follows an unanswered follow-up. Email replies and
bounces are not ingested automatically in v1: the owner must record them before another
send. Contact forms require manual written review; there is no automatic form submission.

## Daily operation and data safety

An external scheduler can run discovery once daily without granting mail authority:

```cron
0 7 * * * cd /path/to/LeadAgent && .venv/bin/python -m leadagent discover
```

Use a stable production database, protected filesystem and backups. CLI-created files use
a restrictive umask. Back up via SQLite's backup API or an SQLite-aware backup tool;
copying only the database file while WAL writes are active is unsafe. Identity/suppression
history must survive retention and cleanup; never purge it to recycle prospects. Establish
an appropriate retention policy for evidence, notes and reports before production use.
Reports/export/data/log/cache directories, SQLite files, `.env`, local seed lists and
credentials are ignored. Commit only synthetic fixtures; verify staging explicitly.
The daily report contains evidence, service, contact route, draft, permission, prior
history and REVIEW/SEND/SKIP/BLOCK action; CSV prevents formula injection.

## Validation

```bash
pytest
ruff check .
ruff format --check .
mypy leadagent
git diff --check
```

Tests use synthetic organizations exclusively, including concurrent deduplication/quota/
mail tests, suppression, exact follow-up timing, dry-run, uncertain delivery, provenance,
public-access controls, reports, CLI and invalid configurations. CI validates on Python 3.11.
The operating instructions for agents are in [AGENTS.md](AGENTS.md).
