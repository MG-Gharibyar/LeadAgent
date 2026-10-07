from __future__ import annotations

import argparse
import getpass
import html
import json
import os
import time
from pathlib import Path
from typing import Any

from .config import Config
from .database import Database
from .identity import normalize_domain
from .mail import Mailer, blockers, contacted, email_valid
from .models import Lead
from .outreach import STOP_STATUSES, approve, draft_hash
from .templates import SECTORS, render


def stopped(lead: Lead) -> bool:
    return bool(lead.contact_count or lead.suppressed or lead.outreach_status in STOP_STATUSES)


def import_leads(db: Database, path: Path, sector: str, config: Config) -> list[Lead]:
    data: Any = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("leads", []) if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ValueError("Expected a lead list or an object containing leads")
    result = []
    for row in rows:
        email = str(row.get("email", row.get("public_email", ""))).strip().lower()
        if not email_valid(email):
            raise ValueError("Import requires a valid business email")
        domain = normalize_domain(email.split("@", 1)[1])
        website = row.get("website") or f"https://{domain}"
        lead = Lead(
            company_name=row.get("company", row.get("company_name", "")),
            website=website,
            public_email=email,
            city=row.get("city", ""),
            segment=row.get("sector", row.get("segment", sector)),
            notes=row.get("notes", ""),
            public_contact_name_if_relevant=row.get("contact_name", ""),
            source_urls=[row.get("source_url") or str(path)],
        )
        if "company_name" in row and "evidence" in row:
            exported = Lead.from_dict(row)
            # Import research, never reusable approvals or permission grants from JSON.
            for field in (
                "country",
                "last_researched_at",
                "evidence",
                "final_score",
                "recommended_dsc_service",
                "source_urls",
            ):
                setattr(lead, field, getattr(exported, field))
        if lead.segment == "tax_advisory":
            lead.segment = "tax_advisor"
        if lead.segment not in SECTORS:
            raise ValueError("Unsupported import sector")
        lead, _ = db.upsert(lead, [(domain, row.get("source_url") or str(path))])
        if row.get("contacted_manually") and not stopped(lead):
            contacted(db, lead.id or 0, config, "import", f"Manual contact recorded in {path}")
            lead = db.get(lead.id or 0)
        result.append(lead)
    return result


def migrate_sent(db: Database, paths: list[Path], config: Config) -> None:
    # Idempotent, fail closed on malformed history; never alter the legacy files.
    for path in paths:
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("sent"), list):
            raise ValueError("Invalid legacy sent history")
        for address in data["sent"]:
            if not isinstance(address, str) or not email_valid(address):
                raise ValueError("Invalid address in legacy sent history")
        legacy_leads = path.parent / "karlsruhe_kanzleien_outreach.json"
        if legacy_leads.exists():
            import_leads(db, legacy_leads, "law_firm", config)
        for address in data["sent"]:
            mark_contacted(db, address, config, "legacy-import", f"Imported from {path}")


def mark_contacted(db: Database, email: str, config: Config, actor: str, notes: str) -> None:
    email = email.strip().lower()
    if not email_valid(email):
        raise ValueError("Invalid email")
    domain = normalize_domain(email.split("@", 1)[1])
    lead, _ = db.upsert(
        Lead(company_name=domain, website=f"https://{domain}", public_email=email, notes=notes)
    )
    if not stopped(lead):
        contacted(db, lead.id or 0, config, actor, notes)


def draft(lead: Lead) -> None:
    lead.draft_subject, lead.draft_text = render(lead.segment, lead.company_name)
    lead.draft_html = (
        "<html><body>" + html.escape(lead.draft_text).replace("\n", "<br>") + "</body></html>"
    )
    lead.approved_draft_hash = ""
    lead.approved_at = ""
    lead.approved_by = ""


def dispatch(args: argparse.Namespace, db: Database, config: Config) -> int:
    migrate_sent(db, [Path("OutReach/.sent_outreach.json"), Path(".sent_outreach.json")], config)
    action = args.outreach_command
    if action == "mark-contacted":
        mark_contacted(db, args.email, config, args.actor, args.notes)
        return 0
    if action in {"history", "pending"}:
        for lead in db.all():
            if action == "pending" and stopped(lead):
                continue
            print(
                json.dumps(
                    {
                        "company": lead.company_name,
                        "sector": lead.segment,
                        "email": lead.public_email,
                        "domain": lead.domain,
                        "first_seen": lead.first_seen_at,
                        "contacted_at": lead.first_contact_at,
                        "contact_method": [
                            h["mode"] for h in db.history(lead.id or 0) if h["mode"] != "DRY_RUN"
                        ],
                        "status": lead.outreach_status if stopped(lead) else "NEW",
                        "source": lead.source_urls,
                        "notes": lead.notes,
                    },
                    ensure_ascii=False,
                )
            )
        return 0
    imported = import_leads(db, Path(args.input), args.sector, config) if args.input else db.all()
    leads = [
        lead
        for lead in imported
        if lead.segment.replace("tax_advisory", "tax_advisor") == args.sector
    ]
    pending: list[Lead] = []
    seen: set[int | None] = set()
    already = sum(stopped(lead) for lead in leads)
    for lead in leads:
        if stopped(lead) or lead.id in seen:
            continue
        seen.add(lead.id)
        draft(lead)
        db.save(lead)
        pending.append(lead)
    print(
        f"Sektor: {args.sector}\nGesamt: {len(leads)}\nBereits kontaktiert/gesperrt: {already}\nÜbersprungen: {len(leads) - len(pending)}\nOffen: {len(pending)}"
    )
    for lead in pending:
        print(
            f"\n{lead.company_name} <{lead.public_email}>\nBetreff: {lead.draft_subject}\n{lead.draft_text}"
        )
    if action != "send" or not pending:
        print("DRY-RUN: Es wurde nichts versendet.")
        return 0
    # Evaluate existing policy before asking the human to approve the exact displayed drafts.
    if not config.mail.automatic_sending_enabled:
        raise ValueError("Live sending disabled in config")
    for lead in pending:
        # Approval-only blockers are resolved by the single explicit batch confirmation below.
        reasons = [
            r
            for r in blockers(lead, config)
            if r
            not in {
                "Current draft requires human approval",
                "Approval missing or expired",
                "Permission basis changed since approval",
                "Initial outreach already attempted or not approved",
            }
        ]
        if reasons:
            raise ValueError(f"Batch blocked for lead {lead.id}: {'; '.join(reasons)}")
    if input(f"Wirklich alle {len(pending)} Mails versenden? Tippe JA: ").strip() != "JA":
        print("Abgebrochen.")
        return 0
    if not os.environ.get("DSC_SMTP_PASSWORD"):
        os.environ["DSC_SMTP_PASSWORD"] = getpass.getpass(
            "Passwort für kontakt@digitalskills-campus.de: "
        )
    displayed_hashes = {lead.id: draft_hash(lead) for lead in pending}
    failures = 0
    for index, lead in enumerate(pending):
        if index:
            time.sleep(config.mail.minimum_interval_seconds)
        if draft_hash(db.get(lead.id or 0)) != displayed_hashes[lead.id]:
            raise ValueError("Displayed draft changed; preview and confirm a fresh batch")
        approve(db, lead.id or 0, args.actor, config)
        result = Mailer(db, config).send(lead.id or 0, live=True)
        print(f"{lead.company_name}: {result.state}")
        failures += result.state != "ACCEPTED"
    return 1 if failures else 0
