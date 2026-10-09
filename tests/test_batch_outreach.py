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
    suppress_email,
)
from leadagent.mail import FROM_EMAIL, SMTPSettings, build_message, smtp_transport
from leadagent.templates import SECTORS, render


def arguments(
    action,
    sector="law_firm",
    path=None,
    region="",
    max_count=None,
    review_input=False,
    reviewed_by="",
):
    return argparse.Namespace(
        outreach_command=action,
        sector=sector,
        input=path,
        actor="reviewer",
        region=region,
        max_count=max_count,
        review_input=review_input,
        reviewed_by=reviewed_by,
    )


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
    assert "€" not in body
    assert {
        "law_firm": "individuell abgestimmten",
        "medical_practice": "Patientendaten",
        "tax_advisor": "Mandantendaten",
        "it_service_provider": "projektbezogen",
        "manufacturing_industry": "Fertigungs- und Produktionsumgebungen",
        "electrical_engineering": "technisch geprägten Betrieben",
        "logistics": "Transport und Logistik",
        "property_management": "Immobilienverwaltung",
        "technical_trade": "technischem Handel",
        "fitness_studio": "digitaler Mitgliederverwaltung",
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
    path = fixture(tmp_path / "leads.json", rows)
    law = import_leads(db, path, "law_firm", config)
    tax = import_leads(db, path, "tax_advisor", config)
    medical = import_leads(db, path, "medical_practice", config)
    assert len({law[0].id, tax[0].id, medical[0].id}) == 1
    mark_contacted(db, "office@beta.example", config, "human", "Previous manual contact")
    assert stopped(db.get(law[0].id))
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
    # No consent flag is pre-populated. The single JA confirmation records an explicit
    # PUBLIC_BUSINESS_OUTREACH audit basis instead of pretending consent.
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
        history = db.history(qualified.id)
        assert history[0]["state"] == "ACCEPTED"
        assert history[0]["permission_status"] == "PUBLIC_BUSINESS_OUTREACH"
        assert "public business contact" in history[0]["permission_basis"]
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


def test_batch_region_and_max_filter(db, config, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rows = [
        {
            "company": "Stuttgart One",
            "email": "info@stuttgart-one.example",
            "website": "https://stuttgart-one.example",
            "sector": "law_firm",
        },
        {
            "company": "Stuttgart Two",
            "email": "info@stuttgart-two.example",
            "website": "https://stuttgart-two.example",
            "sector": "law_firm",
        },
        {
            "company": "Karlsruhe One",
            "email": "info@karlsruhe-one.example",
            "website": "https://karlsruhe-one.example",
            "sector": "law_firm",
        },
    ]
    imported = import_leads(
        db,
        fixture(tmp_path / "regional.json", rows),
        "law_firm",
        config,
    )
    for lead in imported:
        lead.segment = "law_firm"
        lead.campaign_region = "Stuttgart" if "Stuttgart" in lead.company_name else "Karlsruhe"
        lead.qualified_at = "2026-10-08T00:00:00+00:00"
        lead.final_score = config.minimum_score
        db.save(lead)

    assert dispatch(arguments("preview", region="Stuttgart", max_count=1), db, config) == 0
    output = capsys.readouterr().out
    assert "Region: Stuttgart" in output
    assert "Stuttgart One" in output
    assert "Karlsruhe One" not in output
    assert "Pending outreach: 1" in output


def test_manual_reviewed_campaign_json_becomes_sendable(db, config, tmp_path):
    path = tmp_path / "manual.json"
    path.write_text(
        json.dumps(
            {
                "campaign": {
                    "sector": "law_firm",
                    "region": "Stuttgart",
                    "reviewed_by": "OWNER",
                    "manual_reviewed": True,
                },
                "leads": [
                    {
                        "company": "Synthetic Manual Kanzlei",
                        "email": "info@manual.example",
                        "website": "https://manual.example/",
                        "city": "Stuttgart",
                        "source_url": "https://manual.example/impressum",
                    }
                ],
            }
        )
    )
    imported = import_leads(db, path, "law_firm", config)
    lead = imported[0]
    assert lead.country == "Germany"
    assert lead.campaign_region == "Stuttgart"
    assert lead.qualified_at
    assert lead.final_score == config.minimum_score
    assert lead.recommended_dsc_service == "Windows Security Assessment"
    assert any(e.kind == "manual_review" for e in lead.evidence)
    assert lead.draft_evidence
    assert lead.draft_evidence_urls == ["https://manual.example/impressum"]


def test_manual_reviewed_campaign_can_take_region_from_cli(db, config, tmp_path):
    path = tmp_path / "manual.json"
    path.write_text(
        json.dumps(
            {
                "campaign": {
                    "sector": "law_firm",
                    "reviewed_by": "OWNER",
                    "manual_reviewed": True,
                },
                "leads": [
                    {
                        "company": "Synthetic CLI Region",
                        "email": "info@cli-region.example",
                        "website": "https://cli-region.example/",
                        "city": "Esslingen",
                        "source_url": "https://cli-region.example/kontakt",
                    }
                ],
            }
        )
    )
    imported = import_leads(
        db,
        path,
        "law_firm",
        config,
        default_region="Stuttgart",
    )
    assert imported[0].campaign_region == "Stuttgart"


def test_manual_review_flag_requires_explicit_public_source(db, config, tmp_path):
    path = tmp_path / "bad-manual.json"
    path.write_text(
        json.dumps(
            {
                "campaign": {
                    "sector": "law_firm",
                    "region": "Stuttgart",
                    "manual_reviewed": True,
                },
                "leads": [
                    {
                        "company": "Missing Website",
                        "email": "info@missing.example",
                        "city": "Stuttgart",
                    }
                ],
            }
        )
    )
    with pytest.raises(ValueError, match="explicit public website"):
        import_leads(db, path, "law_firm", config)


def test_mixed_sector_reviewed_input_uses_custom_draft(db, config, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "mittelstand.json"
    path.write_text(
        json.dumps(
            {
                "leads": [
                    {
                        "company": "Synthetic Produktion GmbH",
                        "email": "info@produktion.example",
                        "website": "https://produktion.example/",
                        "city": "Karlsruhe",
                        "sector": "manufacturing_industry",
                        "source_url": "https://produktion.example/kontakt",
                        "public_observation": "Fertigt öffentlich beschriebene Präzisionsteile.",
                        "suggested_service": "IT Security Assessment / Backup & Recovery",
                        "email_subject": "Kurzer Austausch zur IT-Sicherheit bei Synthetic Produktion GmbH",
                        "email_body": "Guten Tag,\\n\\nindividuelle Beratung statt starrem Paket.\\n",
                    },
                    {
                        "company": "Synthetic Logistik GmbH",
                        "email": "info@logistik.example",
                        "website": "https://logistik.example/",
                        "city": "Karlsruhe",
                        "sector": "logistics",
                        "source_url": "https://logistik.example/kontakt",
                        "public_observation": "Bietet öffentlich beschriebene Logistikleistungen.",
                        "email_subject": "Kurzer Austausch zur IT-Sicherheit bei Synthetic Logistik GmbH",
                        "email_body": "Guten Tag,\\n\\nLogistik-Beratung.\\n",
                    },
                ]
            }
        )
    )

    args = arguments(
        "preview",
        "manufacturing_industry",
        str(path),
        "Karlsruhe",
        review_input=True,
        reviewed_by="Hasib Gharibyar",
    )
    assert dispatch(args, db, config) == 0
    output = capsys.readouterr().out
    assert "Synthetic Produktion GmbH" in output
    assert "individuelle Beratung statt starrem Paket" in output
    assert "Synthetic Logistik GmbH" not in output

    lead = db.all()[0]
    assert lead.segment == "manufacturing_industry"
    assert lead.country == "Germany"
    assert lead.final_score == config.minimum_score
    assert lead.qualified_at
    assert lead.draft_source == "manual_json"
    assert lead.draft_subject.startswith("Kurzer Austausch")
    assert any(e.kind == "personalization" for e in lead.draft_evidence)


def test_review_input_requires_named_reviewer(db, config, tmp_path):
    path = fixture(
        tmp_path / "lead.json",
        [
            {
                "company": "Synthetic Produktion GmbH",
                "email": "info@produktion.example",
                "website": "https://produktion.example/",
                "city": "Karlsruhe",
                "sector": "manufacturing_industry",
                "source_url": "https://produktion.example/kontakt",
            }
        ],
    )
    args = arguments(
        "preview",
        "manufacturing_industry",
        str(path),
        "Karlsruhe",
        review_input=True,
    )
    args.actor = ""
    with pytest.raises(ValueError, match="requires --reviewed-by"):
        dispatch(args, db, config)


def test_reimport_keeps_contact_history_for_new_sectors(db, config, tmp_path):
    path = fixture(
        tmp_path / "lead.json",
        [
            {
                "company": "Synthetic Hausverwaltung GmbH",
                "email": "info@verwaltung.example",
                "website": "https://verwaltung.example/",
                "city": "Karlsruhe",
                "sector": "property_management",
                "source_url": "https://verwaltung.example/kontakt",
            }
        ],
    )
    imported = import_leads(
        db,
        path,
        "property_management",
        config,
        default_region="Karlsruhe",
        manual_reviewed_override=True,
        reviewed_by_override="Hasib Gharibyar",
    )
    mark_contacted(
        db,
        imported[0].public_email,
        config,
        "Hasib Gharibyar",
        "Synthetic previous contact",
    )
    repeated = import_leads(
        db,
        path,
        "property_management",
        config,
        default_region="Karlsruhe",
        manual_reviewed_override=True,
        reviewed_by_override="Hasib Gharibyar",
    )
    assert repeated[0].id == imported[0].id
    assert stopped(repeated[0])
    assert len(db.history(repeated[0].id or 0)) == 1


def test_mixed_sector_all_preview_is_single_safe_batch(db, config, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "mixed.json"
    path.write_text(
        json.dumps(
            {
                "leads": [
                    {
                        "company": "Synthetic Produktion GmbH",
                        "email": "info@produktion.example",
                        "website": "https://produktion.example/",
                        "city": "Karlsruhe",
                        "sector": "manufacturing_industry",
                        "source_url": "https://produktion.example/kontakt",
                        "public_observation": "Produziert Präzisionsteile.",
                        "email_subject": "Produktion Security",
                        "email_body": "Individuelle Beratung Produktion",
                    },
                    {
                        "company": "Synthetic Logistik GmbH",
                        "email": "info@logistik.example",
                        "website": "https://logistik.example/",
                        "city": "Karlsruhe",
                        "sector": "logistics",
                        "source_url": "https://logistik.example/kontakt",
                        "public_observation": "Bietet Logistikleistungen.",
                        "email_subject": "Logistik Security",
                        "email_body": "Individuelle Beratung Logistik",
                    },
                ]
            }
        )
    )
    args = arguments(
        "preview",
        "all",
        str(path),
        "Karlsruhe",
        review_input=True,
        reviewed_by="Hasib Gharibyar",
    )
    assert dispatch(args, db, config) == 0
    output = capsys.readouterr().out
    assert "Gesamt: 2" in output
    assert "Pending outreach: 2" in output
    assert "Individuelle Beratung Produktion" in output
    assert "Individuelle Beratung Logistik" in output
    assert {lead.segment for lead in db.all()} == {"manufacturing_industry", "logistics"}


def test_mixed_sector_all_requires_explicit_input(db, config):
    with pytest.raises(ValueError, match="requires an explicit --input"):
        dispatch(arguments("preview", "all"), db, config)


def test_reviewed_reimport_qualifies_existing_unsent_identity(db, config, tmp_path):
    path = fixture(
        tmp_path / "lead.json",
        [
            {
                "company": "Synthetic Produktion GmbH",
                "email": "info@produktion.example",
                "website": "https://produktion.example/",
                "city": "Karlsruhe",
                "sector": "manufacturing_industry",
                "source_url": "https://produktion.example/kontakt",
                "public_observation": "Produziert Präzisionsteile.",
                "email_subject": "Produktion Security",
                "email_body": "Individuelle Beratung Produktion",
            }
        ],
    )
    first = import_leads(db, path, "manufacturing_industry", config)[0]
    assert not first.qualified_at
    assert first.final_score == 0

    reviewed = import_leads(
        db,
        path,
        "manufacturing_industry",
        config,
        default_region="Karlsruhe",
        manual_reviewed_override=True,
        reviewed_by_override="Hasib Gharibyar",
    )[0]
    assert reviewed.id == first.id
    assert reviewed.qualified_at
    assert reviewed.final_score == config.minimum_score
    assert reviewed.draft_evidence
    assert reviewed.draft_source == "manual_json"


def test_suppress_email_is_idempotent_and_survives_reimport(db, config, tmp_path):
    path = fixture(
        tmp_path / "lead.json",
        [
            {
                "company": "Elektro Synthetic GmbH",
                "email": "info@elektro-synthetic.example",
                "website": "https://elektro-synthetic.example/",
                "city": "Karlsruhe",
                "sector": "electrical_engineering",
                "source_url": "https://elektro-synthetic.example/kontakt",
            }
        ],
    )
    lead = import_leads(
        db,
        path,
        "electrical_engineering",
        config,
        default_region="Karlsruhe",
        manual_reviewed_override=True,
        reviewed_by_override="OWNER",
    )[0]
    blocked = suppress_email(
        db,
        "info@elektro-synthetic.example",
        "Hasib Gharibyar",
        "Explicit objection to all further marketing contact",
    )
    assert blocked.id == lead.id
    assert blocked.do_not_contact
    assert blocked.email_permission_status == "PROHIBITED"

    # Re-importing the same company must preserve the suppression instead of recreating
    # it as a fresh prospect.
    repeated = import_leads(
        db,
        path,
        "electrical_engineering",
        config,
        default_region="Karlsruhe",
        manual_reviewed_override=True,
        reviewed_by_override="OWNER",
    )[0]
    assert repeated.id == lead.id
    assert repeated.do_not_contact
    assert repeated.email_permission_status == "PROHIBITED"
    assert stopped(repeated)

    # Repeating the suppression is safe and remains permanent.
    again = suppress_email(
        db,
        "info@elektro-synthetic.example",
        "Hasib Gharibyar",
        "Suppression confirmation",
    )
    assert again.id == lead.id
    assert again.do_not_contact
