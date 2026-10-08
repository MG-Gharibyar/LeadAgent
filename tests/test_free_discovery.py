import json
from unittest.mock import MagicMock, patch

import pytest

from leadagent.cli import main
from leadagent.config import DiscoveryConfig
from leadagent.free_providers import (
    CachedDirectoryClient,
    FreeDiscoveryProvider,
    LawAssociationDirectory,
    OpenStreetMapDirectory,
    StuttgartBarDirectory,
)
from leadagent.models import Status, utcnow
from leadagent.pipeline import discover
from leadagent.providers import BraveSearchProvider, Candidate, provider_from_config
from leadagent.research import Researcher
from leadagent.web import AccessDenied, Page, PublicWebClient


def candidate(domain="firm.example", source="https://directory.example/"):
    return Candidate("Directory hint", "https://" + domain + "/", source, utcnow(), "Hint City")


def source(name, candidates=None, error=None):
    mock = MagicMock()
    mock.name = name
    mock.discover.side_effect = error
    mock.discover.return_value = iter(candidates or [])
    return mock


def test_default_free_without_brave_key(monkeypatch):
    monkeypatch.delenv("DSC_BRAVE_API_KEY", raising=False)
    assert isinstance(provider_from_config(DiscoveryConfig()), FreeDiscoveryProvider)


def test_blocked_source_fallback_and_deduplication():
    a = source("a", [candidate(), candidate("second.example")])
    b = source("b", [candidate("www.firm.example", "https://other.example/")])
    blocked = source("blocked", error=AccessDenied("robots"))
    provider = FreeDiscoveryProvider(DiscoveryConfig(), [blocked, a, b])
    found = list(provider.discover(2))
    assert len(found) == 2
    assert provider.duplicate_domains == 1
    assert len(found[0].discovery_sources) == 2
    assert provider.source_results["blocked"]["status"] == "skipped"
    for adapter in (a, b, blocked):
        adapter.discover.assert_called_once_with(2)


def test_brave_api_failure_falls_back(monkeypatch):
    monkeypatch.setenv("DSC_BRAVE_API_KEY", "synthetic")
    with (
        patch("leadagent.providers.urlopen", side_effect=OSError("unavailable")),
        patch("leadagent.providers.time.sleep"),
        patch(
            "leadagent.free_providers.FreeDiscoveryProvider.discover",
            return_value=iter([candidate()]),
        ),
    ):
        assert (
            len(list(BraveSearchProvider(DiscoveryConfig(queries=["synthetic"])).discover(20))) == 1
        )


@pytest.mark.parametrize(
    "sector", ["law_firm", "medical_practice", "tax_advisor", "it_service_provider"]
)
def test_osm_sector_adapter(sector):
    client = MagicMock()
    client.fetch.return_value = Page(
        "https://directory.example/",
        json.dumps(
            {
                "elements": [
                    {
                        "type": "node",
                        "id": 1,
                        "tags": {
                            "name": "Synthetic Firm",
                            "website": "firm.example",
                            "addr:city": "Synthetic City",
                            "email": "personal@firm.example",
                        },
                    },
                    {"type": "node", "id": 2, "tags": {"name": "No Website"}},
                ]
            }
        ),
        utcnow(),
    )
    found = list(
        OpenStreetMapDirectory(
            DiscoveryConfig(sector=sector, location="Karlsruhe"), client
        ).discover(20)
    )
    assert len(found) == 1
    assert found[0].website == "https://firm.example"
    assert found[0].fixture is None
    assert "email" not in found[0].__dict__
    assert "35000" in client.fetch.call_args.args[0]


def test_association_only_external_firm_links():
    client = MagicMock()
    client.fetch.return_value = Page(
        "https://directory.example/",
        '<a href="https://firm.example/jobs">Synthetic Rechtsanwälte</a><a href="https://designer.example/">Webdesign</a>',
        utcnow(),
    )
    assert [c.website for c in LawAssociationDirectory(client).discover(20)] == [
        "https://firm.example/"
    ]


def test_directory_to_independent_website_research_and_suppression(db, config):
    config.discovery.sector = "law_firm"
    config.discovery.location = "Karlsruhe"
    client = MagicMock()
    client.config = config.discovery
    markup = '<title>Synthetic Rechtsanwälte</title><p>76133 Karlsruhe</p><a href="/impressum">Impressum</a><a href="mailto:info@firm.example">Kontakt</a>'
    client.fetch.side_effect = lambda url: Page(url, markup, utcnow())
    provider = FreeDiscoveryProvider(config.discovery, [source("directory", [candidate()])])
    result = discover(db, config, provider, Researcher(client))
    assert result.discovered == 1
    lead = db.get(result.new_ids[0])
    assert lead.segment == "law_firm"
    assert lead.company_name != "Directory hint"
    assert lead.city != "Hint City"
    assert lead.public_email == "info@firm.example"
    assert all(
        e.source_url.startswith("https://firm.example/")
        for e in lead.evidence
        if e.kind != "discovery"
    )
    assert any(
        e.kind == "discovery" and e.source_url == "https://directory.example/"
        for e in lead.evidence
    )
    assert "https://directory.example/" in lead.source_urls
    lead.outreach_status = Status.CONTACTED.value
    db.save(lead)
    researcher = MagicMock()
    again = discover(
        db,
        config,
        FreeDiscoveryProvider(config.discovery, [source("directory", [candidate()])]),
        researcher,
    )
    assert again.already_contacted == 1
    researcher.research.assert_not_called()


def test_cache_and_conservative_interval(tmp_path):
    client = CachedDirectoryClient(DiscoveryConfig(cache_directory=str(tmp_path)))
    assert client.client.config.request_interval_seconds >= 5
    page = Page("https://directory.example/", "{}", utcnow())
    with patch.object(client.client, "fetch", return_value=page) as fetch:
        assert client.fetch(page.url) == page
        assert client.fetch(page.url) == page
        fetch.assert_called_once()


def test_http_rate_limiting():
    client = PublicWebClient(DiscoveryConfig(request_interval_seconds=5))
    client.last_request = 10
    response = MagicMock()
    response.__enter__.return_value = response
    response.headers.get_content_type.return_value = "application/json"
    response.headers.get_content_charset.return_value = "utf-8"
    response.read.return_value = b"{}"
    with (
        patch.object(client, "_check"),
        patch.object(client.opener, "open", return_value=response),
        patch("leadagent.web.time.monotonic", return_value=11),
        patch("leadagent.web.time.sleep") as sleep,
    ):
        client._request("https://directory.example/")
        sleep.assert_called_once_with(4)


def test_free_cli_never_invokes_mail(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DSC_BRAVE_API_KEY", raising=False)
    path = tmp_path / "config.yaml"
    path.write_text("database: data/test.sqlite3\nreport_directory: reports\n")
    with (
        patch("leadagent.free_providers.FreeDiscoveryProvider.discover", return_value=iter([])),
        patch("leadagent.mail.smtp_transport") as smtp,
        patch("leadagent.sent_mail.connect") as imap,
    ):
        assert (
            main(
                [
                    "--config",
                    str(path),
                    "discover",
                    "--provider",
                    "free",
                    "--sector",
                    "law_firm",
                    "--location",
                    "Karlsruhe",
                    "--limit",
                    "20",
                ]
            )
            == 0
        )
        smtp.assert_not_called()
        imap.assert_not_called()


def test_blocked_source_is_not_cached(tmp_path):
    client = CachedDirectoryClient(DiscoveryConfig(cache_directory=str(tmp_path)))
    with patch.object(client.client, "fetch", side_effect=AccessDenied("captcha")) as fetch:
        with pytest.raises(AccessDenied):
            client.fetch("https://blocked.example/")
        assert list(tmp_path.iterdir()) == []
        fetch.assert_called_once()


def test_directory_round_robin_budget():
    provider = FreeDiscoveryProvider(
        DiscoveryConfig(),
        [
            source("a", [candidate("one.example"), candidate("two.example")]),
            source("b", [candidate("three.example"), candidate("four.example")]),
        ],
    )
    assert [c.website for c in provider.discover(2)] == [
        "https://one.example/",
        "https://three.example/",
    ]


def test_brave_malformed_response_falls_back(monkeypatch):
    monkeypatch.setenv("DSC_BRAVE_API_KEY", "synthetic")
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = b"[]"
    with (
        patch("leadagent.providers.urlopen", return_value=response),
        patch("leadagent.providers.time.sleep"),
        patch(
            "leadagent.free_providers.FreeDiscoveryProvider.discover",
            return_value=iter([candidate()]),
        ),
    ):
        assert (
            len(list(BraveSearchProvider(DiscoveryConfig(queries=["synthetic"])).discover(20))) == 1
        )


def test_weingarten_location_whitespace_is_normalized():
    from conftest import synthetic_lead

    from leadagent.research import Document, _contact

    lead = synthetic_lead(segment="law_firm")
    lead.country = ""
    lead.city = ""
    page = Page("https://firm.example/kontakt", "<p>76356 Weingarten<br>(Baden)</p>", utcnow())
    _contact(lead, page, Document(page.text, page.url), "Karlsruhe")
    assert lead.city == "Weingarten (Baden)"
    assert lead.country == "Germany"


def test_osm_candidate_preserves_directory_coordinates():
    config = DiscoveryConfig(sector="law_firm", location="Karlsruhe")
    client = MagicMock()
    client.fetch.return_value = Page(
        "https://overpass.private.coffee/api/interpreter",
        json.dumps(
            {
                "elements": [
                    {
                        "type": "node",
                        "id": 9,
                        "lat": 48.80,
                        "lon": 9.20,
                        "tags": {
                            "name": "Coordinate Kanzlei",
                            "website": "https://coordinate.example",
                            "addr:city": "Stuttgart",
                        },
                    }
                ]
            }
        ),
        utcnow(),
    )
    found = list(OpenStreetMapDirectory(config, client).discover(5))
    assert found[0].latitude == 48.80
    assert found[0].longitude == 9.20


def test_overpass_access_denied_tries_independent_endpoint():
    config = DiscoveryConfig(sector="law_firm", location="Karlsruhe")
    client = MagicMock()
    client.fetch.side_effect = [
        AccessDenied("robots"),
        Page(
            "https://overpass-api.de/api/interpreter",
            json.dumps(
                {
                    "elements": [
                        {
                            "type": "node",
                            "id": 42,
                            "lat": 49.0,
                            "lon": 8.4,
                            "tags": {
                                "name": "Independent Endpoint Kanzlei",
                                "website": "https://independent.example",
                                "addr:city": "Karlsruhe",
                            },
                        }
                    ]
                }
            ),
            utcnow(),
        ),
    ]
    found = list(OpenStreetMapDirectory(config, client).discover(5))
    assert [candidate.website for candidate in found] == ["https://independent.example"]
    assert client.fetch.call_count == 2


def test_stuttgart_public_bar_directory_external_sites_only():
    client = MagicMock()
    client.fetch.return_value = Page(
        "https://rak-stuttgart.de/fuer-mandaten/regionale-anwaltssuche",
        (
            '<a href="https://rak-stuttgart.de/impressum">RAK</a>'
            '<a href="https://www.kanzlei-a.example/profil">Kanzlei A</a>'
            '<a href="https://kanzlei-b.example/">Kanzlei B</a>'
        ),
        utcnow(),
    )
    found = list(StuttgartBarDirectory(client).discover(10))
    assert [candidate.website for candidate in found] == [
        "https://www.kanzlei-a.example/",
        "https://kanzlei-b.example/",
    ]


def test_stuttgart_area_enables_bar_directory_source():
    provider = FreeDiscoveryProvider(
        DiscoveryConfig(sector="law_firm", areas=["Stuttgart:35"]),
    )
    assert any(source.name == "rak_stuttgart_regional_search" for source in provider.sources)
