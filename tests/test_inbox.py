from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime

from leadagent.inbox import classify_text, ingest_message, match_lead
from leadagent.models import Status


def test_classification_rules():
    assert classify_text("", "Bitte keine weiteren E-Mails.", "info@example.de")[0] == "OPT_OUT"
    assert classify_text("", "Wir haben kein Interesse.", "info@example.de")[0] == "NOT_INTERESTED"
    assert (
        classify_text("", "Können wir nächste Woche einen Termin machen?", "info@example.de")[0]
        == "MEETING_REQUEST"
    )
    assert (
        classify_text(
            "",
            "Bitte senden Sie weitere Informationen zum Umfang.",
            "info@example.de",
        )[0]
        == "REQUESTED_INFORMATION"
    )
    assert (
        classify_text("Delivery Status Notification", "", "mailer-daemon@example.de")[0]
        == "BOUNCE"
    )


def test_thread_matching_and_idempotent_ingest(db, qualified):
    db.save(qualified)
    at = datetime.now(UTC).isoformat()
    db.connection.execute(
        "INSERT INTO deliveries(lead_id,kind,mode,state,created_at,updated_at,draft_hash,"
        "permission_status,permission_basis,approved_by,recipient,message_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            qualified.id,
            "initial",
            "LIVE",
            "ACCEPTED",
            at,
            at,
            "h",
            "CONSENTED",
            "synthetic",
            "owner",
            qualified.public_email,
            "<sent@example>",
        ),
    )
    assert match_lead(db, "other@example.net", ["<sent@example>"]) == qualified.id
    msg = EmailMessage()
    msg["From"] = qualified.public_email
    msg["To"] = "kontakt@digitalskills-campus.de"
    msg["Subject"] = "Re: Angebot"
    msg["Message-ID"] = "<reply@example>"
    msg["In-Reply-To"] = "<sent@example>"
    msg["Date"] = format_datetime(datetime.now(UTC))
    msg.set_content("Bitte senden Sie weitere Informationen zum Umfang.")
    raw = msg.as_bytes()
    assert ingest_message(db, "1", raw)
    assert not ingest_message(db, "1", raw)
    event = db.connection.execute("SELECT * FROM inbox_events").fetchone()
    assert event["classification"] == "REQUESTED_INFORMATION"
    assert db.get(qualified.id).outreach_status == Status.RESPONDED.value
