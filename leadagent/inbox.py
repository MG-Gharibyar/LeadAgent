"""Read-only IMAP reply ingestion and deterministic CRM event classification."""

from __future__ import annotations

import argparse
import re
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime

from .config import Config
from .database import Database
from .identity import normalize_domain
from .models import Status, utcnow
from .outreach import STOP_STATUSES, set_status
from .sent_mail import connect, disconnect, mailbox_password

OWN_EMAIL = "kontakt@digitalskills-campus.de"


def message_text(message: Message) -> str:
    if message.is_multipart():
        parts = []
        for part in message.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(
                part.get("Content-Disposition", "")
            ).lower():
                try:
                    parts.append(part.get_content())
                except (LookupError, UnicodeError):
                    continue
        return "\n".join(parts)[:8000]
    try:
        return str(message.get_content())[:8000]
    except (LookupError, UnicodeError):
        payload = message.get_payload(decode=True) or b""
        return payload.decode("utf-8", errors="replace")[:8000]


def classify_text(subject: str, body: str, sender: str) -> tuple[str, float, str, str]:
    text = f"{subject}\n{body}".casefold()
    sender_l = sender.casefold()
    if (
        "mailer-daemon" in sender_l
        or "postmaster" in sender_l
        or re.search(r"\b(undeliverable|unzustellbar|delivery status notification|mail delivery failed)\b", text)
    ):
        return "BOUNCE", 0.99, "Delivery failure detected", ""
    if re.search(r"\b(abwesenheitsnotiz|automatische antwort|out of office|urlaub|bin bis .* nicht erreichbar)\b", text):
        return "OUT_OF_OFFICE", 0.95, "Automatic absence reply", ""
    if re.search(r"\b(keine weiteren (?:e-?mails|nachrichten)|nicht mehr kontaktieren|aus dem verteiler|abbestellen|unsubscribe|widerspreche)\b", text):
        return "OPT_OUT", 0.99, "Recipient requests no further messages", "DO_NOT_CONTACT"
    if re.search(r"\b(kein interesse|nicht interessiert|kommt für uns nicht in frage|sehen wir keinen bedarf)\b", text):
        return "NOT_INTERESTED", 0.97, "Recipient states no interest", "REJECTED"
    if re.search(r"\b(termin|telefonat|gespräch|gespraech|call|meeting|nächste woche|naechste woche)\b", text):
        return "MEETING_REQUEST", 0.90, "Reply appears to request or discuss a meeting", "INTERESTED"
    if re.search(r"\b(weitere informationen|mehr informationen|details|angebot|kosten|umfang|ablauf|unterlagen)\b", text):
        return "REQUESTED_INFORMATION", 0.88, "Reply requests or discusses additional information", "INTERESTED"
    return "REVIEW_REQUIRED", 0.50, "Reply received; manual review required", "RESPONDED"


def _references(message: Message) -> list[str]:
    values = []
    for header in ("In-Reply-To", "References"):
        raw = str(message.get(header, ""))
        values.extend(re.findall(r"<[^>]+>", raw))
    return list(dict.fromkeys(values))


def match_lead(db: Database, sender: str, refs: list[str]) -> int | None:
    for ref in refs:
        row = db.connection.execute(
            "SELECT lead_id FROM deliveries WHERE message_id=? ORDER BY id DESC LIMIT 1", (ref,)
        ).fetchone()
        if row:
            return int(row[0])
    sender = sender.strip().lower()
    exact = [lead.id for lead in db.all() if lead.public_email.lower() == sender and lead.id]
    if len(exact) == 1:
        return exact[0]
    if "@" in sender:
        try:
            domain = normalize_domain(sender.rsplit("@", 1)[1])
        except ValueError:
            return None
        row = db.connection.execute(
            "SELECT lead_id FROM domain_aliases WHERE domain=?", (domain,)
        ).fetchone()
        if row:
            return int(row[0])
    return None


def _apply_safe_state(db: Database, lead_id: int, classification: str, detail: str) -> str:
    lead = db.get(lead_id)
    if classification == "BOUNCE":
        with db.transaction():
            current = db.get(lead_id)
            current.email_disabled = True
            current.approved_draft_hash = ""
            db.save(current)
            db.audit(lead_id, "BOUNCE", "inbox", detail)
        return utcnow()
    if classification == "OPT_OUT":
        if lead.outreach_status != Status.DO_NOT_CONTACT.value:
            set_status(db, lead_id, Status.DO_NOT_CONTACT, "inbox", detail)
        return utcnow()
    if classification == "NOT_INTERESTED":
        if not lead.suppressed and lead.outreach_status != Status.REJECTED.value:
            set_status(db, lead_id, Status.REJECTED, "inbox", detail)
        return utcnow()
    if not lead.suppressed and lead.outreach_status not in STOP_STATUSES:
        with db.transaction():
            current = db.get(lead_id)
            current.outreach_status = Status.RESPONDED.value
            current.approved_draft_hash = ""
            db.save(current)
            db.audit(lead_id, "REPLY_RECEIVED", "inbox", classification)
    return ""


def ingest_message(db: Database, uid: str, raw: bytes) -> bool:
    message = BytesParser(policy=policy.default).parsebytes(raw)
    sender = parseaddr(str(message.get("From", "")))[1].strip().lower()
    if not sender or sender == OWN_EMAIL:
        return False
    message_id = str(message.get("Message-ID", "")).strip() or f"imap-uid:{uid}"
    if db.connection.execute(
        "SELECT 1 FROM inbox_events WHERE message_id=?", (message_id,)
    ).fetchone():
        return False
    subject = str(message.get("Subject", ""))
    body = message_text(message)
    refs = _references(message)
    lead_id = match_lead(db, sender, refs)
    classification, confidence, summary, suggested = classify_text(subject, body, sender)
    if lead_id is None:
        classification, confidence, summary, suggested = (
            "REVIEW_REQUIRED",
            min(confidence, 0.50),
            "Unmatched inbound message; identity review required",
            "",
        )
    try:
        received = parsedate_to_datetime(str(message.get("Date", ""))).isoformat()
    except (TypeError, ValueError):
        received = utcnow()
    applied_at = _apply_safe_state(db, lead_id, classification, summary) if lead_id else ""
    with db.transaction():
        db.connection.execute(
            """
            INSERT INTO inbox_events(
              lead_id,message_id,imap_uid,in_reply_to,sender,subject,received_at,
              classification,confidence,summary,suggested_status,applied_at,reviewed_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                lead_id,
                message_id,
                uid,
                refs[0] if refs else "",
                sender,
                subject[:500],
                received,
                classification,
                confidence,
                summary,
                suggested,
                applied_at,
                applied_at if classification in {"BOUNCE", "OPT_OUT", "NOT_INTERESTED"} else "",
            ),
        )
    return True


def sync(db: Database, limit: int = 100) -> int:
    if not 1 <= limit <= 1000:
        raise ValueError("Inbox sync limit must be 1..1000")
    client = connect(mailbox_password())
    try:
        status, _ = client.select("INBOX", readonly=True)
        if status != "OK":
            raise ValueError("Cannot select INBOX")
        status, data = client.uid("SEARCH", None, "ALL")
        if status != "OK" or not data or not isinstance(data[0], bytes):
            raise ValueError("Cannot search INBOX")
        uids = data[0].split()[-limit:]
        count = 0
        for uid in uids:
            status, fetched = client.uid("FETCH", uid.decode("ascii"), "(RFC822)")
            if status != "OK":
                continue
            raw = next(
                (item[1] for item in fetched or [] if isinstance(item, tuple) and isinstance(item[1], bytes)),
                None,
            )
            if raw is not None and ingest_message(db, uid.decode("ascii"), raw):
                count += 1
        return count
    finally:
        disconnect(client)


def apply_event(db: Database, event_id: int, actor: str) -> None:
    if not actor.strip():
        raise ValueError("Inbox review requires a named actor")
    row = db.connection.execute("SELECT * FROM inbox_events WHERE id=?", (event_id,)).fetchone()
    if row is None:
        raise ValueError("Inbox event not found")
    if row["reviewed_at"]:
        raise ValueError("Inbox event already reviewed")
    lead_id = row["lead_id"]
    suggested = row["suggested_status"]
    if lead_id and suggested == Status.INTERESTED.value:
        set_status(db, int(lead_id), Status.INTERESTED, actor, f"Inbox event {event_id}")
    elif lead_id and suggested == Status.RESPONDED.value:
        lead = db.get(int(lead_id))
        if not lead.suppressed and lead.outreach_status != Status.RESPONDED.value:
            set_status(db, int(lead_id), Status.RESPONDED, actor, f"Inbox event {event_id}")
    with db.transaction():
        db.connection.execute(
            "UPDATE inbox_events SET reviewed_at=?, applied_at=CASE WHEN applied_at='' THEN ? ELSE applied_at END WHERE id=?",
            (utcnow(), utcnow(), event_id),
        )


def dispatch(args: argparse.Namespace, db: Database, config: Config) -> int:
    action = args.inbox_command
    if action == "sync":
        count = sync(db, args.limit)
        print(f"Inbox: {count} neue Nachricht(en) verarbeitet. Kein SMTP-Versand.")
        return 0
    if action == "stats":
        rows = db.connection.execute(
            "SELECT classification,COUNT(*) n FROM inbox_events GROUP BY classification ORDER BY n DESC"
        )
        for row in rows:
            print(f"{row['classification']}: {row['n']}")
        return 0
    if action == "review":
        rows = db.connection.execute(
            "SELECT * FROM inbox_events WHERE reviewed_at='' ORDER BY id"
        )
        for row in rows:
            company = ""
            if row["lead_id"]:
                company = db.get(int(row["lead_id"])).company_name
            print(
                f"{row['id']} | {company or 'UNMATCHED'} | {row['classification']} "
                f"({row['confidence']:.2f}) | {row['sender']} | {row['subject']} | "
                f"{row['summary']} | suggested={row['suggested_status'] or '—'}"
            )
        return 0
    if action == "apply":
        apply_event(db, args.event_id, args.actor)
        print("Inbox event reviewed.")
        return 0
    raise ValueError("Unknown inbox action")
