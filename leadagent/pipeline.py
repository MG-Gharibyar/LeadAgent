from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from .config import Config
from .database import Database
from .identity import normalize_domain
from .models import Lead, Status, utcnow
from .outreach import prepare_draft
from .providers import Candidate, DiscoveryProvider
from .scoring import score

logger = logging.getLogger(__name__)


class ResearchProvider(Protocol):
    def research(self, candidate: Candidate) -> tuple[Lead, list[tuple[str, str]]]: ...


@dataclass
class RunResult:
    run_id: int
    discovered: int
    duplicates: int
    errors: int
    qualified_ids: list[int]


def select_new(db: Database, config: Config, day: str) -> list[int]:
    selected: list[int] = []
    with db.transaction():
        already = sum(1 for lead in db.all() if lead.qualified_at.startswith(day))
        remaining = max(0, config.daily_new_lead_limit - already)
        candidates = [
            lead
            for lead in db.all()
            if not lead.qualified_at
            and not lead.contact_count
            and not lead.suppressed
            and lead.outreach_status == Status.DISCOVERED.value
            and lead.final_score >= config.minimum_score
            and lead.recommended_dsc_service
            and lead.country == "Germany"
        ]
        # Quality wins. Distribution preferences only break ties at the same score.
        segment_count: dict[str, int] = {}
        for _ in range(min(remaining, len(candidates))):
            candidates.sort(
                key=lambda lead: (
                    -lead.final_score,
                    -lead.intent_score,
                    segment_count.get(lead.segment, 0)
                    >= config.segments.get(lead.segment, {}).get("target", 0),
                    lead.id or 0,
                )
            )
            lead = candidates.pop(0)
            try:
                prepare_draft(lead)
            except ValueError:
                continue
            lead.qualified_at = utcnow()
            db.save(lead)
            db.audit(lead.id, "QUALIFIED", "system", f"score={lead.final_score}")
            assert lead.id is not None
            selected.append(lead.id)
            segment_count[lead.segment] = segment_count.get(lead.segment, 0) + 1
    return selected


def discover(
    db: Database, config: Config, provider: DiscoveryProvider, researcher: ResearchProvider
) -> RunResult:
    cursor = db.connection.execute(
        "INSERT INTO runs(started_at,provider) VALUES (?,?)", (utcnow(), provider.name)
    )
    run_id = cursor.lastrowid
    assert run_id is not None
    result = RunResult(run_id, 0, 0, 0, [])
    try:
        for index, candidate in enumerate(provider.discover(config.discovery.maximum_candidates)):
            if index >= config.discovery.maximum_candidates:
                break
            try:
                domain = normalize_domain(candidate.website)
                known = db.connection.execute(
                    "SELECT lead_id FROM domain_aliases WHERE domain=?", (domain,)
                ).fetchone()
                if known:
                    existing = db.get(known[0])
                    if existing.suppressed or existing.contact_count:
                        result.duplicates += 1
                        continue
                lead, aliases = researcher.research(candidate)
                score(lead, config)
                _, created = db.upsert(lead, aliases)
                if created:
                    result.discovered += 1
                else:
                    result.duplicates += 1
            except (ValueError, OSError) as exc:
                result.errors += 1
                # No prospect names, URLs or response body in application logs.
                logger.warning(
                    "candidate_research_failed",
                    extra={"error_type": type(exc).__name__, "run_id": run_id},
                )
        day = datetime.now(UTC).date().isoformat()
        result.qualified_ids = select_new(db, config, day)
    finally:
        db.connection.execute(
            "UPDATE runs SET completed_at=?,summary=? WHERE id=?",
            (
                utcnow(),
                json.dumps(
                    {
                        "discovered": result.discovered,
                        "duplicates": result.duplicates,
                        "errors": result.errors,
                        "qualified": len(result.qualified_ids),
                    }
                ),
                run_id,
            ),
        )
    return result
