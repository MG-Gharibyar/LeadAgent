import pytest
from conftest import synthetic_lead

from leadagent.models import Evidence, Segment, utcnow
from leadagent.research import Document, Researcher, extract_evidence
from leadagent.scoring import classify, match_service, score
from leadagent.web import Page


def test_explainable_scores(config):
    lead = score(synthetic_lead(), config)
    assert (lead.fit_score, lead.intent_score, lead.final_score) == (100, 30, 79)
    assert all(
        reason.evidence in lead.evidence and reason.evidence.source_url
        for reason in lead.score_reasons
    )
    assert len({r.rule for r in lead.score_reasons}) == len(lead.score_reasons)


def test_high_intent_ranks_higher(config):
    plain = synthetic_lead()
    plain.evidence = [e for e in plain.evidence if e.kind != "intent"]
    strong = synthetic_lead(2)
    strong.evidence.append(
        Evidence(
            "intent", "explicit_request", "Wir suchen einen IT Partner", strong.website, utcnow()
        )
    )
    assert score(strong, config).final_score > score(plain, config).final_score


def test_generic_does_not_qualify(config):
    lead = synthetic_lead()
    lead.evidence = [e for e in lead.evidence if e.kind in {"segment", "location"}]
    assert score(lead, config).final_score < 70


@pytest.mark.parametrize("segment", [s.value for s in Segment if s != Segment.UNKNOWN])
def test_classification(segment):
    assert classify(synthetic_lead(segment=segment).evidence) == segment


def test_ambiguous_unknown():
    lead = synthetic_lead()
    lead.evidence.append(Evidence("segment", "law_firm", "Rechtsanwälte", lead.website, utcnow()))
    assert classify(lead.evidence) == "unknown"


@pytest.mark.parametrize(
    "segment,service",
    [
        ("medical_practice", "Backup & Recovery Review"),
        ("tax_advisory", "Microsoft 365 Security"),
        ("law_firm", "Microsoft 365 Security"),
        ("it_service_provider", "Partner Security Assessment"),
    ],
)
def test_service_matching(config, segment, service):
    assert score(synthetic_lead(segment=segment), config).recommended_dsc_service == service


def test_existing_pentest_provider_reviewed(config):
    lead = synthetic_lead(segment="it_service_provider")
    lead.evidence.append(
        Evidence("security", "penetration_testing", "Wir bieten Pentests", lead.website, utcnow())
    )
    assert score(lead, config).final_score == 0
    assert match_service(lead)[0] == ""


def test_country_and_risk_gate(config):
    lead = synthetic_lead()
    lead.country = "Austria"
    assert score(lead, config).final_score == 0
    lead = synthetic_lead()
    lead.evidence.append(
        Evidence("risk", "large_enterprise", "5000 Mitarbeiter", lead.website, utcnow())
    )
    assert score(lead, config).final_score == 0


def test_html_evidence_extraction():
    markup = """<title>Synthetic Kanzlei</title><script>Windows</script>
    <h1>Steuerberatung</h1><p>12 Mitarbeiter arbeiten mit Microsoft 365.</p>
    <p>Unser Mandantenportal ermöglicht digitale Zusammenarbeit.</p>
    <p>Wir bieten keine Pentests.</p><p>Unsere IT wird durch einen externen Dienstleister betreut.</p>"""
    page = Page("https://synthetic.example", markup, utcnow())
    evidence = extract_evidence(page, Document(markup, page.url), include_segment=True)
    assert any(e.kind == "segment" and e.value == "tax_advisory" for e in evidence)
    assert any(e.value == "microsoft_365" for e in evidence)
    assert not any(e.value in {"windows", "penetration_testing"} for e in evidence)
    assert all(e.excerpt in markup and e.source_url == page.url for e in evidence)


def test_web_research_role_contact_and_country():
    from leadagent.config import DiscoveryConfig
    from leadagent.providers import Candidate

    class Client:
        config = DiscoveryConfig(maximum_pages_per_company=3)

        def fetch(self, url):
            return Page(
                url,
                '<title>Synthetic Steuerberatung</title><h1>Steuerberatung</h1><p>12 Mitarbeiter arbeiten mit Microsoft 365.</p><p>10115 Berlin</p><p>Deutschland</p><a href="mailto:kontakt@synthetic.example">Kontakt</a><a href="mailto:person@synthetic.example">Person</a>',
                utcnow(),
            )

    lead, aliases = Researcher(Client()).research(
        Candidate("", "https://synthetic.example/", "https://synthetic.example", utcnow())
    )
    assert lead.country == "Germany" and lead.city == "Berlin"
    assert lead.public_email == "kontakt@synthetic.example"
    assert not aliases


def test_legal_identity_required_for_web_qualification(config):
    from leadagent.config import DiscoveryConfig
    from leadagent.providers import Candidate

    class Client:
        config = DiscoveryConfig()

        def fetch(self, url):
            return Page(
                url,
                "<title>Synthetic Steuerberatung</title><p>Steuerberatung</p><p>12 Mitarbeiter arbeiten mit Microsoft 365.</p><p>Backup und Mandantenportal</p><p>10115 Berlin</p><p>Deutschland</p>",
                utcnow(),
            )

    lead, _ = Researcher(Client()).research(
        Candidate("", "https://synthetic.example/", "https://synthetic.example", utcnow())
    )
    assert score(lead, config).final_score == 0
    assert "unverified_legal_identity" in lead.risk_disqualification_signals


def test_public_company_legal_notice_allows_qualification(config):
    from leadagent.config import DiscoveryConfig
    from leadagent.providers import Candidate

    class Client:
        config = DiscoveryConfig()

        def fetch(self, url):
            return Page(
                url,
                '<title>Synthetic Steuerberatung</title><h1>Steuerberatung</h1><p>12 Mitarbeiter arbeiten mit Microsoft 365.</p><p>Backup und Mandantenportal</p><p>10115 Berlin</p><p>Deutschland</p><a href="/impressum">Impressum</a>',
                utcnow(),
            )

    lead, _ = Researcher(Client()).research(
        Candidate("", "https://synthetic.example/", "https://synthetic.example", utcnow())
    )
    assert score(lead, config).final_score >= 70
    assert not lead.risk_disqualification_signals


@pytest.mark.parametrize(
    "need,service",
    [
        ("training", "IT Security Training"),
        ("penetration_testing", "Penetration Testing"),
        ("managed_it", "Managed IT & Security"),
    ],
)
def test_explicit_service_request(config, need, service):
    lead = synthetic_lead()
    lead.evidence.append(
        Evidence("service_need", need, "Explicit synthetic request", lead.website, utcnow())
    )
    assert score(lead, config).recommended_dsc_service == service


def test_existing_security_assessment_provider_not_gap(config):
    lead = synthetic_lead(segment="it_service_provider")
    lead.evidence.append(
        Evidence(
            "security",
            "security_assessment",
            "Wir bieten Security Assessments",
            lead.website,
            utcnow(),
        )
    )
    assert score(lead, config).final_score == 0
