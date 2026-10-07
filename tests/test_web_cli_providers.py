import json
import socket
from unittest.mock import patch
from urllib.error import HTTPError

import pytest
from conftest import synthetic_lead

from leadagent.cli import main
from leadagent.config import DiscoveryConfig
from leadagent.providers import BraveSearchProvider, FixtureProvider
from leadagent.web import AccessDenied, PublicWebClient, validate_public_url


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/private",
        "https://user:secret@synthetic.example/",
        "https://synthetic.example:8443/",
    ],
)
def test_unsafe_url(url):
    with pytest.raises(AccessDenied):
        validate_public_url(url)


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "192.168.1.1"]
)
def test_private_ip_refused(address):
    with (
        patch(
            "leadagent.web.socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))],
        ),
        pytest.raises(AccessDenied, match="Private"),
    ):
        validate_public_url("https://synthetic.example/")


def test_robots_denial():
    client = PublicWebClient(DiscoveryConfig())
    with (
        patch.object(client, "_check"),
        patch.object(client, "_request", return_value=("User-agent: *\nDisallow: /", "text/plain")),
        pytest.raises(AccessDenied, match="robots"),
    ):
        client.fetch("https://synthetic.example/")


def test_robots_fail_closed():
    client = PublicWebClient(DiscoveryConfig())
    with (
        patch.object(client, "_check"),
        patch.object(
            client,
            "_request",
            side_effect=HTTPError(
                "https://synthetic.example/robots.txt", 403, "forbidden", {}, None
            ),
        ),
        pytest.raises(AccessDenied),
    ):
        client.fetch("https://synthetic.example/")


def test_captcha_skipped():
    client = PublicWebClient(DiscoveryConfig())
    with (
        patch.object(client, "_check"),
        patch.object(
            client,
            "_request",
            side_effect=[
                ("User-agent: *\nDisallow:", "text/plain"),
                ("<html>verify you are human</html>", "text/html"),
            ],
        ),
        pytest.raises(AccessDenied, match="Human"),
    ):
        client.fetch("https://synthetic.example/")


def test_redirect_rechecks_robots():
    client = PublicWebClient(DiscoveryConfig())
    redirect = HTTPError(
        "https://synthetic.example/", 302, "redirect", {"Location": "https://second.example/"}, None
    )
    with (
        patch.object(client, "_check"),
        patch.object(
            client,
            "_request",
            side_effect=[
                ("User-agent: *\nDisallow:", "text/plain"),
                redirect,
                ("User-agent: *\nDisallow: /", "text/plain"),
            ],
        ),
        pytest.raises(AccessDenied, match="robots"),
    ):
        client.fetch("https://synthetic.example/")


def test_fixture_rejects_real_domains(tmp_path):
    path = tmp_path / "fixtures.json"
    lead = synthetic_lead()
    lead.website = "https://not-synthetic.de"
    path.write_text(json.dumps([lead.to_dict()]))
    with pytest.raises(ValueError, match="synthetic"):
        list(FixtureProvider(str(path)).discover(20))


def test_search_requires_api_key(monkeypatch):
    monkeypatch.delenv("DSC_BRAVE_API_KEY", raising=False)
    with pytest.raises(ValueError, match="DSC_BRAVE_API_KEY"):
        list(BraveSearchProvider(DiscoveryConfig()).discover(20))


def test_cli_end_to_end(tmp_path, capsys):
    fixture = tmp_path / "fixtures.json"
    fixture.write_text(json.dumps([synthetic_lead().to_dict()]))
    config = tmp_path / "config.yaml"
    config.write_text(
        f"database: {tmp_path / 'demo.sqlite3'}\nreport_directory: {tmp_path / 'reports'}\n"
    )
    prefix = ["--config", str(config)]
    assert main(prefix + ["discover", "--provider", "fixture", "--input", str(fixture)]) == 0
    assert main(prefix + ["leads"]) == 0
    assert main(prefix + ["mail", "preview", "1"]) == 0
    assert main(prefix + ["mail", "send", "1"]) == 0
    assert main(prefix + ["stats"]) == 0
    assert main(prefix + ["block", "1", "--actor", "reviewer"]) == 0
    assert main(prefix + ["mail", "send", "1"]) == 1
    assert "DRY_RUN" in capsys.readouterr().out


def test_fixture_url_query_cannot_hide_real_domain(tmp_path):
    path = tmp_path / "bad.json"
    lead = synthetic_lead()
    lead.website = "https://not-synthetic.de/?redirect=synthetic.example/"
    path.write_text(json.dumps([lead.to_dict()]))
    with pytest.raises(ValueError, match="synthetic"):
        list(FixtureProvider(str(path)).discover(20))


def test_fixture_sources_must_be_synthetic(tmp_path):
    path = tmp_path / "bad.json"
    lead = synthetic_lead()
    lead.evidence[0].source_url = "https://not-synthetic.de/"
    path.write_text(json.dumps([lead.to_dict()]))
    with pytest.raises(ValueError, match="synthetic"):
        list(FixtureProvider(str(path)).discover(20))


def test_wildcard_robots_disallow_is_conservative():
    client = PublicWebClient(DiscoveryConfig())
    with (
        patch.object(client, "_check"),
        patch.object(
            client, "_request", return_value=("User-agent: *\nDisallow: /private/*", "text/plain")
        ),
        pytest.raises(AccessDenied, match="robots"),
    ):
        client.fetch("https://synthetic.example/private/facts")


def test_robots_allow_root_does_not_override_disallow():
    client = PublicWebClient(DiscoveryConfig())
    with (
        patch.object(client, "_check"),
        patch.object(
            client,
            "_request",
            return_value=("User-agent: *\nAllow: /\nDisallow: /private/", "text/plain"),
        ),
        pytest.raises(AccessDenied, match="robots"),
    ):
        client.fetch("https://synthetic.example/private/facts")
