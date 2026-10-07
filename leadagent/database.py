from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

from .identity import company_key, normalize_domain, normalize_name
from .models import Lead, instant, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
 id INTEGER PRIMARY KEY, normalized_domain TEXT NOT NULL UNIQUE,
 company_key TEXT NOT NULL, payload TEXT NOT NULL,
 qualified_at TEXT NOT NULL DEFAULT '', contact_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_leads_company ON leads(company_key);
CREATE INDEX IF NOT EXISTS idx_leads_qualified ON leads(qualified_at);
CREATE TABLE IF NOT EXISTS company_aliases (
 company_key TEXT PRIMARY KEY, lead_id INTEGER NOT NULL REFERENCES leads(id),
 source_url TEXT NOT NULL, retrieved_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS domain_aliases (
 domain TEXT PRIMARY KEY, lead_id INTEGER NOT NULL REFERENCES leads(id),
 source_url TEXT NOT NULL, retrieved_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deliveries (
 id INTEGER PRIMARY KEY, lead_id INTEGER NOT NULL REFERENCES leads(id),
 kind TEXT NOT NULL CHECK(kind IN ('initial', 'followup')),
 mode TEXT NOT NULL CHECK(mode IN ('LIVE', 'DRY_RUN', 'MANUAL')),
 state TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 draft_hash TEXT NOT NULL, permission_status TEXT NOT NULL, permission_basis TEXT NOT NULL,
 approved_by TEXT NOT NULL, recipient TEXT NOT NULL, message_id TEXT NOT NULL,
 error_type TEXT NOT NULL DEFAULT '',
 subject TEXT NOT NULL DEFAULT '', text_body TEXT NOT NULL DEFAULT '', html_body TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_contact ON deliveries(lead_id, kind)
 WHERE mode IN ('LIVE', 'MANUAL');
CREATE TABLE IF NOT EXISTS audit (
 id INTEGER PRIMARY KEY, lead_id INTEGER REFERENCES leads(id),
 action TEXT NOT NULL, at TEXT NOT NULL, actor TEXT NOT NULL, detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
 id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, completed_at TEXT NOT NULL DEFAULT '',
 provider TEXT NOT NULL, summary TEXT NOT NULL DEFAULT ''
);
"""


class Database:
    def __init__(self, path: str) -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=30, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA busy_timeout=30000")
        version = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
        if version > 4:
            raise ValueError("Database schema is newer than this application")
        if version < 1:
            self.connection.executescript(
                "BEGIN IMMEDIATE;\n" + SCHEMA + "\nPRAGMA user_version=2;\nCOMMIT;"
            )
        elif version == 1:
            # Upgrade early v1 databases without discarding identity or delivery history.
            with self.transaction():
                self.connection.execute(
                    "CREATE TABLE IF NOT EXISTS company_aliases (company_key TEXT PRIMARY KEY, lead_id INTEGER NOT NULL REFERENCES leads(id), source_url TEXT NOT NULL, retrieved_at TEXT NOT NULL)"
                )
                columns = {
                    row[1] for row in self.connection.execute("PRAGMA table_info(deliveries)")
                }
                for column in ("subject", "text_body", "html_body"):
                    if column not in columns:
                        self.connection.execute(
                            f"ALTER TABLE deliveries ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
                        )
                for lead in self.all():
                    key = company_key(lead.company_name, lead.city)
                    if key:
                        self.connection.execute(
                            "INSERT OR IGNORE INTO company_aliases VALUES (?,?,?,?)",
                            (key, lead.id, lead.website, utcnow()),
                        )
                    accepted = self.connection.execute(
                        "SELECT 1 FROM deliveries WHERE lead_id=? AND kind='initial' AND ((mode='LIVE' AND state='ACCEPTED') OR (mode='MANUAL' AND state='RECORDED'))",
                        (lead.id,),
                    ).fetchone()
                    lead.initial_delivery_confirmed = bool(accepted)
                    self.save(lead)
                self.connection.execute("PRAGMA user_version=2")

        version = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
        if version < 3:
            with self.transaction():
                if int(self.connection.execute("PRAGMA user_version").fetchone()[0]) < 3:
                    for definition in (
                        "rfc822 BLOB NOT NULL DEFAULT X''",
                        "sent_copy_status TEXT NOT NULL DEFAULT 'NOT_REQUIRED'",
                        "sent_folder TEXT NOT NULL DEFAULT ''",
                        "sent_copied_at TEXT NOT NULL DEFAULT ''",
                        "sent_copy_error TEXT NOT NULL DEFAULT ''",
                    ):
                        self.connection.execute(f"ALTER TABLE deliveries ADD COLUMN {definition}")
                    self.connection.execute(
                        "UPDATE deliveries SET sent_copy_status='UNAVAILABLE' WHERE mode='LIVE' AND state='ACCEPTED'"
                    )
                    self.connection.execute("PRAGMA user_version=3")

        if int(self.connection.execute("PRAGMA user_version").fetchone()[0]) < 4:
            with self.transaction():
                if int(self.connection.execute("PRAGMA user_version").fetchone()[0]) < 4:
                    # Rebuild only the delivery table to widen its kind constraint. IDs,
                    # wire bytes, permission audit and Sent-copy repair state survive.
                    definition = self.connection.execute(
                        "SELECT sql FROM sqlite_master WHERE type='table' AND name='deliveries'"
                    ).fetchone()[0]
                    definition = definition.replace(
                        "CREATE TABLE deliveries", "CREATE TABLE deliveries_v4", 1
                    )
                    definition = definition.replace(
                        "'initial', 'followup'", "'initial', 'followup', 'customer_checkin'"
                    )
                    self.connection.execute(definition)
                    self.connection.execute("INSERT INTO deliveries_v4 SELECT * FROM deliveries")
                    self.connection.execute("DROP TABLE deliveries")
                    self.connection.execute("ALTER TABLE deliveries_v4 RENAME TO deliveries")
                    self.connection.execute(
                        "ALTER TABLE deliveries ADD COLUMN customer_period TEXT NOT NULL DEFAULT ''"
                    )
                    self.connection.execute(
                        "CREATE UNIQUE INDEX idx_one_contact ON deliveries(lead_id,kind) WHERE mode IN ('LIVE','MANUAL') AND kind IN ('initial','followup')"
                    )
                    self.connection.execute(
                        "CREATE UNIQUE INDEX idx_customer_period ON deliveries(lead_id,customer_period) WHERE mode='LIVE' AND kind='customer_checkin'"
                    )
                    for lead in self.all():
                        if lead.customer or lead.outreach_status == "CUSTOMER":
                            row = self.connection.execute(
                                "SELECT at FROM audit WHERE lead_id=? AND action='CUSTOMER' ORDER BY id LIMIT 1",
                                (lead.id,),
                            ).fetchone()
                            at = row[0] if row else utcnow()
                            lead.customer = True
                            lead.customer_since = at
                            lead.next_customer_checkin_at = (
                                instant(at) + timedelta(days=90)
                            ).isoformat()
                            self.save(lead)
                            self.audit(
                                lead.id,
                                "CUSTOMER_CREATED",
                                "migration",
                                "Existing customer schedule initialized",
                            )
                    self.connection.execute("PRAGMA user_version=4")

    def close(self) -> None:
        self.connection.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    def get(self, lead_id: int) -> Lead:
        row = self.connection.execute("SELECT payload FROM leads WHERE id=?", (lead_id,)).fetchone()
        if row is None:
            raise ValueError(f"Lead {lead_id} not found")
        return Lead.from_dict(json.loads(row["payload"]))

    def all(self) -> list[Lead]:
        return [
            Lead.from_dict(json.loads(row[0]))
            for row in self.connection.execute("SELECT payload FROM leads ORDER BY id")
        ]

    def save(self, lead: Lead) -> None:
        if lead.id is None:
            raise ValueError("Insert lead before saving")
        self.connection.execute(
            "UPDATE leads SET payload=?, qualified_at=?, contact_count=? WHERE id=?",
            (
                json.dumps(lead.to_dict(), ensure_ascii=False),
                lead.qualified_at,
                lead.contact_count,
                lead.id,
            ),
        )

    def upsert(self, lead: Lead, aliases: list[tuple[str, str]] | None = None) -> tuple[Lead, bool]:
        if not normalize_name(lead.company_name):
            raise ValueError("A meaningful company name is required")
        lead.normalized_domain = normalize_domain(lead.website)
        lead.domain = lead.normalized_domain
        lead.normalized_company_name = normalize_name(lead.company_name)
        key = company_key(lead.company_name, lead.city)
        domains = [(lead.normalized_domain, lead.website)]
        if lead.public_email and "@" in lead.public_email:
            email_domain = normalize_domain(lead.public_email.rsplit("@", 1)[1])
            domains.append((email_domain, lead.website))
        domains.extend((normalize_domain(d), source) for d, source in aliases or [])
        with self.transaction():
            ids = set()
            for domain, _ in domains:
                row = self.connection.execute(
                    "SELECT lead_id FROM domain_aliases WHERE domain=?", (domain,)
                ).fetchone()
                if row:
                    ids.add(int(row[0]))
            if key:
                ids.update(
                    int(row[0])
                    for row in self.connection.execute(
                        "SELECT lead_id FROM company_aliases WHERE company_key=?", (key,)
                    )
                )
            if len(ids) > 1:
                raise ValueError("Conflicting company aliases require human identity review")
            if ids:
                existing = self.get(ids.pop())
                # Preserve all contact/permission/suppression history. Refresh research only.
                fields = (
                    "segment",
                    "street_address_if_public",
                    "postal_code",
                    "city",
                    "country",
                    "estimated_company_size",
                    "company_size_evidence",
                    "public_email",
                    "contact_page",
                    "last_researched_at",
                    "services_detected",
                    "technologies_detected",
                    "security_services_detected",
                    "opportunity_signals",
                    "risk_disqualification_signals",
                    "fit_score",
                    "intent_score",
                    "final_score",
                    "score_reasons",
                    "recommended_dsc_service",
                    "recommended_pitch_angle",
                )
                if lead.last_researched_at:
                    for name in fields:
                        setattr(existing, name, getattr(lead, name))
                    seen = {
                        (e.kind, e.value, e.source_url, e.retrieved_at) for e in existing.evidence
                    }
                    existing.evidence.extend(
                        e
                        for e in lead.evidence
                        if (e.kind, e.value, e.source_url, e.retrieved_at) not in seen
                    )
                    existing.source_urls = sorted(set(existing.source_urls + lead.source_urls))
                    if existing.approved_draft_hash:
                        existing.approved_draft_hash = ""
                        self.audit(existing.id, "RESEARCH_INVALIDATED_APPROVAL", "system")
                    self.save(existing)
                result, created = existing, False
            else:
                cursor = self.connection.execute(
                    "INSERT INTO leads(normalized_domain, company_key, payload) VALUES (?, ?, '{}')",
                    (lead.normalized_domain, key),
                )
                lead.id = cursor.lastrowid
                self.save(lead)
                result, created = lead, True
                self.audit(lead.id, "DISCOVERED", "system")
            if key:
                self.connection.execute(
                    "INSERT OR IGNORE INTO company_aliases VALUES (?, ?, ?, ?)",
                    (key, result.id, lead.website, utcnow()),
                )
            for domain, source in domains:
                self.connection.execute(
                    "INSERT OR IGNORE INTO domain_aliases VALUES (?, ?, ?, ?)",
                    (domain, result.id, source, utcnow()),
                )
            return result, created

    def audit(self, lead_id: int | None, action: str, actor: str, detail: str = "") -> None:
        self.connection.execute(
            "INSERT INTO audit(lead_id,action,at,actor,detail) VALUES (?,?,?,?,?)",
            (lead_id, action, utcnow(), actor, detail),
        )

    def history(self, lead_id: int) -> list[dict[str, Any]]:
        return [
            {key: value for key, value in dict(row).items() if key != "rfc822"}
            for row in self.connection.execute(
                "SELECT * FROM deliveries WHERE lead_id=? ORDER BY id",
                (lead_id,),
            )
        ]
