# Manual JSON outreach workflow

This is the recommended production workflow for DSC LeadAgent.

The automatic discovery code remains available for experiments, but production campaigns
do not depend on it. Contacts are researched manually, written to a private JSON file,
previewed through the shared LeadAgent engine, and only then sent after one explicit
`JA` confirmation.

## 1. Prepare a private campaign JSON

Store real lead files under `OutReach/data/`. That directory is ignored by Git.

Example: `OutReach/data/stuttgart_kanzleien.json`

```json
{
  "campaign": {
    "sector": "law_firm",
    "region": "Stuttgart",
    "reviewed_by": "OWNER",
    "manual_reviewed": true
  },
  "leads": [
    {
      "company": "Beispiel Kanzlei",
      "email": "info@beispiel-kanzlei.de",
      "website": "https://www.beispiel-kanzlei.de/",
      "city": "Stuttgart",
      "source_url": "https://www.beispiel-kanzlei.de/impressum",
      "notes": "Optional internal note"
    }
  ]
}
```

For a manually reviewed lead, provide:

- `company`: organization name
- `email`: public business email address
- `website`: explicit public organization website
- `city`: office city
- `source_url`: public page used during manual review, preferably contact/imprint
- `manual_reviewed: true`: either on each lead or once in the campaign object
- campaign `region`: the metro wording used in outreach, e.g. `Stuttgart`
- `reviewed_by`: the human reviewer, normally `OWNER`

The import records this as a manual human review. It does **not** invent recipient consent.
The final batch confirmation records `PUBLIC_BUSINESS_OUTREACH` as the operational basis
for previously-unknown public business contacts.

## 2. Preview only

From the repository root:

```bash
cd ~/Documents/Hasib/LeadAgent
source .venv/bin/activate

python3 -m leadagent outreach preview law_firm \
  --input OutReach/data/stuttgart_kanzleien.json \
  --region Stuttgart \
  --max 20
```

Preview never sends SMTP mail. Check company, recipient, subject, text and campaign wording.
For this campaign the template should say **"Stuttgart und Umgebung"**.

If a lead was already contacted, suppressed, rejected, opted out or is already a customer,
the shared SQLite history remains authoritative and prevents a new initial outreach.

## 3. Send the reviewed batch

Use the **same JSON file** and the same region:

```bash
python3 -m leadagent outreach send law_firm \
  --input OutReach/data/stuttgart_kanzleien.json \
  --region Stuttgart \
  --max 20 \
  --actor OWNER
```

The command prints the exact pending messages and then asks once:

```text
Wirklich alle N Mails versenden? Tippe JA:
```

Only the exact answer `JA` starts SMTP delivery. The mailbox password is read from
`DSC_SMTP_PASSWORD` or requested interactively. It is never stored in the JSON file.

Successful output looks like:

```text
Firma: ACCEPTED; sent_copy_status=SAVED; folder=Sent
```

`ACCEPTED` means the SMTP server accepted the message. `SAVED; folder=Sent` means the
same RFC822 message was archived in the IMAP Sent folder, so it is visible in Thunderbird.

The configured production pacing is two seconds between messages.

## 4. After sending

Useful commands:

```bash
python3 -m leadagent outreach history
python3 -m leadagent outreach pending
python3 -m leadagent inbox sync
python3 -m leadagent inbox stats
python3 -m leadagent inbox review
```

If SMTP succeeded but the Sent-folder copy failed:

```bash
python3 -m leadagent outreach repair-sent
```

That command repairs only the IMAP copy and never repeats SMTP delivery.

## 5. Repeat for another city or sector

Create another private JSON file and change only the campaign metadata and data:

```bash
python3 -m leadagent outreach preview law_firm \
  --input OutReach/data/mannheim_kanzleien.json \
  --region Mannheim --max 20

python3 -m leadagent outreach send law_firm \
  --input OutReach/data/mannheim_kanzleien.json \
  --region Mannheim --max 20 --actor OWNER
```

Supported sectors are:

- `law_firm`
- `medical_practice`
- `tax_advisor`
- `it_service_provider`

The same SMTP, Sent-copy, deduplication, suppression, inbox and customer-history logic is
shared across all sectors.

## 6. Data safety

Real campaign JSON files belong in `OutReach/data/` and must remain private. The path is
ignored by Git. Do not commit customer/prospect lists, passwords or mailbox exports.

Do not delete the production SQLite database to "start fresh". It contains deduplication,
delivery history, suppressions, replies and customer state. Back it up with an SQLite-aware
backup method.

The automated `discover` subsystem is optional. If a third-party directory or public API
returns `AccessDenied`, that does not block this manual JSON workflow.
