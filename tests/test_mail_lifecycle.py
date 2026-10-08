from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from unittest.mock import MagicMock, patch

import pytest

from leadagent.database import Database
from leadagent.mail import Mailer, SMTPSettings, blockers, build_message, smtp_transport
from leadagent.models import Status
from leadagent.outreach import approve, followup_due, prepare_draft, set_permission, set_status


def eligible(db, lead, config):
    config.mail.automatic_sending_enabled = True
    set_permission(
        db, lead.id, "CONSENTED", "Synthetic inbound written request, ticket SYN-1", "reviewer"
    )
    approve(db, lead.id, "reviewer", config)
    return db.get(lead.id)


def make_due(db, lead_id, days=11):
    lead = db.get(lead_id)
    now = datetime.now(UTC)
    lead.first_contact_at = (now - timedelta(days=days)).isoformat()
    lead.next_contact_allowed_at = (now - timedelta(days=days - 10)).isoformat()
    db.save(lead)
    return lead


def test_default_dry_run(db, config, qualified):
    sent = []
    result = Mailer(db, config, sent.append).send(qualified.id)
    assert result.state == "DRY_RUN" and sent == []
    assert db.get(qualified.id).contact_count == 0
    assert result.blockers
    assert db.history(qualified.id)[0]["mode"] == "DRY_RUN"


def test_multipart_and_escaping(qualified):
    message = build_message(qualified)
    assert message["To"] == qualified.public_email
    assert message.is_multipart()
    assert message.get_body(preferencelist=("plain",)).get_content().strip() == qualified.draft_text
    assert "<html" in message.get_body(preferencelist=("html",)).get_content()
    lead = qualified
    lead.evidence[3].excerpt = '<script>alert("x")</script> Microsoft 365'
    lead.evidence = [lead.evidence[3]]
    prepare_draft(lead)
    assert "<script>" not in lead.draft_html and "&lt;script&gt;" in lead.draft_html
    assert lead.draft_evidence_urls == [lead.evidence[0].source_url]


def test_live_policy_gate(db, config, qualified):
    approve(db, qualified.id, "reviewer", config)
    with pytest.raises(ValueError, match="blocked"):
        Mailer(db, config, lambda _: None).send(qualified.id, live=True)
    assert db.get(qualified.id).contact_count == 0


def test_initial_at_most_once(db, config, qualified):
    eligible(db, qualified, config)
    sent = []
    result = Mailer(db, config, sent.append).send(qualified.id, live=True)
    assert result.state == "ACCEPTED" and len(sent) == 1
    assert db.get(qualified.id).contact_count == 1
    with pytest.raises(ValueError, match="already"):
        Mailer(db, config, sent.append).send(qualified.id, live=True)
    assert len(sent) == 1
    history = db.history(qualified.id)
    assert history[0]["permission_status"] == "CONSENTED"
    assert "SYN-1" in history[0]["permission_basis"]


def test_concurrent_send_single_delivery(db, config, qualified):
    eligible(db, qualified, config)
    sent = []

    def send(_):
        local = Database(config.database)
        try:
            return Mailer(local, config, sent.append).send(qualified.id, live=True).state
        except ValueError:
            return "BLOCKED"
        finally:
            local.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(send, range(2))) == ["ACCEPTED", "BLOCKED"]
    assert len(sent) == 1


@pytest.mark.parametrize(
    "status",
    [Status.REJECTED, Status.DO_NOT_CONTACT, Status.CUSTOMER, Status.RESPONDED, Status.INTERESTED],
)
def test_suppression_and_response(db, config, qualified, status):
    eligible(db, qualified, config)
    set_status(db, qualified.id, status, "reviewer")
    for live in (False, True):
        with pytest.raises(ValueError, match="Suppressed"):
            Mailer(db, config, lambda _: None).send(qualified.id, live)
    assert not followup_due(db.get(qualified.id), config)


def test_permanent_opt_out(db, config, qualified):
    set_permission(db, qualified.id, "PROHIBITED", "Opt-out reply", "reviewer")
    with pytest.raises(ValueError, match="permanent"):
        set_permission(db, qualified.id, "CONSENTED", "attempted reset", "reviewer")
    with pytest.raises(ValueError):
        approve(db, qualified.id, "reviewer", config)


def test_followup_delay_and_maximum(db, config, qualified):
    eligible(db, qualified, config)
    sent = []
    Mailer(db, config, sent.append).send(qualified.id, live=True)
    lead = db.get(qualified.id)
    assert not followup_due(lead, config)
    boundary = datetime.fromisoformat(lead.next_contact_allowed_at)
    assert not followup_due(lead, config, boundary - timedelta(seconds=1))
    assert followup_due(lead, config, boundary)
    lead = make_due(db, qualified.id)
    assert followup_due(lead, config)
    prepare_draft(lead, "followup")
    db.save(lead)
    approve(db, lead.id, "reviewer", config)
    # Advance rate-limiter reservation time without waiting.
    db.connection.execute(
        "UPDATE deliveries SET created_at=?",
        ((datetime.now(UTC) - timedelta(days=11)).isoformat(),),
    )
    assert Mailer(db, config, sent.append).send(lead.id, live=True).state == "ACCEPTED"
    lead = db.get(lead.id)
    assert lead.followup_count == 1 and lead.contact_count == 2
    assert lead.next_contact_allowed_at == ""
    assert not followup_due(lead, config, datetime.now(UTC) + timedelta(days=100))
    with pytest.raises(ValueError):
        Mailer(db, config, sent.append).send(lead.id, live=True)
    assert len(sent) == 2


def test_followups_disabled(db, config, qualified):
    eligible(db, qualified, config)
    Mailer(db, config, lambda _: None).send(qualified.id, live=True)
    lead = make_due(db, qualified.id)
    config.maximum_followups = 0
    assert not followup_due(lead, config)


def test_failed_smtp_consumes_slot_and_stops_followup(db, config, qualified):
    eligible(db, qualified, config)

    def broken(_):
        raise OSError("Sensitive mail server response must not be logged")

    result = Mailer(db, config, broken).send(qualified.id, live=True)
    assert result.state == "FAILED_OR_UNCERTAIN"
    assert db.get(qualified.id).contact_count == 1
    assert db.get(qualified.id).next_contact_allowed_at == ""
    assert not followup_due(db.get(qualified.id), config)
    with pytest.raises(ValueError):
        Mailer(db, config, broken).send(qualified.id, live=True)
    assert "Sensitive" not in str(db.history(qualified.id))


def test_permission_draft_and_approval_invalidation(db, config, qualified):
    lead = eligible(db, qualified, config)
    lead.draft_text += "Changed"
    db.save(lead)
    assert any("human approval" in b for b in blockers(lead, config))
    lead = eligible(db, qualified, config)
    set_permission(db, lead.id, "REQUESTED_INFORMATION", "New request", "reviewer")
    assert not db.get(lead.id).approved_draft_hash


def test_stale_approval_and_evidence(db, config, qualified):
    lead = eligible(db, qualified, config)
    lead.approved_at = (datetime.now(UTC) - timedelta(days=8)).isoformat()
    lead.last_researched_at = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    assert any("expired" in b for b in blockers(lead, config))
    assert any("stale" in b for b in blockers(lead, config))


def test_empty_actor_and_basis(db, config, qualified):
    with pytest.raises(ValueError):
        approve(db, qualified.id, "", config)
    with pytest.raises(ValueError):
        set_permission(db, qualified.id, "CONSENTED", "", "reviewer")


def test_smtp_tls(monkeypatch):
    settings = SMTPSettings(
        "smtp.synthetic.example",
        587,
        "user",
        "test-placeholder",
        "starttls",
        "kontakt@digitalskills-campus.de",
        "Digital Skills Campus",
    )
    client = MagicMock()
    with patch("leadagent.mail.smtplib.SMTP", return_value=client):
        client.sendmail.return_value = {}
        smtp_transport(settings, EmailMessage())
    client.starttls.assert_called_once()
    client.login.assert_called_once_with("user", "test-placeholder")
    client.sendmail.assert_called_once()
    client.close.assert_called_once()


def test_plaintext_smtp_refused(monkeypatch):
    monkeypatch.setenv("DSC_SMTP_HOST", "smtp.synthetic.example")
    monkeypatch.setenv("DSC_SMTP_SECURITY", "none")
    with pytest.raises(ValueError, match="TLS"):
        SMTPSettings.from_environment()


def test_crash_reservation_never_followed_up(db, config, qualified):
    from leadagent.outreach import record_contact

    record_contact(qualified, config, (datetime.now(UTC) - timedelta(days=11)).isoformat())
    db.save(qualified)
    assert not qualified.initial_delivery_confirmed
    assert not followup_due(qualified, config)


def test_personalization_staleness_independent_of_research(db, config, qualified):
    lead = eligible(db, qualified, config)
    lead.draft_evidence[0].retrieved_at = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    assert any("Personalization" in b for b in blockers(lead, config))


def test_rate_limit_between_companies(db, config, qualified):
    from conftest import synthetic_lead

    from leadagent.scoring import score

    eligible(db, qualified, config)
    Mailer(db, config, lambda _: None).send(qualified.id, live=True)
    other, _ = db.upsert(prepare_draft(score(synthetic_lead(2), config)))
    eligible(db, other, config)
    with pytest.raises(ValueError, match="rate limit"):
        Mailer(db, config, lambda _: None).send(other.id, live=True)
    assert db.get(other.id).contact_count == 0


def test_public_business_outreach_permission_state(db, config, qualified):
    set_permission(
        db,
        qualified.id,
        "PUBLIC_BUSINESS_OUTREACH",
        "Owner-approved public business contact; source: https://example.invalid/contact",
        "OWNER",
    )
    lead = db.get(qualified.id)
    assert lead.email_permission_status == "PUBLIC_BUSINESS_OUTREACH"
    assert "PUBLIC_BUSINESS_OUTREACH" in config.mail.allowed_permission_states
