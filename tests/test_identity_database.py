from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import synthetic_lead

from leadagent.database import Database
from leadagent.identity import company_key, normalize_domain, normalize_name
from leadagent.models import Evidence, Status, utcnow
from leadagent.outreach import set_status


@pytest.mark.parametrize(
    "value",
    [
        "https://www.Example.de/",
        "http://example.de",
        "example.de",
        "https://example.de/path/?x=1",
        "https://example.de.:443/",
    ],
)
def test_domain_normalization(value):
    assert normalize_domain(value) == "example.de"


def test_idna():
    assert normalize_domain("https://www.büro.de") == "xn--bro-hoa.de"


@pytest.mark.parametrize(
    "value",
    [
        "file:///tmp/foo",
        "https://user:password@synthetic.example",
        "localhost",
        "https://bad_host.de",
        "https://a..de",
    ],
)
def test_invalid_identity(value):
    with pytest.raises(ValueError):
        normalize_domain(value)


def test_name_city():
    assert normalize_name("Büro Weiß GmbH & Co. KG") == "buro weiss"
    assert company_key("Büro Weiß GmbH", "München") == "buro weiss|munchen"
    assert company_key("Example", "") == ""


def test_duplicate_and_alias(db):
    first, created = db.upsert(synthetic_lead())
    assert created
    lead = synthetic_lead()
    lead.website = "http://www.synthetic-1.example/"
    second, created = db.upsert(lead)
    assert not created and first.id == second.id
    assert len(db.all()) == 1


def test_company_fallback_records_domain_alias(db):
    first, _ = db.upsert(synthetic_lead())
    second = synthetic_lead()
    second.website = "https://alternate.example"
    result, created = db.upsert(second)
    assert not created and result.id == first.id
    third = synthetic_lead(3)
    third.website = "http://www.alternate.example/path"
    result, created = db.upsert(third)
    assert not created and result.id == first.id


def test_verified_redirect_alias(db):
    first, _ = db.upsert(synthetic_lead(), [("old.example", "https://old.example")])
    next_lead = synthetic_lead(2)
    next_lead.website = "https://old.example"
    result, created = db.upsert(next_lead)
    assert not created and result.id == first.id


def test_distinct_city_and_blank_city_do_not_merge(db):
    db.upsert(synthetic_lead())
    other = synthetic_lead(2)
    other.company_name = synthetic_lead().company_name
    other.city = "Hamburg"
    assert db.upsert(other)[1]
    other = synthetic_lead(3)
    other.company_name = synthetic_lead().company_name
    other.city = ""
    assert db.upsert(other)[1]


def test_conflicting_alias_fail_closed(db):
    db.upsert(synthetic_lead())
    db.upsert(synthetic_lead(2))
    with pytest.raises(ValueError, match="Conflicting"):
        db.upsert(synthetic_lead(), [("synthetic-2.example", "https://synthetic-2.example")])


def test_suppression_survives_research_and_restart(db, config):
    lead, _ = db.upsert(synthetic_lead())
    set_status(db, lead.id, Status.DO_NOT_CONTACT, "reviewer", "opt-out")
    assert db.upsert(synthetic_lead())[0].suppressed
    reopened = Database(config.database)
    assert reopened.get(lead.id).suppressed
    reopened.close()


def test_concurrent_dedup(config):
    Database(config.database).close()

    def insert(_):
        db = Database(config.database)
        try:
            return db.upsert(synthetic_lead())[1]
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(insert, range(8))) == 1


def test_provenance_validation():
    with pytest.raises(ValueError):
        Evidence("technology", "windows", "", "https://synthetic.example", utcnow())
    with pytest.raises(ValueError):
        Evidence("technology", "windows", "Windows", "file:///private", utcnow())
    with pytest.raises(ValueError):
        Evidence("technology", "windows", "Windows", "https://synthetic.example", "2026-01-01")


def test_newer_schema_refused(tmp_path):
    import sqlite3

    path = str(tmp_path / "future.sqlite3")
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA user_version=99")
    connection.close()
    with pytest.raises(ValueError, match="newer"):
        Database(path)


def test_v1_migration_preserves_history_and_aliases(tmp_path):
    import json
    import sqlite3

    from leadagent.database import SCHEMA

    path = str(tmp_path / "legacy.sqlite3")
    old_schema = SCHEMA.replace(
        " subject TEXT NOT NULL DEFAULT '', text_body TEXT NOT NULL DEFAULT '', html_body TEXT NOT NULL DEFAULT ''\n",
        "",
    )
    # Strip the comma before the legacy table terminator.
    old_schema = old_schema.replace(
        " error_type TEXT NOT NULL DEFAULT '',\n);", " error_type TEXT NOT NULL DEFAULT ''\n);"
    )
    connection = sqlite3.connect(path)
    connection.executescript(old_schema)
    connection.execute("DROP TABLE company_aliases")
    lead = synthetic_lead()
    lead.id = 1
    lead.contact_count = 1
    lead.outreach_status = "CONTACTED"
    payload = lead.to_dict()
    payload.pop("initial_delivery_confirmed")
    connection.execute(
        "INSERT INTO leads(id,normalized_domain,company_key,payload,contact_count) VALUES (?,?,?,?,?)",
        (1, "synthetic-1.example", "synthetische firma 1|berlin", json.dumps(payload), 1),
    )
    connection.execute(
        "INSERT INTO domain_aliases VALUES (?,?,?,?)",
        ("synthetic-1.example", 1, lead.website, utcnow()),
    )
    connection.execute(
        "INSERT INTO deliveries(lead_id,kind,mode,state,created_at,updated_at,draft_hash,permission_status,permission_basis,approved_by,recipient,message_id) VALUES (1,'initial','LIVE','ACCEPTED',?,?,?,?,?,?,?,?)",
        (
            utcnow(),
            utcnow(),
            "hash",
            "CONSENTED",
            "Synthetic basis",
            "reviewer",
            lead.public_email,
            "synthetic-message",
        ),
    )
    connection.execute("PRAGMA user_version=1")
    connection.commit()
    connection.close()
    db = Database(path)
    assert db.get(1).contact_count == 1 and db.get(1).initial_delivery_confirmed
    alias = synthetic_lead()
    alias.website = "https://alternative.example"
    assert db.upsert(alias)[1] is False
    assert db.connection.execute("PRAGMA user_version").fetchone()[0] == 3
    assert db.history(1)[0]["permission_basis"] == "Synthetic basis"
    assert "text_body" in db.history(1)[0]
    db.close()


def test_generic_seo_titles_do_not_merge_distinct_law_firms(db):
    from leadagent.models import Lead

    assert company_key("Rechtsanwalt Rastatt", "Rastatt") == ""
    first, created = db.upsert(
        Lead("Rechtsanwalt Rastatt", "https://first-law.example", city="Rastatt")
    )
    assert created
    second, created = db.upsert(
        Lead("Rechtsanwalt Rastatt", "https://second-law.example", city="Rastatt")
    )
    assert created and first.id != second.id


def test_v2_sent_copy_migration_preserves_all_history(tmp_path):
    import json
    import sqlite3

    from leadagent.database import SCHEMA

    path = str(tmp_path / "v2.sqlite3")
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    lead = synthetic_lead()
    lead.id = 1
    lead.do_not_contact = True
    lead.outreach_status = "DO_NOT_CONTACT"
    lead.contact_count = 1
    lead.email_permission_basis = "Synthetic original request"
    original = json.dumps(lead.to_dict())
    connection.execute(
        "INSERT INTO leads(id,normalized_domain,company_key,payload,contact_count) VALUES (1,?,?,?,1)",
        ("synthetic-1.example", company_key(lead.company_name, lead.city), original),
    )
    connection.execute(
        "INSERT INTO domain_aliases VALUES (?,1,?,?)",
        ("synthetic-1.example", lead.website, utcnow()),
    )
    connection.execute(
        "INSERT INTO company_aliases VALUES (?,1,?,?)",
        (company_key(lead.company_name, lead.city), lead.website, utcnow()),
    )
    connection.execute(
        "INSERT INTO audit(lead_id,action,at,actor,detail) VALUES (1,'PERMISSION',?,'reviewer','Synthetic original request')",
        (utcnow(),),
    )
    connection.execute(
        "INSERT INTO deliveries(lead_id,kind,mode,state,created_at,updated_at,draft_hash,permission_status,permission_basis,approved_by,recipient,message_id) VALUES (1,'initial','LIVE','ACCEPTED',?,?,?,?,?,?,?,?)",
        (
            utcnow(),
            utcnow(),
            "synthetic-hash",
            "CONSENTED",
            "Synthetic original request",
            "reviewer",
            lead.public_email,
            "<original@synthetic.example>",
        ),
    )
    connection.execute("PRAGMA user_version=2")
    connection.commit()
    connection.close()
    db = Database(path)
    assert db.connection.execute("PRAGMA user_version").fetchone()[0] == 3
    assert db.connection.execute("SELECT payload FROM leads").fetchone()[0] == original
    assert db.get(1).suppressed and db.get(1).contact_count == 1
    assert db.history(1)[0]["state"] == "ACCEPTED"
    assert db.history(1)[0]["permission_basis"] == "Synthetic original request"
    assert db.history(1)[0]["sent_copy_status"] == "UNAVAILABLE"
    assert db.connection.execute("SELECT COUNT(*) FROM audit").fetchone()[0] == 1
    assert db.connection.execute("SELECT COUNT(*) FROM domain_aliases").fetchone()[0] == 1
    db.close()
