from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .models import Permission, Segment
from .rules import FIT_RULES, INTENT_RULES


@dataclass
class DiscoveryConfig:
    sector: str = ""
    location: str = ""
    provider: str = "free"
    seeds_file: str = "seeds.local.json"
    cache_directory: str = "cache/discovery"
    geo_database: str = "data/geonames-de.json"
    areas: list[str] = field(default_factory=list)
    maximum_candidates: int = 80
    maximum_pages_per_company: int = 5
    request_interval_seconds: float = 2
    timeout_seconds: float = 15
    max_response_bytes: int = 1_000_000
    user_agent: str = "DSCLeadAgent/0.1 (+https://digitalskills-campus.de)"
    allowed_hosts: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    query_regions: list[str] = field(
        default_factory=lambda: [
            "Berlin",
            "Hamburg",
            "München",
            "Köln",
            "Frankfurt",
            "Stuttgart",
            "Düsseldorf",
            "Leipzig",
            "Dresden",
            "Hannover",
            "Bremen",
            "Nürnberg",
            "Bonn",
            "Mannheim",
            "Freiburg",
            "Kiel",
        ]
    )


@dataclass
class MailConfig:
    automatic_sending_enabled: bool = False
    allowed_permission_states: list[str] = field(
        default_factory=lambda: [
            "CONSENTED",
            "REQUESTED_INFORMATION",
            "PUBLIC_BUSINESS_OUTREACH",
        ]
    )
    minimum_interval_seconds: int = 60
    approval_valid_days: int = 7
    evidence_max_age_days: int = 30


@dataclass
class Config:
    country: str = "Germany"
    database: str = "data/leadagent.sqlite3"
    report_directory: str = "reports"
    daily_new_lead_limit: int = 20
    minimum_score: int = 70
    customer_checkin_interval_days: int = 90
    customer_message_type: str = "QUARTERLY_CHECKIN"
    followup_delay_days: int = 10
    maximum_followups: int = 1
    fit_weight: float = 0.7
    fit_points: dict[str, int] = field(
        default_factory=lambda: {key: rule[1] for key, rule in FIT_RULES.items()}
    )
    intent_points: dict[str, int] = field(
        default_factory=lambda: {key: rule[0] for key, rule in INTENT_RULES.items()}
    )
    segments: dict[str, dict[str, int]] = field(
        default_factory=lambda: {
            "medical_practice": {"target": 6},
            "tax_advisory": {"target": 5},
            "law_firm": {"target": 5},
            "it_service_provider": {"target": 4},
        }
    )
    discovery: DiscoveryConfig = field(default_factory=DiscoveryConfig)
    mail: MailConfig = field(default_factory=MailConfig)

    def validate(self) -> None:
        if self.country != "Germany":
            raise ValueError("v1 supports Germany only")
        integer_ranges = {
            "daily_new_lead_limit": (1, 20),
            "minimum_score": (0, 100),
            "followup_delay_days": (1, 3650),
            "customer_checkin_interval_days": (1, 3650),
            "maximum_followups": (0, 1),
        }
        for name, (low, high) in integer_ranges.items():
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{name} must be an integer in {low}..{high}")
        if self.customer_message_type not in {
            "QUARTERLY_CHECKIN",
            "SECURITY_REVIEW",
            "BACKUP_REVIEW",
            "RETEST",
            "GENERAL_SERVICE",
        }:
            raise ValueError("Unsupported customer message type")
        if type(self.fit_weight) not in (int, float) or not 0 <= self.fit_weight <= 1:
            raise ValueError("fit_weight must be 0..1")
        for configured, defaults in (
            (self.fit_points, FIT_RULES),
            (self.intent_points, INTENT_RULES),
        ):
            if not isinstance(configured, dict) or set(configured) != set(defaults):
                raise ValueError("Configure all supported scoring point rules")
            if any(
                type(points) is not int or not 0 <= points <= 100 for points in configured.values()
            ):
                raise ValueError("Scoring points must be integers in 0..100")
        if sum(self.fit_points.values()) > 100:
            raise ValueError("Fit points must sum to at most 100")
        if not isinstance(self.segments, dict):
            raise ValueError("Segments must be a mapping")
        expected = {s.value for s in Segment if s != Segment.UNKNOWN}
        if set(self.segments) != expected:
            raise ValueError("Configure exactly the four supported segments")
        for entry in self.segments.values():
            if (
                not isinstance(entry, dict)
                or set(entry) != {"target"}
                or type(entry["target"]) is not int
                or entry["target"] < 0
            ):
                raise ValueError("Segment target must be a nonnegative integer")
        if not isinstance(self.discovery.sector, str) or self.discovery.sector not in {
            "",
            "law_firm",
            "medical_practice",
            "tax_advisor",
            "it_service_provider",
        }:
            raise ValueError("Unsupported discovery sector")
        if not isinstance(self.discovery.location, str) or any(
            c in self.discovery.location for c in "\r\n"
        ):
            raise ValueError("Invalid discovery location")
        if self.discovery.provider not in {"free", "seeds", "brave", "fixture"}:
            raise ValueError("Unknown discovery provider")
        for name in ("maximum_candidates", "maximum_pages_per_company", "max_response_bytes"):
            value = getattr(self.discovery, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        for value in (self.discovery.request_interval_seconds, self.discovery.timeout_seconds):
            if type(value) not in (float, int) or value <= 0:
                raise ValueError("Discovery timing must be positive")
        if not isinstance(self.discovery.user_agent, str) or not self.discovery.user_agent:
            raise ValueError("Set an identifying user agent")
        for value in (
            self.discovery.allowed_hosts,
            self.discovery.queries,
            self.discovery.query_regions,
            self.discovery.areas,
        ):
            if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
                raise ValueError("Discovery hosts and queries must be string lists")
        if any(len(query) > 550 or len(query.split()) > 70 for query in self.discovery.queries):
            raise ValueError("Search queries must fit API limits, including the appended region")
        if type(self.mail.automatic_sending_enabled) is not bool:
            raise ValueError("automatic_sending_enabled must be boolean")
        allowed = {p.value for p in Permission} - {"UNKNOWN", "PROHIBITED"}
        if not isinstance(self.mail.allowed_permission_states, list) or any(
            not isinstance(p, str) or p not in allowed for p in self.mail.allowed_permission_states
        ):
            raise ValueError("UNKNOWN/PROHIBITED can never be allowed for sending")
        for name in ("minimum_interval_seconds", "approval_valid_days", "evidence_max_age_days"):
            value = getattr(self.mail, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        for value in (
            self.database,
            self.report_directory,
            self.discovery.seeds_file,
            self.discovery.cache_directory,
            self.discovery.geo_database,
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Paths must be nonempty strings")


def load_config(path: str | Path) -> Config:
    with Path(path).open(encoding="utf-8") as handle:
        data: Any = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError("Config must be a YAML mapping")
    values = dict(data)
    try:
        values["discovery"] = DiscoveryConfig(**values.get("discovery", {}))
        values["mail"] = MailConfig(**values.get("mail", {}))
        config = Config(**values)
    except TypeError as exc:
        raise ValueError(f"Invalid config fields: {exc}") from exc
    config.validate()
    return config
