import argparse
import json
from unittest.mock import patch

import pytest

from leadagent.batch_outreach import (
    dispatch,
    import_leads,
    mark_contacted,
    migrate_sent,
    stopped,
)
from leadagent.mail import FROM_EMAIL, SMTPSettings, build_message, smtp_transport
from leadagent.outreach import set_permission
from leadagent.templates import SECTORS, render


def arguments(action, sector="law_firm", path=None):
    return argparse.Namespace(outreach_command=action, sector=sector, input=path, actor="reviewer")


def fixture(path, rows):
    path.write_text(json.dumps({"leads": rows}))
    return path


@pytest.mark.parametrize("sector", SECTORS)
def test_templates_and_dry_run(sector, db, config, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    path = fixture(
        tmp_path / "leads.json",
        [{"company": "Synthetic & Partners", "email": "info@sector.example", "sector": sector}],
    )
    with patch("leadagent.mail.smtp_transport") as smtp, patch("builtins.input") as confirm:
        assert dispatch(arguments("preview", sector, str(path)), db, config) == 0
        smtp.assert_not_called()
        confirm.assert_not_called()
    lead = db.all()[0]
    assert "Synthetic & Partners" in lead.draft_text
    assert str(build_message(lead)["From"]).endswith("<" + FROM_EMAIL + ">")
    assert lead.contact_count == 0
    assert "DRY-RUN" in capsys.readouterr().out
    body = render(sector, "Synthetic")[1]
    assert {
        "law_firm": "1.100 €",
        "medical_practice": "Patientendaten",
        "tax_advisor": "Mandantendaten",
        "it_service_provider": "White-Label",
    }[sector] in body


def test_identity_manual_and_cross_sector(db, config, tmp_path):
    rows = [
        {
            "company": "Synthetic GmbH",
            "city": "Berlin",
            "email": "info@alpha.example",
            "website": "https://www.alpha.example",
            "sector": "law_firm",
        },
        {
            "company": "Synthetic",
            "city": "Berlin",
            "email": "office@beta.example",
            "website": "beta.example",
            "sector": "tax_advisor",
        },
        {"company": "Other", "email": "contact@alpha.example", "sector": "medical_practice"},
    ]
    imported = import_leads(db, fixture(tmp_path / "leads.json", rows), "law_firm", config)
    assert len({lead.id for lead in imported}) == 1
    mark_contacted(db, "office@beta.example", config, "human", "Previous manual contact")
    assert stopped(db.get(imported[0].id))
    assert len(db.all()) == 1


def test_manual_flag_and_migration(db, config, tmp_path):
    path = fixture(
        tmp_path / "karlsruhe_kanzleien_outreach.json",
        [{"company": "Synthetic", "email": "info@history.example"}],
    )
    history = tmp_path / ".sent_outreach.json"
    original = '{"sent": ["INFO@history.example"]}'
    history.write_text(original)
    migrate_sent(db, [history], config)
    migrate_sent(db, [history], config)
    assert db.all()[0].company_name == "Synthetic"
    assert db.all()[0].contact_count == 1
    assert len(db.history(db.all()[0].id)) == 1
    assert history.read_text() == original
    imported = import_leads(
        db,
        fixture(
            path,
            [{"company": "Manual", "email": "info@manual.example", "contacted_manually": True}],
        ),
        "law_firm",
        config,
    )
    assert stopped(imported[0])
    history.write_text('{"sent": "bad"}')
    with pytest.raises(ValueError):
        migrate_sent(db, [history], config)


@pytest.mark.parametrize("answer", ["JA", "ja", "NEIN"])
def test_batch_confirmation_and_persistence(answer, db, config, qualified, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DSC_SMTP_PASSWORD", "synthetic")
    config.mail.automatic_sending_enabled = True
    qualified.segment = "law_firm"
    db.save(qualified)
    set_permission(db, qualified.id, "CONSENTED", "Synthetic written request", "reviewer")
    # Mock only the network boundary; the shared Mailer exercises all delivery gates.
    from leadagent.mail import Mailer

    sent = []

    def factory(database, settings):
        return Mailer(database, settings, sent.append)

    with (
        patch("builtins.input", return_value=answer) as confirm,
        patch("leadagent.batch_outreach.Mailer", side_effect=factory),
    ):
        assert dispatch(arguments("send"), db, config) == 0
        confirm.assert_called_once_with("Wirklich alle 1 Mails versenden? Tippe JA: ")
    assert len(sent) == (1 if answer == "JA" else 0)
    if answer == "JA":
        assert db.history(qualified.id)[0]["state"] == "ACCEPTED"
        assert stopped(db.get(qualified.id))
        assert dispatch(arguments("preview"), db, config) == 0


def test_sender_envelope_and_kit_rejection(monkeypatch):
    settings = SMTPSettings(
        "mxe9aa.netcup.net", 465, FROM_EMAIL, "synthetic", "ssl", FROM_EMAIL, "Hasib"
    )
    from email.message import EmailMessage

    message = EmailMessage()
    message["To"] = "info@recipient.example"
    with patch("leadagent.mail.smtplib.SMTP_SSL") as client:
        client.return_value.sendmail.return_value = {}
        smtp_transport(settings, message)
        client.return_value.sendmail.assert_called_once()
        call = client.return_value.sendmail.call_args
        assert call.args[:2] == (FROM_EMAIL, ["info@recipient.example"])
    monkeypatch.setenv("DSC_SMTP_USERNAME", "kit@kit.example")
    monkeypatch.setenv("DSC_SMTP_PASSWORD", "synthetic")
    with pytest.raises(ValueError, match="SMTP user"):
        SMTPSettings.from_environment()


def test_failed_batch_is_not_accepted_or_retried(db, config, qualified, tmp_path, monkeypatch):
    from leadagent.mail import Mailer

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DSC_SMTP_PASSWORD", "synthetic")
    config.mail.automatic_sending_enabled = True
    qualified.segment = "law_firm"
    db.save(qualified)
    set_permission(db, qualified.id, "CONSENTED", "Synthetic request", "reviewer")

    def failing_transport(message):
        raise OSError("Synthetic network failure")

    def factory(database, settings):
        return Mailer(database, settings, failing_transport)

    with (
        patch("builtins.input", return_value="JA") as confirm,
        patch("leadagent.batch_outreach.Mailer", side_effect=factory),
    ):
        assert dispatch(arguments("send"), db, config) == 1
        assert db.history(qualified.id)[0]["state"] == "FAILED_OR_UNCERTAIN"
        assert not db.get(qualified.id).initial_delivery_confirmed
        assert dispatch(arguments("send"), db, config) == 0
        confirm.assert_called_once()
