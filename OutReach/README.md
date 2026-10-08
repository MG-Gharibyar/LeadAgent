# DSC Outreach

The recommended production workflow is now **manually researched contacts in private JSON**.
Automatic discovery is optional and is not required for outreach.

Full instructions: [../docs/MANUAL_OUTREACH.md](../docs/MANUAL_OUTREACH.md)

## Quick start

Create a private file such as `OutReach/data/stuttgart_kanzleien.json`:

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
      "company": "Kanzlei Name",
      "email": "info@kanzlei.de",
      "website": "https://kanzlei.de/",
      "city": "Stuttgart",
      "source_url": "https://kanzlei.de/impressum"
    }
  ]
}
```

Preview:

```bash
python3 -m leadagent outreach preview law_firm \
  --input OutReach/data/stuttgart_kanzleien.json \
  --region Stuttgart --max 20
```

Send after reviewing the preview:

```bash
python3 -m leadagent outreach send law_firm \
  --input OutReach/data/stuttgart_kanzleien.json \
  --region Stuttgart --max 20 --actor OWNER
```

The send command requires one final `JA`. Successful SMTP messages are copied to the
account's IMAP `Sent` folder and appear in Thunderbird. Existing contact history,
suppression, reply tracking and customer state are shared through the production SQLite
database.

Real JSON files under `OutReach/data/` are ignored by Git.
