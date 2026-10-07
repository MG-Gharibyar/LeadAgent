from __future__ import annotations

import os
import re
import smtplib
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from .config import Config
from .database import Database
from .models import Lead, Status, instant, utcnow
from .outreach import STOP_STATUSES, draft_hash, followup_due, record_contact


@dataclass
class SMTPSettings:
    host: str
    port: int
    username: str
    password: str
    security: str
    from_email: str
    from_name: str

    @classmethod
    def from_environment(cls) -> SMTPSettings:
        host = os.environ.get("DSC_SMTP_HOST", "")
        security = os.environ.get("DSC_SMTP_SECURITY", "starttls")
        if not host or security not in {"starttls", "ssl"}:
            raise ValueError("Live mail requires SMTP host and TLS (starttls or ssl)")
        settings = cls(
            host,
            int(os.environ.get("DSC_SMTP_PORT", "587")),
            os.environ.get("DSC_SMTP_USERNAME", ""),
            os.environ.get("DSC_SMTP_PASSWORD", ""),
            security,
            os.environ.get("DSC_FROM_EMAIL", "kontakt@digitalskills-campus.de"),
            os.environ.get("DSC_FROM_NAME", "Digital Skills Campus"),
        )
        if not 1 <= settings.port <= 65535:
            raise ValueError("Invalid SMTP port")
        if not settings.username or not settings.password:
            raise ValueError("SMTP authentication credentials are required")
        if settings.from_email != "kontakt@digitalskills-campus.de":
            raise ValueError("v1 sender must be kontakt@digitalskills-campus.de")
        if any(c in settings.from_name for c in "\r\n"):
            raise ValueError("Invalid sender name")
        return settings


def email_valid(value: str) -> bool:
    return bool(
        re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", value)
    )


def build_message(lead: Lead, settings: SMTPSettings | None = None) -> EmailMessage:
    if not email_valid(lead.public_email):
        raise ValueError(
            "A valid public business email is required; use contact-page review otherwise"
        )
    if not lead.draft_text or not lead.draft_html or not lead.draft_subject:
        raise ValueError("A complete draft is required")
    if any(c in lead.draft_subject for c in "\r\n"):
        raise ValueError("Invalid subject")
    message = EmailMessage()
    sender = settings.from_email if settings else "kontakt@digitalskills-campus.de"
    name = settings.from_name if settings else "Digital Skills Campus"
    message["From"] = formataddr((name, sender))
    message["To"] = lead.public_email
    message["Subject"] = lead.draft_subject
    message["Message-ID"] = make_msgid(domain="digitalskills-campus.de")
    message.set_content(lead.draft_text)
    message.add_alternative(lead.draft_html, subtype="html")
    return message


def blockers(lead: Lead, config: Config, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(UTC)
    result = []
    if lead.suppressed or lead.outreach_status in STOP_STATUSES:
        result.append("Suppressed, customer or already responded")
    if lead.country != "Germany" or lead.final_score < config.minimum_score:
        result.append("Lead does not satisfy qualification policy")
    if not lead.draft_text or not email_valid(lead.public_email):
        result.append("Missing draft or valid public email")
    if not lead.approved_draft_hash or lead.approved_draft_hash != draft_hash(lead):
        result.append("Current draft requires human approval")
    if not lead.approved_at or now - instant(lead.approved_at) > timedelta(
        days=config.mail.approval_valid_days
    ):
        result.append("Approval missing or expired")
    if (
        lead.email_permission_status not in config.mail.allowed_permission_states
        or not lead.email_permission_basis.strip()
    ):
        result.append("No policy-permitted documented email permission basis")
    if (lead.approved_permission_status, lead.approved_permission_basis) != (
        lead.email_permission_status,
        lead.email_permission_basis,
    ):
        result.append("Permission basis changed since approval")
    if not lead.last_researched_at or now - instant(lead.last_researched_at) > timedelta(
        days=config.mail.evidence_max_age_days
    ):
        result.append("Research evidence is missing or stale")
    if lead.last_researched_at and instant(lead.last_researched_at) > now + timedelta(minutes=5):
        result.append("Research timestamp is in the future")
    if not lead.draft_evidence or any(
        now - instant(e.retrieved_at) > timedelta(days=config.mail.evidence_max_age_days)
        or instant(e.retrieved_at) > now + timedelta(minutes=5)
        for e in lead.draft_evidence
    ):
        result.append("Personalization evidence is missing, stale or future-dated")
    if lead.draft_kind == "initial" and (
        lead.contact_count or lead.outreach_status != Status.APPROVED.value
    ):
        result.append("Initial outreach already attempted or not approved")
    if lead.draft_kind == "followup" and not followup_due(lead, config, now):
        result.append("Follow-up timing/count does not permit contact")
    if lead.draft_kind not in {"initial", "followup"}:
        result.append("Unknown message kind")
    return result


def smtp_transport(settings: SMTPSettings, message: EmailMessage) -> None:
    context = ssl.create_default_context()
    client: smtplib.SMTP
    if settings.security == "ssl":
        client = smtplib.SMTP_SSL(settings.host, settings.port, timeout=30, context=context)
    else:
        client = smtplib.SMTP(settings.host, settings.port, timeout=30)
    # SMTP acceptance is the success boundary. A failure on QUIT must not mislabel acceptance.
    try:
        client.ehlo()
        if settings.security == "starttls":
            client.starttls(context=context)
            client.ehlo()
        client.login(settings.username, settings.password)
        refused = client.send_message(message)
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)
    finally:
        client.close()


@dataclass
class DeliveryResult:
    delivery_id: int
    state: str
    blockers: list[str]


class Mailer:
    def __init__(
        self, db: Database, config: Config, transport: Callable[[EmailMessage], None] | None = None
    ) -> None:
        self.db, self.config, self.transport = db, config, transport

    def send(self, lead_id: int, live: bool = False) -> DeliveryResult:
        settings = None
        if live and self.transport is None:
            # Validate credentials before consuming the one-shot reservation.
            settings = SMTPSettings.from_environment()
        with self.db.transaction():
            lead = self.db.get(lead_id)
            if lead.suppressed or lead.outreach_status in STOP_STATUSES:
                raise ValueError("Suppressed or responded lead cannot be sent, including dry-run")
            if lead.draft_kind == "initial" and lead.contact_count:
                raise ValueError("Initial outreach already attempted")
            if lead.draft_kind == "followup" and not followup_due(lead, self.config):
                raise ValueError("Follow-up is not due or already exhausted")
            if live and self.transport is None and lead.normalized_domain.endswith(".example"):
                raise ValueError("Synthetic fixture domains cannot receive real SMTP mail")
            message = build_message(lead, settings)
            reasons = blockers(lead, self.config)
            if live:
                if not self.config.mail.automatic_sending_enabled:
                    reasons.append("Live sending disabled in config")
                if reasons:
                    raise ValueError("Sending blocked: " + "; ".join(reasons))
                last = self.db.connection.execute(
                    "SELECT created_at FROM deliveries WHERE mode='LIVE' ORDER BY id DESC LIMIT 1"
                ).fetchone()
                if last and datetime.now(UTC) - instant(last[0]) < timedelta(
                    seconds=self.config.mail.minimum_interval_seconds
                ):
                    raise ValueError("Global sending rate limit; retry after configured interval")
            at = utcnow()
            cursor = self.db.connection.execute(
                "INSERT INTO deliveries(lead_id,kind,mode,state,created_at,updated_at,draft_hash,"
                "permission_status,permission_basis,approved_by,recipient,message_id,subject,text_body,html_body) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    lead_id,
                    lead.draft_kind,
                    "LIVE" if live else "DRY_RUN",
                    "RESERVED" if live else "DRY_RUN",
                    at,
                    at,
                    draft_hash(lead),
                    lead.email_permission_status,
                    lead.email_permission_basis,
                    lead.approved_by,
                    lead.public_email,
                    str(message["Message-ID"]),
                    lead.draft_subject,
                    lead.draft_text,
                    lead.draft_html,
                ),
            )
            delivery_id = cursor.lastrowid
            assert delivery_id is not None
            if live:
                # Reserve before network I/O. Crashes and uncertain SMTP failures never cause retry.
                record_contact(lead, self.config, at)
                self.db.save(lead)
            self.db.audit(
                lead_id,
                "LIVE_RESERVED" if live else "MAIL_DRY_RUN",
                lead.approved_by or "operator",
                lead.draft_kind,
            )
        if not live:
            return DeliveryResult(delivery_id, "DRY_RUN", reasons)
        try:
            # Hold the write lock while sending: concurrent suppression cannot slip between
            # the final suppression check and transport. Reservation already survives a crash.
            with self.db.transaction():
                current = self.db.get(lead_id)
                if current.suppressed or current.outreach_status in STOP_STATUSES:
                    raise ValueError("Suppression recorded after reservation; transport cancelled")
                if (
                    current.email_permission_status != lead.email_permission_status
                    or current.email_permission_basis != lead.email_permission_basis
                ):
                    raise ValueError("Permission changed after reservation; transport cancelled")
                if self.transport:
                    self.transport(message)
                else:
                    assert settings is not None
                    smtp_transport(settings, message)
                self.db.connection.execute(
                    "UPDATE deliveries SET state='ACCEPTED',updated_at=? WHERE id=?",
                    (utcnow(), delivery_id),
                )
                if lead.draft_kind == "initial":
                    current.initial_delivery_confirmed = True
                    self.db.save(current)
                self.db.audit(lead_id, "SMTP_ACCEPTED", lead.approved_by, str(delivery_id))
            return DeliveryResult(delivery_id, "ACCEPTED", [])
        except Exception as exc:
            # SMTP errors may include addresses/credentials; record only the exception class.
            with self.db.transaction():
                self.db.connection.execute(
                    "UPDATE deliveries SET state='FAILED_OR_UNCERTAIN',updated_at=?,error_type=? WHERE id=?",
                    (utcnow(), type(exc).__name__, delivery_id),
                )
                current = self.db.get(lead_id)
                current.next_contact_allowed_at = (
                    ""  # No follow-up after failed/uncertain delivery.
                )
                self.db.save(current)
                self.db.audit(lead_id, "DELIVERY_FAILED_OR_UNCERTAIN", "system", type(exc).__name__)
            return DeliveryResult(delivery_id, "FAILED_OR_UNCERTAIN", [type(exc).__name__])


def contacted(db: Database, lead_id: int, config: Config, actor: str, basis: str) -> None:
    """Record contact performed outside LeadAgent, conservatively consuming its contact slot."""
    if not actor.strip() or not basis.strip():
        raise ValueError("Manual contact requires an actor and an explanation/basis")
    with db.transaction():
        lead = db.get(lead_id)
        if lead.suppressed or lead.contact_count:
            raise ValueError("Suppressed or previously contacted lead cannot be contacted again")
        lead.draft_kind = "initial"
        at = utcnow()
        db.connection.execute(
            "INSERT INTO deliveries(lead_id,kind,mode,state,created_at,updated_at,draft_hash,"
            "permission_status,permission_basis,approved_by,recipient,message_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                lead_id,
                "initial",
                "MANUAL",
                "RECORDED",
                at,
                at,
                "",
                lead.email_permission_status,
                basis,
                actor,
                lead.public_email,
                "",
            ),
        )
        record_contact(lead, config, at, confirmed=True)
        db.save(lead)
        db.audit(lead_id, "MANUAL_CONTACT", actor, basis)
