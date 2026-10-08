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
from .models import Lead, Permission
from .outreach import approve, draft_hash, initial_contacted, set_permission
from .sent_mail import append_sent, detect_sent, mailbox_password, repair_sent
from .templates import SECTORS, render


def stopped(lead: Lead) -> bool:
    return initial_contacted(lead)


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


def public_business_basis(lead: Lead) -> str:
    sources = [lead.contact_page, lead.website, *lead.source_urls]
    source = next((item for item in sources if item.startswith(("https://", "http://"))), "")
    if not source:
        raise ValueError("Public business outreach requires a public source URL")
    return (
        "Owner-approved initial outreach to a public business contact address "
        f"published for the organization; source: {source}"
    )


def draft(lead: Lead, region: str = "") -> None:
    lead.draft_subject, lead.draft_text = render(
        lead.segment, lead.company_name, lead.campaign_region or region or lead.city
    )
    lead.draft_html = (
        "<html><body>" + html.escape(lead.draft_text).replace("\n", "<br>") + "</body></html>"
    )
    lead.approved_draft_hash = ""
    lead.approved_at = ""
    lead.approved_by = ""


def dispatch(args: argparse.Namespace, db: Database, config: Config) -> int:
    migrate_sent(db, [Path("OutReach/.sent_outreach.json"), Path(".sent_outreach.json")], config)
    action = args.outreach_command
    if action == "detect-sent":
        print(f"IMAP \\Sent: {detect_sent(mailbox_password())}")
        return 0
    if action == "repair-sent":
        count = db.connection.execute(
            "SELECT COUNT(*) FROM deliveries WHERE mode='LIVE' AND state='ACCEPTED' AND sent_copy_status IN ('PENDING','FAILED')"
        ).fetchone()[0]
        if not count:
            print("Keine ausstehenden IMAP-Sent-Kopien; kein SMTP-Versand.")
            return 0
        password = mailbox_password()
        results = repair_sent(db, lambda raw, message_id: append_sent(password, raw, message_id))
        for repaired in results:
            print(json.dumps(repaired, ensure_ascii=False))
        return int(any(result["sent_copy_status"] == "FAILED" for result in results))
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
    requested_region = str(getattr(args, "region", "") or "").strip()
    if any(c in requested_region for c in "\r\n"):
        raise ValueError("Invalid campaign region")
    if requested_region:
        leads = [
            lead
            for lead in leads
            if lead.campaign_region.casefold() == requested_region.casefold()
        ]
    max_count = getattr(args, "max_count", None)
    if max_count is not None and (type(max_count) is not int or max_count < 1):
        raise ValueError("--max must be a positive integer")
    summaries = [
        json.loads(row[0])
        for row in db.connection.execute(
            "SELECT summary FROM runs WHERE summary != '' ORDER BY id DESC"
        )
    ]
    summary: dict[str, Any] = next(
        (item for item in summaries if item.get("sector", "") in {"", args.sector}), {}
    )
    campaign_region = requested_region or str(summary.get("location") or "")
    pending: list[Lead] = []
    seen: set[int | None] = set()
    already = sum(stopped(lead) for lead in leads)
    for lead in leads:
        if stopped(lead) or lead.id in seen:
            continue
        seen.add(lead.id)
        if action == "send" and (
            lead.final_score < config.minimum_score
            or not lead.qualified_at
            or not email_valid(lead.public_email)
        ):
            continue
        draft(lead, campaign_region)
        db.save(lead)
        pending.append(lead)
    if max_count is not None:
        pending = pending[:max_count]
    print(
        f"Sektor: {args.sector}"
        f"{f' | Region: {requested_region}' if requested_region else ''}\n"
        f"Gesamt: {len(leads)}\n"
        f"Bereits kontaktiert/gesperrt: {already}\n"
        f"Übersprungen/nicht im Batch: {len(leads) - len(pending)}\n"
        f"Offen im Batch: {len(pending)}"
    )
    qualified_count = len(
        {
            lead.id
            for lead in leads
            if lead.qualified_at
            and lead.final_score >= config.minimum_score
            and not stopped(lead)
            and email_valid(lead.public_email)
        }
    )
    print(
        f"Discovered: {summary.get('discovered', 0)}\nDuplicates: {summary.get('duplicates', 0)}\nAlready contacted: {already}\nQualified: {qualified_count}\nPending outreach: {len(pending)}"
    )
    for lead in pending:
        print(
            "PENDING_OUTREACH"
            if lead.qualified_at
            and lead.final_score >= config.minimum_score
            and email_valid(lead.public_email)
            else "REVIEW_ONLY — below qualification policy"
        )
        print(
            f"City: {lead.city or 'unverified'} | Website: {lead.website} | Source: {', '.join(lead.source_urls)} | Score: {lead.final_score} | Reasons: {'; '.join(reason.reason for reason in lead.score_reasons) or 'Not yet qualified'}"
        )
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
                "No policy-permitted documented email permission basis",
                "Permission basis changed since approval",
                "Initial outreach already attempted or not approved",
            }
        ]
        if (
            lead.email_permission_status
            not in {Permission.UNKNOWN.value, Permission.PUBLIC_BUSINESS_OUTREACH.value}
            and lead.email_permission_status not in config.mail.allowed_permission_states
        ):
            reasons.append("Existing email permission state does not permit outreach")
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
        current = db.get(lead.id or 0)
        if current.email_permission_status == Permission.UNKNOWN.value:
            set_permission(
                db,
                lead.id or 0,
                Permission.PUBLIC_BUSINESS_OUTREACH.value,
                public_business_basis(current),
                args.actor,
            )
        approve(db, lead.id or 0, args.actor, config)
        result = Mailer(db, config).send(lead.id or 0, live=True)
        print(
            f"{lead.company_name}: {result.state}; sent_copy_status={result.sent_copy_status}; folder={result.sent_folder}"
        )
        failures += result.state != "ACCEPTED"
    return 1 if failures else 0
