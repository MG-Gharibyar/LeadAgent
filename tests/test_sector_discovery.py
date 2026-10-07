import json
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
from conftest import synthetic_lead

from leadagent.cli import main
from leadagent.config import DiscoveryConfig
from leadagent.models import Status, utcnow
from leadagent.outreach import set_status
from leadagent.pipeline import discover, select_new
from leadagent.providers import BraveSearchProvider, Candidate
from leadagent.research import Document, _contact
from leadagent.scoring import score
from leadagent.sectors import KARLSRUHE_REGION, configure_search
from leadagent.web import Page


def test_karlsruhe_search_covers_region_without_nationwide_rotation():
    config = DiscoveryConfig()
    configure_search(config, "law_firm", "Karlsruhe")
    assert config.query_regions == []
    assert len(config.queries) == len(KARLSRUHE_REGION) + 1
    assert all(any(city in query for query in config.queries) for city in KARLSRUHE_REGION)
    assert any("Umgebung" in query for query in config.queries)


def test_brave_region_queries_are_distributed(monkeypatch):
    monkeypatch.setenv("DSC_BRAVE_API_KEY", "synthetic")
    config = DiscoveryConfig(
        queries=["Kanzlei A", "Kanzlei B", "Kanzlei C"], location="Synthetic", query_regions=[]
    )
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.side_effect = [
        json.dumps(
            {"web": {"results": [{"url": f"https://firm-{q}-{i}.example"} for i in range(5)]}}
        ).encode()
        for q in range(3)
    ]
    with (
        patch("leadagent.providers.urlopen", return_value=response) as request,
        patch("leadagent.providers.time.sleep"),
    ):
        assert len(list(BraveSearchProvider(config).discover(6))) == 6
    queries = [parse_qs(urlsplit(call.args[0].full_url).query) for call in request.call_args_list]
    assert [query["q"][0] for query in queries] == config.queries
    assert all(query["count"] == ["2"] for query in queries)


@pytest.mark.parametrize(
    "status",
    [
        Status.CONTACTED,
        Status.RESPONDED,
        Status.INTERESTED,
        Status.REJECTED,
        Status.DO_NOT_CONTACT,
        Status.CUSTOMER,
    ],
)
def test_existing_campaign_statuses_are_excluded_before_research(status, db, config):
    lead = score(synthetic_lead(segment="law_firm"), config)
    stored, _ = db.upsert(lead)
    stored.outreach_status = status.value
    db.save(stored)
    config.discovery.sector = "law_firm"

    class Provider:
        name = "synthetic"

        def discover(self, limit):
            yield Candidate(
                "", "http://www.synthetic-1.example/", "https://synthetic-1.example/", utcnow()
            )

    researcher = MagicMock()
    result = discover(db, config, Provider(), researcher)
    assert result.duplicates == result.already_contacted == 1
    assert result.discovered == 0 and not result.qualified_ids
    researcher.research.assert_not_called()


def test_email_alias_contacted_after_research(db, config):
    first = score(synthetic_lead(segment="law_firm"), config)
    stored, _ = db.upsert(first)
    set_status(db, stored.id, Status.RESPONDED, "reviewer")
    alternate = synthetic_lead(2, "law_firm")
    alternate.public_email = first.public_email
    config.discovery.sector = "law_firm"

    class Provider:
        name = "synthetic"

        def discover(self, limit):
            yield Candidate("", alternate.website, alternate.website, utcnow())

    researcher = MagicMock()
    researcher.research.return_value = (alternate, [])
    result = discover(db, config, Provider(), researcher)
    assert result.discovered == 0
    assert result.duplicates == result.already_contacted == 1
    assert db.get(stored.id).outreach_status == "RESPONDED"


def test_targeted_selection_does_not_qualify_other_sectors(db, config):
    db.upsert(score(synthetic_lead(), config))
    law, _ = db.upsert(score(synthetic_lead(2, "law_firm"), config))
    config.discovery.sector = "law_firm"
    assert select_new(db, config, datetime.now(UTC).date().isoformat()) == [law.id]


def test_discover_and_preview_separation(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "config.yaml"
    config.write_text("database: data/private.sqlite3\nreport_directory: reports\n")
    source = tmp_path / "fixtures.json"
    source.write_text(json.dumps([synthetic_lead(segment="law_firm").to_dict()]))
    prefix = ["--config", str(config)]
    with (
        patch("leadagent.mail.smtp_transport") as smtp,
        patch("leadagent.sent_mail.connect") as imap,
    ):
        assert (
            main(
                prefix
                + [
                    "discover",
                    "--sector",
                    "law_firm",
                    "--location",
                    "Karlsruhe",
                    "--provider",
                    "fixture",
                    "--input",
                    str(source),
                ]
            )
            == 0
        )
        assert main(prefix + ["outreach", "preview", "law_firm"]) == 0
        smtp.assert_not_called()
        imap.assert_not_called()
    output = capsys.readouterr().out
    for label in (
        "Discovered:",
        "Duplicates:",
        "Already contacted:",
        "Qualified:",
        "Pending outreach:",
        "Score:",
        "Source:",
    ):
        assert label in output
    assert "1.100 €" in output
    assert (tmp_path / "reports/discovery-1-review.md").exists()


def test_verified_regional_address_and_printed_role_email():
    page = Page("https://law.example/kontakt", "", utcnow())
    lead = synthetic_lead(segment="law_firm")
    lead.public_email = ""
    lead.city = ""
    lead.country = ""
    doc = Document(
        "<p>76437 Rastatt</p><p>office@law.example</p><p>named.person@law.example</p>", page.url
    )
    _contact(lead, page, doc, "Karlsruhe")
    assert (lead.postal_code, lead.city, lead.country) == ("76437", "Rastatt", "Germany")
    assert lead.public_email == "office@law.example"
    assert all(e.source_url == page.url for e in lead.evidence if e.kind == "contact")
    assert not lead.technologies_detected


def test_public_company_name_rejects_generic_site_name():
    from leadagent.research import public_company_name

    doc = Document(
        '<meta property="og:site_name" content="Rechtsanwalt Rastatt"><title>Rechtsanwalt Rastatt | Synthetic Weber</title>',
        "https://law.example",
    )
    assert public_company_name(doc) == "Synthetic Weber"


def test_legal_notice_link_label_supports_query_urls(config):
    from leadagent.research import Researcher

    class Client:
        config = DiscoveryConfig(maximum_pages_per_company=3)

        def fetch(self, url):
            return Page(
                url,
                '<title>Synthetic Anwaltskanzlei</title><h1>Rechtsanwälte</h1><p>10115 Berlin</p><p>Deutschland</p><a href="/?page_id=99">Impressum</a>',
                utcnow(),
            )

    lead, _ = Researcher(Client()).research(
        Candidate("", "https://law.example/", "https://law.example/", utcnow())
    )
    assert "unverified_legal_identity" not in score(lead, config).risk_disqualification_signals


def test_svg_titles_cannot_contaminate_company_identity():
    from leadagent.research import public_company_name

    doc = Document(
        "<title>Rechtsanwalt Rastatt | Synthetic Weber</title><svg><title>Nach oben scrollen</title></svg>",
        "https://law.example",
    )
    assert public_company_name(doc) == "Synthetic Weber"


def test_public_law_firm_digital_and_location_claims_are_not_windows_inferences():
    from leadagent.research import extract_evidence

    markup = '<h1>Synthetic Rechtsanwälte</h1><a href="https://portal.example">WebAkte</a><p>Als Kanzlei mit Standorten in Karlsruhe und Bretten beraten wir Mandanten.</p>'
    page = Page("https://law.example/", markup, utcnow())
    evidence = extract_evidence(page, Document(markup, page.url), include_segment=True)
    assert any(e.kind == "digital" and e.value == "client_portal" for e in evidence)
    assert any(e.kind == "intent" and e.value == "multiple_locations" for e in evidence)
    assert not any(e.kind == "technology" for e in evidence)
