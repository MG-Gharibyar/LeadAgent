from __future__ import annotations

import csv
import os
import tempfile
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TextIO

from .config import Config
from .database import Database
from .mail import blockers
from .models import Lead
from .outreach import STOP_STATUSES


def action(lead: Lead, config: Config) -> str:
    if lead.suppressed:
        return "BLOCK"
    if lead.outreach_status in STOP_STATUSES or lead.final_score < config.minimum_score:
        return "SKIP"
    if not blockers(lead, config) and config.mail.automatic_sending_enabled:
        return "SEND"
    if lead.contact_count and lead.draft_kind == "initial":
        return "SKIP"
    return "REVIEW"


def safe_cell(value: object) -> str:
    text = str(value)
    # Spreadsheet formula injection protection, including leading whitespace.
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


def md(value: str) -> str:
    return value.replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;")


def _atomic(path: Path, writer: Callable[[TextIO], None]) -> None:
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".report-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            writer(handle)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def generate_report(db: Database, config: Config, day: str | None = None) -> tuple[Path, Path]:
    day = day or datetime.now(UTC).date().isoformat()
    date.fromisoformat(day)
    leads = sorted(
        (
            lead
            for lead in db.all()
            if lead.qualified_at.startswith(day) and lead.final_score >= config.minimum_score
        ),
        key=lambda lead: (-lead.final_score, -lead.intent_score, lead.id or 0),
    )
    directory = Path(config.report_directory)
    directory.mkdir(parents=True, exist_ok=True)
    markdown = directory / f"{day}-leads.md"
    csv_path = directory / f"{day}-leads.csv"
    parts = [
        f"# DSC LeadAgent — {day}\n",
        f"{len(leads)} qualified new companies. Drafts require human and permission review.\n",
    ]
    for rank, lead in enumerate(leads, 1):
        parts.extend(
            [
                f"## {rank}. {md(lead.company_name)} (ID {lead.id})\n",
                f"Segment: {lead.segment} | City: {md(lead.city)} | Website: {lead.website}\n",
                f"Final score: **{lead.final_score}** | Fit: {lead.fit_score} | Intent: {lead.intent_score}\n",
                "### Why it fits DSC / evidence\n",
            ]
        )
        for reason in lead.score_reasons:
            parts.append(
                f"- {reason.component} +{reason.points}: {reason.reason}. Evidence: “{md(reason.evidence.excerpt)}” — {reason.evidence.source_url} (retrieved {reason.evidence.retrieved_at})\n"
            )
        security = (
            ", ".join(lead.security_services_detected)
            or "None detected in reviewed pages; absence is not established"
        )
        parts.extend(
            [
                f"\nDetected services: {', '.join(lead.services_detected) or 'Unknown'}\n",
                f"Technologies: {', '.join(lead.technologies_detected) or 'Unknown'}\n",
                f"Visible security services: {security}\n",
                f"Security gap/opportunity: {lead.recommended_pitch_angle}. A technical deficiency has not been established.\n",
                f"Recommended DSC service: **{lead.recommended_dsc_service}**\n",
                f"Contact route: {lead.public_email or lead.contact_page or 'No verified written route; review'}\n",
                f"Permission: {lead.email_permission_status}; basis: {md(lead.email_permission_basis) or 'Not documented'}\n",
                f"Suggested subject: {md(lead.draft_subject)}\n",
                "### Suggested email\n",
                "\n".join(("> " + md(line)).rstrip() for line in lead.draft_text.splitlines())
                + "\n",
                "\n### Source URLs\n",
            ]
        )
        parts.extend(f"- {url}\n" for url in lead.source_urls)
        parts.append(
            f"\nPrevious contact history: {lead.contact_count} contacts; {lead.followup_count} follow-ups; first={lead.first_contact_at or 'never'}; last={lead.last_contact_at or 'never'}\n"
        )
        for delivery in db.history(lead.id or 0):
            parts.append(
                f"- {delivery['created_at']}: {delivery['kind']} / {delivery['mode']} / {delivery['state']}\n"
            )
        parts.append(f"\nRecommended action: **{action(lead, config)}**\n")
        parts.append("Gate details: " + "; ".join(blockers(lead, config)) + "\n\n")

    def write_markdown(handle: TextIO) -> None:
        handle.write("\n".join(parts).rstrip() + "\n")

    _atomic(markdown, write_markdown)

    def write_csv(handle: TextIO) -> None:
        fields = [
            "rank",
            "id",
            "company",
            "segment",
            "city",
            "website",
            "final_score",
            "fit_score",
            "intent_score",
            "service",
            "contact_route",
            "permission",
            "subject",
            "email",
            "source_urls",
            "previous_contacts",
            "action",
        ]
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(fields)
        for rank, lead in enumerate(leads, 1):
            values: list[object] = [
                rank,
                lead.id,
                lead.company_name,
                lead.segment,
                lead.city,
                lead.website,
                lead.final_score,
                lead.fit_score,
                lead.intent_score,
                lead.recommended_dsc_service,
                lead.public_email or lead.contact_page,
                lead.email_permission_status,
                lead.draft_subject,
                lead.draft_text,
                " | ".join(lead.source_urls),
                lead.contact_count,
                action(lead, config),
            ]
            writer.writerow([safe_cell(value) for value in values])

    _atomic(csv_path, write_csv)
    return markdown, csv_path


def generate_discovery_report(
    db: Database, config: Config, new_ids: list[int], run_id: int
) -> Path:
    """Private review report including unqualified candidates, never a send authorization."""
    directory = Path(config.report_directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"discovery-{run_id}-review.md"
    leads = sorted(
        (db.get(lead_id) for lead_id in new_ids), key=lambda lead: (-lead.final_score, lead.id or 0)
    )
    lines = [
        f"# Discovery review — run {run_id}",
        "No mail sent. Candidates below policy threshold remain in review.",
    ]
    for lead in leads:
        lines.extend(
            [
                f"\n## {md(lead.company_name)} (ID {lead.id})",
                f"City: {md(lead.city)} | Postal code: {lead.postal_code} | Website: {lead.website}",
                f"Domain: {lead.normalized_domain} | Public business email: {lead.public_email or 'Not verified'}",
                f"Sector: {lead.segment} | Score: {lead.final_score} | Qualified: {bool(lead.qualified_at)}",
                f"Status: {lead.outreach_status} | Previous contacts: {lead.contact_count}",
                f"Review risks: {', '.join(lead.risk_disqualification_signals) or 'None detected'}",
            ]
        )
        for reason in lead.score_reasons:
            lines.append(
                f"- {reason.reason}: “{md(reason.evidence.excerpt)}” — {reason.evidence.source_url}"
            )
        lines.append("Sources: " + ", ".join(lead.source_urls))

    def write_review(handle: TextIO) -> None:
        handle.write("\n".join(lines) + "\n")

    _atomic(path, write_review)
    return path
