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

1. `free` (**default**): no API key, subscription, or paid service. Combines:
   - OpenStreetMap business listings through the nonprofit
     [Private.coffee Overpass service](https://overpass.private.coffee/#terms), whose
     terms permit programmatic access including commercial use. Queries all four
     sectors: `law_firm`, `medical_practice`, `tax_advisor`, `it_service_provider`.
     Attribution: © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright),
     ODbL. Preserve this attribution when sharing OSM-derived discovery data.
   - For law firms around Karlsruhe, the local association's
     [public firm listing](https://anwaltsverein-karlsruhe.de/de/ausbildungsbereite-kanzleien).
     Its public pages/robots were reviewed; no login or member-only search is accessed.
   - An existing local seed file, when present, as another free source.
2. `seeds`: owner-maintained JSON such as
   `[{"website": "https://company.example/"}]`. Optional `company_name`, `city`, and
   `source_url` are discovery hints, never independently verified facts.
3. `brave`: **optional enhancement**, only when you already have permitted Brave API
   access and `DSC_BRAVE_API_KEY`. Missing credentials, network failures, rate limits,
   and invalid responses fall back to free sources. Normal operation never needs it.
4. `fixture`: reserved `.example` synthetic evidence, completely offline.

Directory adapters implement the existing typed `DiscoveryProvider` interface and return
`Candidate` objects. `free_providers.py` supplies reusable aggregation, a cached directory
client, sector-specific OSM queries, and a local law association adapter. Each source is
queried once per run, sequentially, with at least five seconds between directory HTTP
requests. Successful discovery responses are cached for 24 hours in ignored
`cache/discovery/`; company websites are independently researched afresh. Domain
normalization deduplicates results before research. Round-robin allocation shares the
candidate budget between sources, and duplicate entries retain all discovery provenance.
Source failures are reported as skipped, without retrying or switching hosts to bypass
restrictions. The command's JSON and SQLite run summary include per-source counts,
pre-research duplicates, candidate counts, and already-contacted exclusions.

Karlsruhe uses a 35 km radius, covering Karlsruhe, Ettlingen, Rheinstetten, Stutensee,
Bruchsal, Pfinztal, Waldbronn, Wörth am Rhein, Eggenstein-Leopoldshafen,
Linkenheim-Hochstetten, Weingarten (Baden), Bretten, Durmersheim, Rastatt, and other nearby
towns. Other locations use named administrative areas; without a location, discovery
rotates through the configured German regions. Source coverage varies by sector and city.

Directory entries supply only organization name, website, city and source URL/timestamp.
Classification, identity, contact route, location, and scoring come from the organization's
own public homepage, legal notice, contact/service/team or career/partner pages. Directory
observations are explicitly marked `discovery` and never scored. A directory result cannot
qualify on its own. Existing shared SQLite identities, suppression and contact history
remain authoritative across sectors. No delivery, permission or approval is created by
this workflow.

Public source terms can change: review them before adding adapters or scaling operation.
Wikidata's query endpoint currently disallows robots, so it is deliberately excluded.
No search HTML, commercial directory, CAPTCHA, authentication or anti-bot bypass is used.

The HTTP client checks robots before each URL/redirect, fails closed when robots is
unavailable (404 means no published restrictions), honors crawl delays/request rates,
limits bytes/time/pages, pauses hosts on 401/403/429, rejects private/reserved network
addresses and skips human verification. It does not retry blocked sources. Configure
`allowed_hosts` when a strict domain allowlist is appropriate (include `www` aliases).
Use an outbound firewall that denies private networks: DNS validation cannot by itself
eliminate DNS rebinding between lookup and connection.

Real discovery:

```bash
python -m leadagent discover \
  --provider free \
  --sector law_firm \
  --location Karlsruhe \
  --limit 20

python -m leadagent outreach preview law_firm
python -m leadagent outreach send law_firm --actor OWNER
```

The send command is a separate, human-confirmed batch workflow; it still requires the
configured sending policy, permission basis and confirmation. Discovery sends nothing.
The SMTP sender remains `kontakt@digitalskills-campus.de` and sent messages retain the
IMAP Sent copy and crash-safe duplicate-send protections.

Manual free URLs and optional Brave:

```bash
python -m leadagent discover --sector law_firm --location Karlsruhe \
  --provider seeds --input seeds.local.karlsruhe.json --limit 20
# Optional, only with your own permitted Brave key:
python -m leadagent discover --sector law_firm --location Karlsruhe --provider brave
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
suppress outreach. No further message follows an unanswered follow-up. Email replies and bounces can be synchronized read-only from the same Netcup IMAP mailbox.
Synchronization never invokes SMTP. High-confidence opt-outs, explicit rejections and
bounces update suppression/delivery state immediately; positive or ambiguous replies are
recorded for review before an INTERESTED state is applied. Contact forms still require
manual written review; there is no automatic form submission.

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

### Recurring customer check-ins

`python -m leadagent customer ID --actor OWNER` marks an organization as a customer,
permanently excludes it from acquisition outreach, and initializes its first check-in
for 90 days later. Configure `customer_checkin_interval_days` (for example 30, 90 or
180) and `customer_message_type` in `config.yaml`. Supported central message types:
`QUARTERLY_CHECKIN` (default), `SECURITY_REVIEW`, `BACKUP_REVIEW`, `RETEST`,
`GENERAL_SERVICE`. Templates are rendered centrally when previewing or sending.
A changed interval applies to newly created customers and the next schedule after
successful delivery; it does not rewrite existing due dates.

```bash
python -m leadagent customers list
python -m leadagent customers due
python -m leadagent customers preview
python -m leadagent customers send --actor OWNER
python -m leadagent show ID
python -m leadagent unsubscribe ID --actor OWNER --reason 'No further customer check-ins'
```

`customers due` is suitable for daily cron checks and never sends. `preview` renders
all due messages without sending. `send` displays due, suppressed and sendable counts,
renders the messages and requires one `JA` confirmation for the batch. Live sending
must be enabled in the existing mail configuration; SMTP credentials and the global
rate limit still apply. Customer eligibility is independent of prospect permission,
qualification, research freshness and prospect approval. Explicit DO_NOT_CONTACT,
UNSUBSCRIBED, customer opt-out and invalid/disabled email still block delivery.
Unsubscribing a customer records `CUSTOMER_OPT_OUT` and preserves its customer history.

Each due period has a durable delivery reservation. SMTP acceptance atomically records
the delivery and advances the customer's next date by the configured interval, before
the IMAP Sent copy. A failed Sent copy remains repairable with
`python -m leadagent outreach repair-sent`; repair never repeats SMTP. Failed or uncertain
SMTP leaves the due date unchanged but consumes that period's reservation, preventing
an automatic retry. Successful delivery allows the next recurring period to be sent.
Customer lifecycle and contact events appear in `show ID`.

Schema v4 preserves acquisition deliveries, identity aliases, suppression and audit
history. Existing customers receive an initial 90-day schedule based on their recorded
CUSTOMER event (migration time if none exists); running the migration again does not
reset the schedule.


### Exact multi-city radius discovery

For exact radii around arbitrary German cities, initialize the free local GeoNames index once:

```bash
python -m leadagent geo update
python -m leadagent discover --provider free --sector law_firm --area "Karlsruhe:50" --limit 20
python -m leadagent discover --provider free --sector law_firm \
  --area "Karlsruhe:50" --area "Stuttgart:30" --limit 40
```

`--area CITY:RADIUS_KM` is repeatable. Normal discovery then uses the local GeoNames
index and Haversine distance; no paid geocoder is required. Overlapping areas are
deduplicated and the nearest matching campaign center becomes the wording region.


### Inbox synchronization and reply tracking

```bash
python -m leadagent inbox sync
python -m leadagent inbox stats
python -m leadagent inbox review
python -m leadagent inbox apply EVENT_ID --actor OWNER
```

`inbox sync` reads up to the configured recent-message limit from INBOX over IMAP and
never sends mail. It matches threads by `In-Reply-To`/`References` against stored
delivery Message-IDs, then falls back to known sender/domain identity. Deterministic rules
classify opt-outs, rejections, information requests, meeting requests, bounces and
out-of-office messages. Ambiguous replies remain review items. Duplicate Message-IDs are
idempotently ignored.


### Owner-confirmed public business outreach

The batch outreach command does not label cold outreach as consent. For a qualified lead
whose business email is publicly sourced and whose permission state is still `UNKNOWN`,
the final human `JA` confirmation records `PUBLIC_BUSINESS_OUTREACH` together with a
public source URL and the named owner actor. Existing `PROHIBITED`, opt-out, suppression,
customer, response, duplicate-contact, stale-evidence and rate-limit gates remain in force.
This is an audit state describing how the contact was selected; it is not represented as
recipient consent.


### Region-scoped outreach batches

After discovery, preview and send a campaign without mixing pending leads from other
cities:

```bash
python -m leadagent outreach preview law_firm --region Stuttgart --max 20
python -m leadagent outreach send law_firm --region Stuttgart --max 20 --actor OWNER
```

`--region` matches the stored campaign center (for example Stuttgart even when the
individual office is in a nearby municipality), and `--max` caps the displayed and
confirmed batch. The final `JA` confirmation is still required before SMTP is used.


### Geo index self-healing and send pacing

If an exact `--area CITY:RADIUS_KM` center is missing from the local GeoNames cache,
the agent refreshes the Germany index once automatically and retries the lookup. The
outreach send interval is configured at 2 seconds between messages, keeping delivery
serial and auditable without the previous 60-second delay.
