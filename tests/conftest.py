from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from leadagent.config import Config
from leadagent.database import Database
from leadagent.models import Evidence, Lead
from leadagent.outreach import prepare_draft
from leadagent.scoring import score


def synthetic_lead(number: int = 1, segment: str = "tax_advisory") -> Lead:
    website = f"https://synthetic-{number}.example"
    at = datetime.now(UTC).isoformat()
    claims = [
        (
            "segment",
            segment,
            f"Synthetische Firma {number}: Steuerberatung mit digitaler Zusammenarbeit",
        ),
        ("location", "Germany", "10115 Berlin, Deutschland"),
        ("size", "small_medium_team", "Unser Team umfasst 12 Mitarbeiter"),
        ("technology", "microsoft_365", "Wir nutzen Microsoft 365 für die Zusammenarbeit"),
        ("operations", "backup", "Unser Backup sichert die Arbeitsunterlagen"),
        (
            "digital",
            "client_portal",
            "Unser Mandantenportal ermöglicht digitalen Dokumentenaustausch",
        ),
        ("intent", "external_it", "Unsere IT wird durch einen externen Dienstleister betreut"),
        ("intent", "multiple_locations", "Unsere Standorte in Berlin und Hamburg"),
    ]
    lead = Lead(
        f"Synthetische Firma {number} GmbH",
        website,
        city="Berlin",
        country="Germany",
        public_email=f"kontakt@synthetic-{number}.example",
        last_researched_at=at,
        source_urls=[website],
    )
    lead.evidence = [Evidence(kind, value, excerpt, website, at) for kind, value, excerpt in claims]
    return lead


@pytest.fixture
def config(tmp_path: Path) -> Config:
    result = Config(
        database=str(tmp_path / "test.sqlite3"), report_directory=str(tmp_path / "reports")
    )
    result.validate()
    return result


@pytest.fixture
def db(config: Config):
    database = Database(config.database)
    yield database
    database.close()


@pytest.fixture
def qualified(db: Database, config: Config) -> Lead:
    lead = synthetic_lead()
    score(lead, config)
    prepare_draft(lead)
    lead.qualified_at = datetime.now(UTC).isoformat()
    result, _ = db.upsert(lead)
    return result
