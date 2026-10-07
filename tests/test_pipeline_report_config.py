import csv
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from conftest import synthetic_lead

from leadagent.config import Config, load_config
from leadagent.database import Database
from leadagent.mail import contacted
from leadagent.models import Status
from leadagent.outreach import set_status
from leadagent.pipeline import discover, select_new
from leadagent.providers import Candidate
from leadagent.report import generate_report, safe_cell
from leadagent.scoring import score


def today():
    return datetime.now(UTC).date().isoformat()


def test_daily_limit_across_repeated_runs(db, config):
    for i in range(30):
        db.upsert(score(synthetic_lead(i), config))
    assert len(select_new(db, config, today())) == 20
    assert select_new(db, config, today()) == []
    assert sum(bool(lead.qualified_at) for lead in db.all()) == 20


def test_never_fill_quota(db, config):
    for i in range(7):
        db.upsert(score(synthetic_lead(i), config))
    assert len(select_new(db, config, today())) == 7


def test_contacted_never_new_even_with_alias(db, config):
    lead, _ = db.upsert(score(synthetic_lead(), config))
    contacted(db, lead.id, config, "reviewer", "Prior correspondence recorded")
    duplicate = synthetic_lead()
    duplicate.website = "https://another-domain.example"
    db.upsert(score(duplicate, config))
    assert select_new(db, config, today()) == []


@pytest.mark.parametrize("status", [Status.REJECTED, Status.DO_NOT_CONTACT, Status.CUSTOMER])
def test_suppressed_never_qualified(db, config, status):
    lead, _ = db.upsert(score(synthetic_lead(), config))
    set_status(db, lead.id, status, "reviewer")
    assert select_new(db, config, today()) == []


def test_distribution_quality_wins(db, config):
    for i in range(25):
        db.upsert(score(synthetic_lead(i, "tax_advisory"), config))
    assert len(select_new(db, config, today())) == 20


def test_concurrent_quota(db, config):
    for i in range(30):
        db.upsert(score(synthetic_lead(i), config))

    def select(_):
        local = Database(config.database)
        try:
            return len(select_new(local, config, today()))
        finally:
            local.close()

    with ThreadPoolExecutor(max_workers=3) as pool:
        assert sum(pool.map(select, range(3))) == 20


def test_report(db, qualified, config):
    md, csv_path = generate_report(db, config)
    text = md.read_text()
    assert qualified.company_name in text
    for field in [
        "Final score",
        "Fit:",
        "Intent:",
        "Evidence:",
        "Contact route:",
        "Suggested subject:",
        "Suggested email",
        "Source URLs",
        "Previous contact history",
        "REVIEW",
    ]:
        assert field in text
    assert qualified.evidence[0].source_url in text
    with csv_path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1 and rows[0]["action"] == "REVIEW"
    assert rows[0]["email"] == qualified.draft_text


def test_empty_report(db, config):
    assert "0 qualified" in generate_report(db, config)[0].read_text()


@pytest.mark.parametrize("value", ["=HYPERLINK(x)", "+cmd", "-1+1", "@formula", " \t=cmd"])
def test_formula_safety(value):
    assert safe_cell(value).startswith("'")


@pytest.mark.parametrize(
    "key,value",
    [
        ("daily_new_lead_limit", 21),
        ("daily_new_lead_limit", True),
        ("minimum_score", 101),
        ("minimum_score", "70"),
        ("maximum_followups", 2),
        ("followup_delay_days", 0),
        ("country", "Austria"),
        ("fit_weight", 1.2),
    ],
)
def test_invalid_config(key, value):
    config = Config()
    setattr(config, key, value)
    with pytest.raises(ValueError):
        config.validate()


def test_config_from_yaml():
    assert load_config("config.yaml").daily_new_lead_limit == 20


def test_unknown_config_key(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("unknown_setting: 1\n")
    with pytest.raises(ValueError):
        load_config(path)


def test_unsafe_permission_policy(config):
    config.mail.allowed_permission_states = ["UNKNOWN"]
    with pytest.raises(ValueError):
        config.validate()


def test_discover_isolated_failures(db, config):
    class Provider:
        name = "test"

        def discover(self, limit):
            for i in range(3):
                yield Candidate(
                    str(i),
                    f"https://synthetic-{i}.example",
                    "https://synthetic.example",
                    "2026-01-01T00:00:00+00:00",
                )

    class Research:
        def research(self, candidate):
            if candidate.company_name == "1":
                raise ValueError("blocked")
            return synthetic_lead(int(candidate.company_name)), []

    result = discover(db, config, Provider(), Research())
    assert (result.discovered, result.errors, len(result.qualified_ids)) == (2, 1, 2)
    assert db.connection.execute("SELECT completed_at FROM runs").fetchone()[0]


def test_malformed_nested_config(tmp_path):
    for text in [
        "segments: []",
        "segments: {medical_practice: null, tax_advisory: {target: 5}, law_firm: {target: 5}, it_service_provider: {target: 4}}",
        "mail: null",
        "mail: {allowed_permission_states: [[]]}",
    ]:
        path = tmp_path / "bad.yaml"
        path.write_text(text)
        with pytest.raises(ValueError):
            load_config(path)


def test_scoring_weights_are_configurable(config):
    lead = score(synthetic_lead(), config)
    previous = lead.final_score
    config.intent_points["external_it"] = 0
    config.validate()
    assert score(synthetic_lead(), config).final_score < previous
