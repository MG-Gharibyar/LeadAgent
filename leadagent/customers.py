"""Recurring customer contact, independent of acquisition permission and drafts."""

from __future__ import annotations

import argparse
import getpass
import html
import os
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from .config import Config
from .database import Database
from .models import Lead, Status, instant

CUSTOMER_TEMPLATES = {
    "QUARTERLY_CHECKIN": (
        "Kurzer Check-in zu IT & Security",
        "Gerne können wir bei Bedarf auch einen kurzen Sicherheits-, Backup- oder "
        "Konfigurationscheck durchführen und gemeinsam prüfen, ob sich seit unserem "
        "letzten Termin Änderungen ergeben haben.",
    ),
    "SECURITY_REVIEW": (
        "Sicherheitscheck",
        "Gerne prüfen wir gemeinsam Ihren aktuellen Sicherheitsbedarf und mögliche Änderungen Ihrer IT-Konfiguration.",
    ),
    "BACKUP_REVIEW": (
        "Backup-Check",
        "Gerne prüfen wir bei Bedarf Ihre Sicherungs- und Wiederherstellungsabläufe.",
    ),
    "RETEST": (
        "Nachprüfung Ihrer Sicherheitsmaßnahmen",
        "Gerne überprüfen wir bei Bedarf gemeinsam, ob die vereinbarten Sicherheitsmaßnahmen wirksam umgesetzt wurden.",
    ),
    "GENERAL_SERVICE": (
        "Unterstützung für Ihre IT",
        "Gerne besprechen wir bei Bedarf Ihre aktuellen IT-Aufgaben und unterstützen Sie bei der Umsetzung.",
    ),
}


def is_customer(lead: Lead) -> bool:
    return lead.customer or lead.outreach_status == Status.CUSTOMER.value


def due(lead: Lead, now: datetime | None = None) -> bool:
    return bool(
        is_customer(lead)
        and lead.next_customer_checkin_at
        and (now or datetime.now(UTC)) >= instant(lead.next_customer_checkin_at)
    )


def blockers(db: Database, lead: Lead, now: datetime | None = None) -> list[str]:
    from .mail import email_valid

    reasons = []
    if not is_customer(lead):
        reasons.append("Organization is not a CUSTOMER")
    if not due(lead, now):
        reasons.append("Customer check-in is not due")
    if lead.do_not_contact or lead.outreach_status in {"DO_NOT_CONTACT", "UNSUBSCRIBED"}:
        reasons.append("Explicit contact suppression")
    if lead.customer_opt_out:
        reasons.append("Customer opted out")
    if lead.email_disabled or not email_valid(lead.public_email):
        reasons.append("Invalid or disabled email")
    if db.connection.execute(
        "SELECT 1 FROM deliveries WHERE lead_id=? AND kind='customer_checkin' AND mode='LIVE' AND customer_period=?",
        (lead.id, lead.next_customer_checkin_at or ""),
    ).fetchone():
        reasons.append("Customer period already reserved; no automatic retry")
    return reasons


def message(lead: Lead, config: Config) -> Lead:
    """Render centrally into an ephemeral copy; never replace a prospect draft."""
    subject, offer = CUSTOMER_TEMPLATES[config.customer_message_type]
    company = lead.company_name
    if any(c in company for c in "\r\n"):
        raise ValueError("Invalid company name")
    body = (
        f"Liebes Team von {company},\n\n"
        "ich wollte mich im Rahmen unserer Zusammenarbeit kurz bei Ihnen melden und "
        "nachfragen, ob aktuell Unterstützungsbedarf im Bereich IT & Security besteht.\n\n"
        f"{offer}\n\n"
        "Falls aktuell kein Bedarf besteht, ist selbstverständlich nichts weiter zu tun.\n\n"
        "Mit freundlichen Grüßen\nHasib Gharibyar\nDigital Skills Campus\n"
        "kontakt@digitalskills-campus.de\ndigitalskills-campus.de\n\n"
        "Falls Sie keine weiteren Kunden-Check-ins wünschen, genügt eine kurze Antwort."
    )
    return replace(
        lead,
        draft_kind="customer_checkin",
        draft_subject=subject,
        draft_text=body,
        draft_html="<html><body>" + html.escape(body).replace("\n", "<br>") + "</body></html>",
        draft_evidence=[],
        approved_by="",
    )


def initialize(lead: Lead, config: Config, at: str) -> None:
    lead.customer = True
    lead.outreach_status = Status.CUSTOMER.value
    lead.customer_since = at
    lead.last_customer_checkin_at = None
    lead.next_customer_checkin_at = (
        instant(at) + timedelta(days=config.customer_checkin_interval_days)
    ).isoformat()
    lead.approved_draft_hash = ""


def record_due(db: Database, lead: Lead) -> None:
    # Daily scheduler calls record each period only once.
    if not db.connection.execute(
        "SELECT 1 FROM audit WHERE lead_id=? AND action='CUSTOMER_CHECKIN_DUE' AND detail=?",
        (lead.id, lead.next_customer_checkin_at),
    ).fetchone():
        db.audit(lead.id, "CUSTOMER_CHECKIN_DUE", "system", lead.next_customer_checkin_at or "")


def dispatch(args: argparse.Namespace, db: Database, config: Config) -> int:
    from .mail import Mailer

    customers = [lead for lead in db.all() if is_customer(lead)]
    current_due = [lead for lead in customers if due(lead)]
    with db.transaction():
        for lead in current_due:
            record_due(db, lead)
    action = args.customers_command
    if action in {"list", "due"}:
        print(f"Customers: {len(customers)}\nDue now: {len(current_due)}")
        for lead in customers if action == "list" else current_due:
            print(
                f"{lead.id}  {lead.company_name}  last: {lead.last_customer_checkin_at or '—'}  due: {lead.next_customer_checkin_at or '—'}  {'; '.join(blockers(db, lead))}"
            )
        return 0
    pending = [lead for lead in current_due if not blockers(db, lead)]
    print(
        f"Customers due: {len(current_due)}\nSuppressed: {len(current_due) - len(pending)}\nWill send: {len(pending)}"
    )
    snapshots = {}
    from .outreach import draft_hash

    for lead in current_due:
        rendered = message(lead, config)
        snapshots[lead.id] = (lead.next_customer_checkin_at, draft_hash(rendered))
        print(
            f"\n{lead.company_name} <{lead.public_email}>\nBetreff: {rendered.draft_subject}\n{rendered.draft_text}"
        )
    if action == "preview" or not pending:
        print("Es wurde nichts versendet.")
        return 0
    if not args.actor.strip():
        raise ValueError("Customer sending requires a named operator")
    if not config.mail.automatic_sending_enabled:
        raise ValueError("Live sending disabled in config")
    if input(f"Wirklich alle {len(pending)} Kunden-E-Mails versenden? Tippe JA: ").strip() != "JA":
        print("Abgebrochen.")
        return 0
    if not os.environ.get("DSC_SMTP_PASSWORD"):
        os.environ["DSC_SMTP_PASSWORD"] = getpass.getpass("SMTP-Passwort: ")
    failures = 0
    for index, lead in enumerate(pending):
        if index:
            time.sleep(config.mail.minimum_interval_seconds)
        result = Mailer(db, config).send_customer(
            lead.id or 0, args.actor, expected=snapshots[lead.id]
        )
        print(f"{lead.company_name}: {result.state}; sent_copy_status={result.sent_copy_status}")
        failures += result.state != "ACCEPTED"
    return int(bool(failures))
