# Shared sector outreach

The legacy law-firm entry point now delegates to `leadagent.batch_outreach`.
`leadagent.templates.SECTORS` holds central templates; adding a template enables
its outreach CLI sector without duplicating SMTP code. Discovery classification
remains separately configured. The law-firm body and prices are preserved verbatim.
`tax_advisory` in existing LeadAgent records maps to `tax_advisor`.

Activate `.venv` and run from the repository root:

```sh
python -m leadagent outreach preview law_firm --input OutReach/karlsruhe_kanzleien_outreach.json
python -m leadagent outreach preview medical_practice --input OutReach/data/medical_practices.json
python -m leadagent outreach preview tax_advisor --input OutReach/data/tax_advisors.json
python -m leadagent outreach preview it_service_provider --input OutReach/data/it_service_providers.json
python -m leadagent outreach pending
python -m leadagent outreach history
python -m leadagent outreach mark-contacted info@organization.example --actor OWNER --notes 'Previously contacted manually'
python -m leadagent outreach send law_firm --actor OWNER
```

`--input` accepts a list or `{"leads": [...]}` with company, email, website,
city and sector; optional source_url, notes, contact_name and contacted_manually.
Existing LeadAgent records with company_name and evidence can also be imported.
No message bodies are required in prospect JSON. Rendered drafts and delivery
snapshots are retained in the existing private SQLite database for approval/audit.
Without `--input`, preview/send use stored leads. Replace `law_firm` in the send
command with any of the four sectors. Preview never opens SMTP.

Live send still requires enabled owner configuration, qualified current evidence,
and a documented policy-permitted permission basis for every lead. Factual JSON
alone does not confer permission or qualification. Review the displayed drafts;
`JA` approves the displayed eligible batch with the named actor. No per-email
prompt occurs. The existing permission CLI remains authoritative; this engine
never invents consent. A blocked batch sends nothing.

SMTP defaults to mxe9aa.netcup.net:465 using SSL. Sender, reply-to, SMTP username
and envelope sender use kontakt@digitalskills-campus.de. Password comes from
DSC_SMTP_PASSWORD or getpass after confirmation. KIT usernames are rejected.
The configured sending interval applies between deliveries.

On CLI startup, existing OutReach/.sent_outreach.json and .sent_outreach.json
are imported idempotently into the same SQLite identity/delivery history used by
discovery and mail. Adjacent legacy law-firm JSON supplies names and identities.
Original files remain untouched. Malformed history fails closed. Run from the
repository root so automatic legacy discovery finds these paths. To migrate a
separate installation, copy its private JSON files into this ignored OutReach
folder before the first invocation. Back up the database and original files.

Aliases normalize www/URLs and email domains; sourced company+city aliases catch
cross-domain duplicates. Shared mailbox-provider domains are conservatively
suppressed as a group when no independent website is supplied: review those
identities before importing. No override or history reset is provided.

Successful acceptance is committed immediately. Reservations commit before
network I/O; failed/uncertain deliveries consume the slot but are never marked
ACCEPTED and never retried automatically. Existing suppression, permission audit
and delivery history are reused. Schema migration v3 adds immutable RFC822
bytes and independent Sent-copy status to delivery records, preserving all existing
identity, suppression, permission and contact data. Older accepted deliveries
without original RFC822 bytes are marked UNAVAILABLE; no email is reconstructed
or resent. Manual historical contacts do not require an automatic Sent copy.

Private lead JSON, histories, databases, reports and credentials are ignored.
Historical Git commits may already contain legacy data; this change removes
legacy JSON from tracking without deleting local files or rewriting Git history.


## IMAP Sent copies and repair

After SMTP acceptance, the shared Mailer archives the identical RFC822 bytes
(including Date, Message-ID and MIME content) to mxe9aa.netcup.net:993 over SSL,
using kontakt@digitalskills-campus.de and the same SMTP mailbox password.
It discovers the selectable `\Sent` special-use mailbox through IMAP LIST,
including SPECIAL-USE when advertised. It never guesses a name or creates a folder.
This is the account's normal Thunderbird Sent mailbox; no Thunderbird integration
or local profile path is necessary.

SMTP acceptance and contact history commit immediately before the IMAP operation,
so a failed append cannot erase delivery success. A durable reservation still
precedes SMTP to protect against crashes. IMAP failure records sent_copy_status
FAILED, emits a warning, and leaves SMTP state ACCEPTED. A crash between SMTP
acceptance and archiving leaves PENDING for repair.

```sh
python -m leadagent outreach detect-sent
python -m leadagent outreach repair-sent
```

`detect-sent` only logs in, lists folders and logs out. `repair-sent` only handles
SMTP-accepted PENDING/FAILED deliveries: it searches and verifies the original
Message-ID before APPEND, then commits SAVED and the detected folder. Repairs
serialize under SQLite's write lock and skip already saved copies. If APPEND
succeeded but its acknowledgement was lost, repair finds the existing copy.
Neither command invokes SMTP. Set DSC_SMTP_PASSWORD securely in the shell, or
enter it through getpass in an interactive terminal. Do not put it in Git.
Sent-copy fields appear in `python -m leadagent show ID` delivery history.

## Karlsruhe-region discovery

```sh
python -m leadagent discover --sector law_firm --location Karlsruhe --limit 12
python -m leadagent outreach preview law_firm
python -m leadagent outreach send law_firm --actor OWNER
```

Targeted discovery uses the existing official Brave API provider and requires
DSC_BRAVE_API_KEY. `leadagent.sectors` defines the sector/location search profiles.
Karlsruhe searches include the requested surrounding towns and a broader region
query. API result budgets are distributed among locations. This is a practical
regional search focus around 30–40 km, not a geocoded hard radius. Other towns
may appear and their publicly sourced locations need review.

When the search key is unavailable, public URLs researched independently can
be supplied explicitly through the existing seed provider:

```sh
python -m leadagent discover --sector law_firm --location Karlsruhe --provider seeds --input seeds.local.karlsruhe.json --limit 8
```

Seeds contain website and optional source_url/company_name/city hints, not email
bodies or invented research evidence. PublicWebClient still independently checks
access, robots, rate limits and the source pages. Discovery never invokes SMTP
or IMAP. The original 20-address campaign migrates before any CLI discovery.
Contacted/stopped identities are excluded before research whenever their domain
is known, and again after website/email/name+city alias resolution.
Generic SEO names such as "Rechtsanwalt Rastatt" are never company+city keys.

Each run records new IDs, duplicate and contacted exclusions, qualification counts,
sector and location. Private discovery-N-review.md reports include even candidates
below the qualification threshold. Outreach preview shows the latest relevant
run counts and companies/cities/emails/websites/sources/scores/reasons. Unqualified
candidates are visibly REVIEW_ONLY; send selects qualified, unsuppressed records
with a valid business email, then applies every existing evidence/permission gate.
Windows use is never inferred just because the business is a law firm. Existing
law-firm drafting also uses the preserved central template.
