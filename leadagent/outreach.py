from __future__ import annotations

import hashlib
import html
import json
from datetime import UTC, datetime, timedelta

from .config import Config
from .database import Database
from .models import Evidence, Lead, Permission, Segment, Status, instant, utcnow
from .research import plain_excerpt
from .rules import INTENT_RULES
from .templates import render

STOP_STATUSES = {
    Status.RESPONDED.value,
    Status.INTERESTED.value,
    Status.REJECTED.value,
    Status.DO_NOT_CONTACT.value,
    Status.CUSTOMER.value,
}


def initial_contacted(lead: Lead) -> bool:
    return bool(
        lead.contact_count
        or lead.suppressed
        or lead.outreach_status
        in STOP_STATUSES | {Status.CONTACTED.value, Status.FOLLOWUP_SENT.value}
    )


def draft_hash(lead: Lead) -> str:
    parts = (
        lead.public_email,
        lead.draft_subject,
        lead.draft_text,
        lead.draft_html,
        lead.draft_kind,
        json.dumps([e.__dict__ for e in lead.draft_evidence], sort_keys=True),
    )
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


def followup_due(lead: Lead, config: Config, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    return bool(
        not lead.suppressed
        and lead.outreach_status not in STOP_STATUSES
        and lead.initial_delivery_confirmed
        and lead.contact_count == 1
        and lead.followup_count < config.maximum_followups
        and lead.next_contact_allowed_at
        and now >= instant(lead.next_contact_allowed_at)
    )


def prepare_draft(lead: Lead, kind: str = "initial") -> Lead:
    if kind not in {"initial", "followup"}:
        raise ValueError("Unknown message kind")
    if lead.suppressed:
        raise ValueError("Suppressed lead cannot receive a draft")
    if not lead.recommended_dsc_service:
        raise ValueError("A supported service match is required")
    observations = [
        e
        for e in lead.evidence
        if e.kind in {"service_need", "intent", "technology", "digital", "operations", "segment"}
    ]

    def relevance(e: Evidence) -> int:
        if e.kind == "service_need":
            return 100
        if e.kind == "intent":
            return INTENT_RULES.get(e.value, (0, ""))[0] * 2
        if lead.recommended_dsc_service == "Backup & Recovery Review" and e.value == "backup":
            return 50
        if lead.recommended_dsc_service == "Microsoft 365 Security" and e.value == "microsoft_365":
            return 50
        if e.kind == "segment":
            return 5
        return 25

    observations.sort(key=lambda e: (relevance(e), instant(e.retrieved_at)), reverse=True)
    observation = observations[0] if observations else None
    if observation is None:
        raise ValueError("A company-specific sourced observation is required")
    quote = plain_excerpt(observation.excerpt)
    # Raw web content is treated as quoted data, never as instructions.
    quote = quote.replace('"', "'")[:240].rstrip(". ")
    company = plain_excerpt(lead.company_name)
    if any(c in company for c in "\r\n"):
        raise ValueError("Invalid company name")
    service = lead.recommended_dsc_service
    subject = f"{service} für {company}"
    deliverables = {
        "Penetration Testing": "ein Bericht über im vereinbarten Umfang geprüfte Angriffspfade mit priorisierten Maßnahmen",
        "IT Security Training": "eine auf den vereinbarten Bedarf abgestimmte Schulung mit praktischen Handlungsempfehlungen",
        "Managed IT & Security": "ein abgestimmter Betreuungsumfang mit dokumentierten Zuständigkeiten und Sicherheitsmaßnahmen",
        "Windows Security Assessment": "eine priorisierte technische Bewertung der Windows- und Zugriffssicherheit mit konkreten Maßnahmen und einem schriftlichen Ergebnisbericht",
        "Microsoft 365 Security": "eine priorisierte Bewertung der Identitäts-, Zugriffs- und Mandanteneinstellungen mit konkreten Maßnahmen und einem schriftlichen Ergebnisbericht",
        "Backup & Recovery Review": "eine Bewertung der Sicherungs- und Wiederherstellungsabläufe mit priorisierten Verbesserungen und einem schriftlichen Ergebnisbericht",
        "Partner Security Assessment": "ein klar abgegrenztes technisches Assessment mit priorisierten Maßnahmen und einem Ergebnisbericht für Ihre Kunden",
    }
    outcome = deliverables.get(
        service, "eine priorisierte technische Bewertung mit schriftlichem Ergebnisbericht"
    )
    fit = (
        "Für ein Systemhaus kann eine ergänzende Assessment-Kapazität für Kundenprojekte interessant sein."
        if lead.segment == Segment.IT.value
        else "Daraus ergibt sich ein möglicher Ansatzpunkt für eine kompakte technische Sicherheitsprüfung."
    )
    body = (
        f"Guten Tag,\n\nauf Ihrer Website beschreiben Sie: „{quote}“.\n\n"
        f"{fit} Digital Skills Campus bietet hierzu {service} an. "
        f"Das Ergebnis ist {outcome}.\n\n"
        "Wenn das zu Ihrer aktuellen Planung passt, sende ich Ihnen gern eine kurze Übersicht zum Prüfungsumfang."
    )
    if kind == "followup":
        subject = f"Kurze Rückfrage: {service} für {company}"
        body = (
            f"Guten Tag,\n\nich möchte einmal zu meiner Nachricht zum Thema {service} nachfragen. "
            "Wenn eine kurze Übersicht zum Prüfungsumfang hilfreich wäre, sende ich sie Ihnen gern schriftlich. "
            "Ohne Rückmeldung werde ich nicht erneut nachfassen."
        )
    body += (
        "\n\nFreundliche Grüße\nHasib Gharibyar\nDigital Skills Campus\n"
        "kontakt@digitalskills-campus.de\ndigitalskills-campus.de\n\n"
        "Falls Sie keine weitere Nachricht wünschen, genügt eine kurze Antwort."
    )
    if kind == "initial" and lead.segment == Segment.LAW.value:
        subject, body = render("law_firm", company, lead.campaign_region or lead.city)
    lead.draft_subject, lead.draft_text = subject, body
    lead.draft_html = (
        '<!doctype html><html lang="de"><body>'
        + "".join("<p>" + html.escape(p).replace("\n", "<br>") + "</p>" for p in body.split("\n\n"))
        + "</body></html>"
    )
    lead.draft_kind = kind
    lead.draft_evidence_urls = [observation.source_url]
    lead.draft_evidence = [observation]
    lead.approved_draft_hash = ""
    lead.approved_at = ""
    lead.approved_by = ""
    if kind == "initial":
        lead.outreach_status = Status.READY_FOR_REVIEW.value
    else:
        lead.outreach_status = Status.FOLLOWUP_DUE.value
    return lead


def approve(db: Database, lead_id: int, actor: str, config: Config) -> Lead:
    if not actor.strip():
        raise ValueError("Approval requires a named human reviewer")
    with db.transaction():
        lead = db.get(lead_id)
        if lead.suppressed or lead.outreach_status in STOP_STATUSES:
            raise ValueError("Lead is suppressed or has already responded")
        if not lead.draft_text or lead.final_score < config.minimum_score:
            raise ValueError("A qualified, evidence-based draft is required")
        if lead.draft_kind == "initial" and initial_contacted(lead):
            raise ValueError("Initial outreach already attempted or recorded")
        if lead.draft_kind == "followup" and not followup_due(lead, config):
            raise ValueError("Follow-up is not due or exhausted")
        lead.approved_draft_hash = draft_hash(lead)
        lead.approved_at, lead.approved_by = utcnow(), actor
        lead.approved_permission_status = lead.email_permission_status
        lead.approved_permission_basis = lead.email_permission_basis
        lead.outreach_status = (
            Status.APPROVED.value if lead.draft_kind == "initial" else Status.FOLLOWUP_DUE.value
        )
        db.save(lead)
        db.audit(lead_id, "APPROVED", actor, lead.draft_kind)
        return lead


def set_permission(db: Database, lead_id: int, state: str, basis: str, actor: str) -> None:
    Permission(state)
    if not actor.strip() or not basis.strip():
        raise ValueError("Permission changes require an actor and documented basis")
    with db.transaction():
        lead = db.get(lead_id)
        if lead.suppressed:
            raise ValueError("Suppression is permanent; permission cannot reactivate a lead")
        lead.email_permission_status = state
        lead.email_permission_basis, lead.email_permission_recorded_at = basis, utcnow()
        lead.approved_draft_hash = ""
        if state == Permission.PROHIBITED.value:
            lead.do_not_contact, lead.outreach_status = True, Status.DO_NOT_CONTACT.value
        db.save(lead)
        db.audit(lead_id, "PERMISSION", actor, f"{state}: {basis}")


def suppress_contact(
    db: Database,
    lead_id: int,
    actor: str,
    reason: str = "",
) -> None:
    """Persist an explicit no-marketing request as a permanent suppression.

    Keep the minimum identity/suppression record so future imports cannot accidentally
    recreate the company as a fresh prospect.
    """
    if not actor.strip():
        raise ValueError("Suppression requires a named operator")
    at = utcnow()
    with db.transaction():
        lead = db.get(lead_id)
        already_suppressed = lead.do_not_contact or (
            lead.email_permission_status == Permission.PROHIBITED.value
        )
        lead.do_not_contact = True
        lead.next_contact_allowed_at = ""
        lead.approved_draft_hash = ""
        lead.approved_at = ""
        lead.approved_by = ""
        lead.approved_permission_status = ""
        lead.approved_permission_basis = ""
        lead.email_permission_status = Permission.PROHIBITED.value
        lead.email_permission_basis = reason.strip() or "Explicit no-marketing request"
        lead.email_permission_recorded_at = at
        if lead.customer or lead.outreach_status == Status.CUSTOMER.value:
            lead.customer_opt_out = True
        else:
            lead.outreach_status = Status.DO_NOT_CONTACT.value
        db.save(lead)
        action = "SUPPRESSION_REAFFIRMED" if already_suppressed else "DO_NOT_CONTACT"
        db.audit(lead_id, action, actor, reason)


def set_status(
    db: Database,
    lead_id: int,
    status: Status,
    actor: str,
    reason: str = "",
    config: Config | None = None,
) -> None:
    if status not in {
        Status.REJECTED,
        Status.DO_NOT_CONTACT,
        Status.CUSTOMER,
        Status.RESPONDED,
        Status.INTERESTED,
    }:
        raise ValueError("Status change must use a lifecycle command")
    if not actor.strip():
        raise ValueError("Status changes require a named operator")
    if status == Status.DO_NOT_CONTACT:
        suppress_contact(db, lead_id, actor, reason)
        return
    with db.transaction():
        lead = db.get(lead_id)
        if lead.suppressed:
            raise ValueError("Suppression is permanent")
        lead.outreach_status = status.value
        lead.rejected = status == Status.REJECTED
        lead.do_not_contact = status == Status.DO_NOT_CONTACT
        lead.customer = status == Status.CUSTOMER
        lead.approved_draft_hash = ""
        db.save(lead)
        if status == Status.CUSTOMER:
            from .customers import initialize

            initialize(lead, config or Config(), utcnow())
            db.save(lead)
            db.audit(lead_id, "CUSTOMER_CREATED", actor, reason)
        else:
            db.audit(lead_id, status.value, actor, reason)


def record_contact(lead: Lead, config: Config, at: str, confirmed: bool = False) -> None:
    if lead.draft_kind == "initial":
        lead.initial_delivery_confirmed = confirmed
        lead.contact_count = 1
        lead.first_contact_at = at
        lead.next_contact_allowed_at = (
            instant(at) + timedelta(days=config.followup_delay_days)
        ).isoformat()
        lead.outreach_status = Status.CONTACTED.value
    else:
        lead.contact_count = 2
        lead.followup_count = 1
        lead.next_contact_allowed_at = ""
        lead.outreach_status = Status.FOLLOWUP_SENT.value
    lead.last_contact_at = at
    lead.approved_draft_hash = ""
