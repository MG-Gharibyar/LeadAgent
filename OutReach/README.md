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
and delivery history are reused; no database schema change is needed.

Private lead JSON, histories, databases, reports and credentials are ignored.
Historical Git commits may already contain legacy data; this change removes
legacy JSON from tracking without deleting local files or rewriting Git history.
