from __future__ import annotations

from .config import Config
from .models import Evidence, Lead, ScoreReason, Segment
from .rules import FIT_RULES, INTENT_RULES


def classify(evidence: list[Evidence]) -> str:
    segments = {
        e.value
        for e in evidence
        if e.kind == "segment" and e.value in {s.value for s in Segment if s != Segment.UNKNOWN}
    }
    # Ambiguity is a review item, not a reason to guess.
    return next(iter(segments)) if len(segments) == 1 else Segment.UNKNOWN.value


def match_service(lead: Lead) -> tuple[str, str]:
    explicit_services = {
        "penetration_testing": (
            "Penetration Testing",
            "Scope an explicitly requested technical penetration test",
        ),
        "training": (
            "IT Security Training",
            "Scope explicitly requested security awareness training",
        ),
        "managed_it": (
            "Managed IT & Security",
            "Review an explicitly requested external IT support scope",
        ),
        "backup": (
            "Backup & Recovery Review",
            "Review explicitly requested backup and recovery support",
        ),
        "microsoft_365": (
            "Microsoft 365 Security",
            "Review an explicitly requested Microsoft 365 security scope",
        ),
    }
    need = next(
        (e for e in lead.evidence if e.kind == "service_need" and e.value in explicit_services),
        None,
    )
    if need and lead.segment != Segment.UNKNOWN.value:
        return explicit_services[need.value]
    if lead.segment == Segment.IT.value:
        if {"penetration_testing", "security_assessment", "security_team"} & set(
            lead.security_services_detected
        ):
            return "", "Security specialist already visible; review partnership evidence manually"
        return (
            "Partner Security Assessment",
            "A scoped technical assessment for SMB customers as a potential partner service",
        )
    if lead.segment == Segment.MEDICAL.value:
        if "backup" in lead.services_detected:
            return "Backup & Recovery Review", "Review documented backup and recovery workflows"
        return (
            "Windows Security Assessment",
            "Assess endpoint and identity controls in the practice",
        )
    if lead.segment in {Segment.TAX.value, Segment.LAW.value}:
        if "microsoft_365" in lead.technologies_detected:
            return "Microsoft 365 Security", "Review identity, access and tenant security settings"
        return "Windows Security Assessment", "Prioritize endpoint and access hardening"
    return "", "Insufficient evidence of a supported target segment"


def score(lead: Lead, config: Config) -> Lead:
    lead.segment = classify(lead.evidence)
    lead.services_detected = sorted({e.value for e in lead.evidence if e.kind == "operations"})
    lead.technologies_detected = sorted({e.value for e in lead.evidence if e.kind == "technology"})
    lead.security_services_detected = sorted(
        {e.value for e in lead.evidence if e.kind == "security"}
    )
    lead.opportunity_signals = sorted({e.value for e in lead.evidence if e.kind == "intent"})
    lead.risk_disqualification_signals = sorted(
        {e.value for e in lead.evidence if e.kind == "risk"}
    )
    size = next((e for e in lead.evidence if e.kind == "size"), None)
    if size:
        lead.estimated_company_size, lead.company_size_evidence = size.value, size.excerpt
    lead.recommended_dsc_service, lead.recommended_pitch_angle = match_service(lead)
    reasons: list[ScoreReason] = []
    if lead.segment != Segment.UNKNOWN.value and lead.country == "Germany":
        for rule, (kind, _, reason) in FIT_RULES.items():
            points = config.fit_points[rule]
            ev = next((e for e in lead.evidence if e.kind == kind), None)
            if ev and (rule != "compatibility" or lead.recommended_dsc_service):
                reasons.append(ScoreReason("fit", rule, points, reason, ev))
        for signal, (_, reason) in INTENT_RULES.items():
            points = config.intent_points[signal]
            ev = next((e for e in lead.evidence if e.kind == "intent" and e.value == signal), None)
            if ev:
                reasons.append(ScoreReason("intent", signal, points, reason, ev))
    lead.score_reasons = reasons
    lead.fit_score = min(100, sum(r.points for r in reasons if r.component == "fit"))
    lead.intent_score = min(100, sum(r.points for r in reasons if r.component == "intent"))
    lead.final_score = round(
        lead.fit_score * config.fit_weight + lead.intent_score * (1 - config.fit_weight)
    )
    if lead.risk_disqualification_signals or not lead.recommended_dsc_service:
        lead.final_score = 0
    return lead
