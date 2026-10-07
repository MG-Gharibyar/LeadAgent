from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


def instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Timestamps must include a timezone")
    return parsed.astimezone(UTC)


class Segment(StrEnum):
    MEDICAL = "medical_practice"
    TAX = "tax_advisory"
    LAW = "law_firm"
    IT = "it_service_provider"
    UNKNOWN = "unknown"


class Status(StrEnum):
    DISCOVERED = "DISCOVERED"
    QUALIFIED = "QUALIFIED"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    APPROVED = "APPROVED"
    CONTACTED = "CONTACTED"
    FOLLOWUP_DUE = "FOLLOWUP_DUE"
    FOLLOWUP_SENT = "FOLLOWUP_SENT"
    RESPONDED = "RESPONDED"
    INTERESTED = "INTERESTED"
    REJECTED = "REJECTED"
    DO_NOT_CONTACT = "DO_NOT_CONTACT"
    CUSTOMER = "CUSTOMER"
    UNSUBSCRIBED = "UNSUBSCRIBED"


class Permission(StrEnum):
    UNKNOWN = "UNKNOWN"
    CONSENTED = "CONSENTED"
    REQUESTED_INFORMATION = "REQUESTED_INFORMATION"
    EXISTING_RELATIONSHIP = "EXISTING_RELATIONSHIP"
    OTHER_DOCUMENTED_BASIS = "OTHER_DOCUMENTED_BASIS"
    PROHIBITED = "PROHIBITED"


@dataclass
class Evidence:
    kind: str
    value: str
    excerpt: str
    source_url: str
    retrieved_at: str

    def __post_init__(self) -> None:
        if not self.excerpt.strip() or not self.source_url.startswith(("https://", "http://")):
            raise ValueError("Evidence requires a public source URL and a nonempty excerpt")
        instant(self.retrieved_at)


@dataclass
class ScoreReason:
    component: str
    rule: str
    points: int
    reason: str
    evidence: Evidence


@dataclass
class Lead:
    company_name: str
    website: str
    id: int | None = None
    normalized_company_name: str = ""
    domain: str = ""
    normalized_domain: str = ""
    segment: str = Segment.UNKNOWN.value
    street_address_if_public: str = ""
    postal_code: str = ""
    city: str = ""
    country: str = ""
    campaign_region: str = ""
    latitude: float | None = None
    longitude: float | None = None
    distance_km: float | None = None
    search_areas: list[str] = field(default_factory=list)
    location_resolution_source: str = ""
    location_review_required: bool = False
    estimated_company_size: str = "unknown"
    company_size_evidence: str = ""
    services_detected: list[str] = field(default_factory=list)
    technologies_detected: list[str] = field(default_factory=list)
    security_services_detected: list[str] = field(default_factory=list)
    opportunity_signals: list[str] = field(default_factory=list)
    risk_disqualification_signals: list[str] = field(default_factory=list)
    contact_role: str = "public business contact"
    public_contact_name_if_relevant: str = ""
    public_email: str = ""
    contact_page: str = ""
    public_phone_if_present: str = ""
    fit_score: int = 0
    intent_score: int = 0
    final_score: int = 0
    score_reasons: list[ScoreReason] = field(default_factory=list)
    recommended_dsc_service: str = ""
    recommended_pitch_angle: str = ""
    source_urls: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    first_seen_at: str = field(default_factory=utcnow)
    last_researched_at: str = ""
    outreach_status: str = Status.DISCOVERED.value
    first_contact_at: str = ""
    last_contact_at: str = ""
    next_contact_allowed_at: str = ""
    initial_delivery_confirmed: bool = False
    contact_count: int = 0
    followup_count: int = 0
    do_not_contact: bool = False
    rejected: bool = False
    customer: bool = False
    customer_since: str | None = None
    last_customer_checkin_at: str | None = None
    next_customer_checkin_at: str | None = None
    customer_opt_out: bool = False
    email_disabled: bool = False
    notes: str = ""
    email_permission_status: str = Permission.UNKNOWN.value
    email_permission_basis: str = ""
    email_permission_recorded_at: str = ""
    qualified_at: str = ""
    draft_subject: str = ""
    draft_text: str = ""
    draft_html: str = ""
    draft_kind: str = "initial"
    draft_evidence_urls: list[str] = field(default_factory=list)
    draft_evidence: list[Evidence] = field(default_factory=list)
    approved_draft_hash: str = ""
    approved_at: str = ""
    approved_by: str = ""
    approved_permission_status: str = ""
    approved_permission_basis: str = ""

    @property
    def suppressed(self) -> bool:
        return (
            self.do_not_contact
            or self.rejected
            or self.customer
            or self.email_permission_status == Permission.PROHIBITED.value
            or self.outreach_status
            in {Status.REJECTED.value, Status.DO_NOT_CONTACT.value, Status.CUSTOMER.value}
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Lead:
        values = dict(data)
        values["evidence"] = [Evidence(**e) for e in values.get("evidence", [])]
        values["draft_evidence"] = [Evidence(**e) for e in values.get("draft_evidence", [])]
        values["score_reasons"] = [
            ScoreReason(**{**r, "evidence": Evidence(**r["evidence"])})
            for r in values.get("score_reasons", [])
        ]
        return cls(**values)
