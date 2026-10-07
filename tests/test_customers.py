from argparse import Namespace
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from leadagent import customers
from leadagent.cli import dispatch, parser
from leadagent.database import Database
from leadagent.mail import Mailer
from leadagent.models import Permission, Status, instant
from leadagent.outreach import draft_hash, set_status
from leadagent.sent_mail import repair_sent

START = datetime(2026, 1, 1, tzinfo=UTC)


def customer(db, config, qualified):
    with patch("leadagent.outreach.utcnow", return_value=START.isoformat()):
        set_status(db, qualified.id, Status.CUSTOMER, "OWNER", config=config)
    config.mail.automatic_sending_enabled = True
    return db.get(qualified.id)


def events(db, lead_id):
    return [
        row[0]
        for row in db.connection.execute(
            "SELECT action FROM audit WHERE lead_id=? ORDER BY id", (lead_id,)
        )
    ]


@pytest.mark.parametrize("interval", [30, 90, 180])
def test_exact_due_boundary_and_initialization(db, config, qualified, interval):
    config.customer_checkin_interval_days = interval
    config.validate()
    lead = customer(db, config, qualified)
    at = START + timedelta(days=interval)
    assert lead.customer_since == START.isoformat()
    assert lead.last_customer_checkin_at is None
    assert instant(lead.next_customer_checkin_at) == at
    assert not customers.due(lead, at - timedelta(microseconds=1))
    assert customers.due(lead, at)
    assert "CUSTOMER_CREATED" in events(db, lead.id)
    with pytest.raises(ValueError):
        set_status(db, lead.id, Status.CUSTOMER, "OWNER", config=config)
    assert db.get(lead.id).next_customer_checkin_at == lead.next_customer_checkin_at


@pytest.mark.parametrize("interval", [30, 90, 180])
def test_repeated_cycles_and_history(db, config, qualified, interval):
    config.customer_checkin_interval_days = interval
    lead = customer(db, config, qualified)
    smtp = MagicMock()
    copy = MagicMock(return_value="Gesendet")
    mailer = Mailer(db, config, smtp, copy)
    for cycle in range(1, 4):
        at = START + timedelta(days=interval * cycle)
        result = mailer.send_customer(lead.id, "OWNER", now=at)
        assert result.state == "ACCEPTED"
        current = db.get(lead.id)
        assert instant(current.last_customer_checkin_at) == at
        assert instant(current.next_customer_checkin_at) == at + timedelta(days=interval)
        assert current.contact_count == qualified.contact_count
        assert current.draft_text == qualified.draft_text
        with pytest.raises(ValueError, match="not due"):
            mailer.send_customer(lead.id, "OWNER", now=at)
    assert smtp.call_count == copy.call_count == 3
    assert len(db.history(lead.id)) == 3
    assert events(db, lead.id).count("CUSTOMER_CHECKIN_SENT") == 3
    assert events(db, lead.id).count("CUSTOMER_CHECKIN_DUE") == 3
    assert all(row["permission_status"] == "" for row in db.history(lead.id))


@pytest.mark.parametrize("permission", list(Permission))
def test_no_prospect_permission_gate(db, config, qualified, permission):
    lead = customer(db, config, qualified)
    lead.email_permission_status = permission.value
    lead.email_permission_basis = ""
    lead.approved_draft_hash = ""
    lead.final_score = 0
    lead.last_researched_at = ""
    db.save(lead)
    assert not customers.blockers(db, lead, START + timedelta(days=90))
    assert (
        Mailer(db, config, lambda m: None, lambda r, i: "Sent")
        .send_customer(lead.id, "OWNER", now=START + timedelta(days=90))
        .state
        == "ACCEPTED"
    )


def test_smtp_failure_keeps_due_but_consumes_period(db, config, qualified):
    lead = customer(db, config, qualified)
    smtp = MagicMock(side_effect=OSError("synthetic"))
    copy = MagicMock()
    mailer = Mailer(db, config, smtp, copy)
    result = mailer.send_customer(lead.id, "OWNER", now=START + timedelta(days=90))
    assert result.state == "FAILED_OR_UNCERTAIN"
    current = db.get(lead.id)
    assert current.next_customer_checkin_at == lead.next_customer_checkin_at
    assert current.last_customer_checkin_at is None
    assert "CUSTOMER_CHECKIN_FAILED" in events(db, lead.id)
    copy.assert_not_called()
    with pytest.raises(ValueError, match="already reserved"):
        mailer.send_customer(lead.id, "OWNER", now=START + timedelta(days=180))
    reopened = Database(config.database)
    assert "reserved" in " ".join(customers.blockers(reopened, reopened.get(lead.id)))
    reopened.close()
    smtp.assert_called_once()


def test_imap_failure_is_sent_and_repairable(db, config, qualified):
    lead = customer(db, config, qualified)
    at = START + timedelta(days=90)

    def copy(raw, message_id):
        independent = Database(config.database)
        assert independent.history(lead.id)[0]["state"] == "ACCEPTED"
        assert instant(independent.get(lead.id).next_customer_checkin_at) == at + timedelta(days=90)
        independent.close()
        raise OSError("synthetic")

    smtp = MagicMock()
    result = Mailer(db, config, smtp, copy).send_customer(lead.id, "OWNER", now=at)
    assert result.state == "ACCEPTED"
    assert result.sent_copy_status == "FAILED"
    assert repair_sent(db, lambda raw, mid: "Sent")[0]["sent_copy_status"] == "SAVED"
    assert repair_sent(db, lambda raw, mid: "Sent") == []
    smtp.assert_called_once()


@pytest.mark.parametrize(
    "field,value",
    [
        ("customer_opt_out", True),
        ("do_not_contact", True),
        ("outreach_status", "DO_NOT_CONTACT"),
        ("outreach_status", "UNSUBSCRIBED"),
        ("email_disabled", True),
        ("public_email", "invalid"),
    ],
)
def test_explicit_suppression(db, config, qualified, field, value):
    lead = customer(db, config, qualified)
    setattr(lead, field, value)
    db.save(lead)
    smtp = MagicMock()
    with pytest.raises(ValueError, match="blocked"):
        Mailer(db, config, smtp).send_customer(lead.id, "OWNER", now=START + timedelta(days=90))
    smtp.assert_not_called()


def test_customer_opt_out_command_and_show(db, config, qualified, capsys):
    lead = customer(db, config, qualified)
    dispatch(parser().parse_args(["unsubscribe", str(lead.id), "--actor", "OWNER"]), db, config)
    current = db.get(lead.id)
    assert current.customer_opt_out and current.do_not_contact and current.customer
    assert "CUSTOMER_OPT_OUT" in events(db, lead.id)
    dispatch(parser().parse_args(["show", str(lead.id)]), db, config)
    output = capsys.readouterr().out
    for key in (
        "customer_since",
        "last_customer_checkin_at",
        "next_customer_checkin_at",
        "customer_opt_out",
        "customer_contact_history",
        "CUSTOMER_CREATED",
        "CUSTOMER_OPT_OUT",
    ):
        assert key in output


def test_prospect_cannot_send_customer_and_customer_cannot_send_acquisition(db, config, qualified):
    smtp = MagicMock()
    config.mail.automatic_sending_enabled = True
    mailer = Mailer(db, config, smtp)
    with pytest.raises(ValueError, match="not a CUSTOMER"):
        mailer.send_customer(qualified.id, "OWNER")
    customer(db, config, qualified)
    with pytest.raises(ValueError, match="Suppressed"):
        mailer.send(qualified.id, live=True)
    smtp.assert_not_called()


def test_crash_reservation_survives(db, config, qualified):
    lead = customer(db, config, qualified)
    smtp = MagicMock(side_effect=KeyboardInterrupt)
    with pytest.raises(KeyboardInterrupt):
        Mailer(db, config, smtp).send_customer(lead.id, "OWNER", now=START + timedelta(days=90))
    assert db.history(lead.id)[0]["state"] == "RESERVED"
    assert db.get(lead.id).next_customer_checkin_at == lead.next_customer_checkin_at
    with pytest.raises(ValueError, match="reserved"):
        Mailer(db, config, smtp).send_customer(lead.id, "OWNER", now=START + timedelta(days=180))
    smtp.assert_called_once()


def test_due_scheduler_and_preview_never_send(db, config, qualified, capsys):
    lead = customer(db, config, qualified)
    with patch("leadagent.mail.smtp_transport") as smtp:
        for action in ("list", "due", "due", "preview"):
            assert dispatch(parser().parse_args(["customers", action]), db, config) == 0
        smtp.assert_not_called()
    output = capsys.readouterr().out
    assert "Customers: 1\nDue now: 1" in output
    assert "Liebes Team von" in output
    assert not db.history(lead.id)
    assert events(db, lead.id).count("CUSTOMER_CHECKIN_DUE") == 1


def test_batch_one_confirmation_and_suppression(db, config, qualified, monkeypatch, capsys):
    lead = customer(db, config, qualified)
    from conftest import synthetic_lead

    other, _ = db.upsert(synthetic_lead(2))
    other = customer(db, config, other)
    other.customer_opt_out = True
    db.save(other)
    prompts = []
    monkeypatch.setenv("DSC_SMTP_PASSWORD", "synthetic")
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or "JA")
    with patch(
        "leadagent.mail.Mailer.send_customer",
        return_value=Namespace(state="ACCEPTED", sent_copy_status="SAVED"),
    ) as send:
        assert (
            dispatch(parser().parse_args(["customers", "send", "--actor", "OWNER"]), db, config)
            == 0
        )
    assert prompts == ["Wirklich alle 1 Kunden-E-Mails versenden? Tippe JA: "]
    assert send.call_count == 1
    assert send.call_args.args == (lead.id, "OWNER")
    assert "Customers due: 2\nSuppressed: 1\nWill send: 1" in capsys.readouterr().out


def test_changed_displayed_message_requires_confirmation(db, config, qualified):
    lead = customer(db, config, qualified)
    expected = (lead.next_customer_checkin_at, draft_hash(customers.message(lead, config)))
    lead.public_email = "changed@synthetic-1.example"
    db.save(lead)
    with pytest.raises(ValueError, match="changed"):
        Mailer(db, config, lambda m: None).send_customer(
            lead.id, "OWNER", expected, START + timedelta(days=90)
        )
    assert not db.history(lead.id)


@pytest.mark.parametrize("message_type", list(customers.CUSTOMER_TEMPLATES))
def test_central_message_types(config, qualified, message_type):
    config.customer_message_type = message_type
    config.validate()
    rendered = customers.message(qualified, config)
    assert rendered.draft_kind == "customer_checkin"
    assert rendered.draft_subject and rendered.draft_html
    assert qualified.company_name in rendered.draft_text
    assert qualified.draft_kind == "initial"


@pytest.mark.parametrize("interval", [0, -1, 90.5, True])
def test_invalid_interval(config, interval):
    config.customer_checkin_interval_days = interval
    with pytest.raises(ValueError, match="customer_checkin_interval_days"):
        config.validate()


def test_batch_declined_and_disabled(db, config, qualified, monkeypatch):
    customer(db, config, qualified)
    args = parser().parse_args(["customers", "send", "--actor", "OWNER"])
    with patch("leadagent.mail.Mailer.send_customer") as send:
        monkeypatch.setattr("builtins.input", lambda prompt: "NEIN")
        assert dispatch(args, db, config) == 0
        config.mail.automatic_sending_enabled = False
        with pytest.raises(ValueError, match="disabled"):
            dispatch(args, db, config)
        send.assert_not_called()
    assert not db.history(qualified.id)


def test_two_concurrent_customer_sends_only_once(db, config, qualified):
    from concurrent.futures import ThreadPoolExecutor

    lead = customer(db, config, qualified)
    smtp = MagicMock()

    def send(_):
        local = Database(config.database)
        try:
            try:
                return (
                    Mailer(local, config, smtp, lambda raw, mid: "Sent")
                    .send_customer(lead.id, "OWNER", now=START + timedelta(days=90))
                    .state
                )
            except ValueError:
                return "BLOCKED"
        finally:
            local.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(send, range(2))) == ["ACCEPTED", "BLOCKED"]
    smtp.assert_called_once()
    assert len(db.history(lead.id)) == 1


def test_v3_migration_preserves_delivery_identity_and_customer_history(tmp_path):
    import json
    import sqlite3

    from leadagent.database import SCHEMA
    from leadagent.models import Lead

    path = str(tmp_path / "migration.sqlite3")
    old = sqlite3.connect(path)
    old.executescript(SCHEMA)
    for definition in (
        "rfc822 BLOB NOT NULL DEFAULT X''",
        "sent_copy_status TEXT NOT NULL DEFAULT 'NOT_REQUIRED'",
        "sent_folder TEXT NOT NULL DEFAULT ''",
        "sent_copied_at TEXT NOT NULL DEFAULT ''",
        "sent_copy_error TEXT NOT NULL DEFAULT ''",
    ):
        old.execute(f"ALTER TABLE deliveries ADD COLUMN {definition}")
    lead = Lead(
        "Migration GmbH",
        "https://migration.example",
        id=1,
        customer=True,
        outreach_status="CUSTOMER",
        do_not_contact=True,
        email_permission_status="PROHIBITED",
        contact_count=1,
    )
    old.execute(
        "INSERT INTO leads VALUES (1, 'migration.example', 'migration', ?, '', 1)",
        (json.dumps(lead.to_dict()),),
    )
    old.execute(
        "INSERT INTO domain_aliases VALUES ('migration.example', 1, 'https://migration.example', ?)",
        (START.isoformat(),),
    )
    old.execute(
        "INSERT INTO audit VALUES (1,1,'CUSTOMER',?,'OWNER','existing customer')",
        (START.isoformat(),),
    )
    old.execute(
        "INSERT INTO deliveries(id,lead_id,kind,mode,state,created_at,updated_at,draft_hash,permission_status,permission_basis,approved_by,recipient,message_id,rfc822,sent_copy_status) VALUES (7,1,'initial','LIVE','ACCEPTED',?,?,'hash','CONSENTED','synthetic request','OWNER','team@migration.example','<synthetic@migration.example>',?,'FAILED')",
        (START.isoformat(), START.isoformat(), b"synthetic wire bytes"),
    )
    old.execute("PRAGMA user_version=3")
    old.commit()
    old.close()
    migrated = Database(path)
    assert migrated.connection.execute("PRAGMA user_version").fetchone()[0] == 4
    current = migrated.get(1)
    assert current.do_not_contact and current.contact_count == 1
    assert current.customer_since == START.isoformat()
    assert instant(current.next_customer_checkin_at) == START + timedelta(days=90)
    row = migrated.connection.execute("SELECT * FROM deliveries WHERE id=7").fetchone()
    assert row["rfc822"] == b"synthetic wire bytes"
    assert row["sent_copy_status"] == "FAILED"
    assert row["permission_basis"] == "synthetic request"
    assert migrated.connection.execute("SELECT lead_id FROM domain_aliases").fetchone()[0] == 1
    migrated.close()
    reopened = Database(path)
    assert events(reopened, 1).count("CUSTOMER_CREATED") == 1
    assert reopened.get(1).next_customer_checkin_at == current.next_customer_checkin_at
    reopened.close()
