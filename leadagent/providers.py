from __future__ import annotations

import json
import math
import os
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .config import DiscoveryConfig
from .identity import normalize_domain
from .models import Lead, utcnow


@dataclass
class Candidate:
    company_name: str
    website: str
    source_url: str
    retrieved_at: str
    city: str = ""
    latitude: float | None = None
    longitude: float | None = None
    # Only fixture providers may directly supply evidence. Web providers research independently.
    fixture: dict[str, Any] | None = None
    aliases: list[tuple[str, str]] = field(default_factory=list)
    discovery_sources: list[tuple[str, str]] = field(default_factory=list)


class DiscoveryProvider(Protocol):
    name: str

    def discover(self, limit: int) -> Iterable[Candidate]: ...


class SeedProvider:
    name = "seeds"

    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def discover(self, limit: int) -> Iterable[Candidate]:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("Seeds must be a JSON list of public website URLs")
        for item in data[:limit]:
            if not isinstance(item, dict) or not isinstance(item.get("website"), str):
                raise ValueError(
                    "Each seed requires website; company name and city are optional hints"
                )
            yield Candidate(
                item.get("company_name", ""),
                item["website"],
                str(item.get("source_url") or item["website"]),
                utcnow(),
                item.get("city", ""),
            )


class FixtureProvider:
    name = "fixture"

    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def discover(self, limit: int) -> Iterable[Candidate]:
        for data in json.loads(self.path.read_text(encoding="utf-8"))[:limit]:
            lead = Lead.from_dict(data)
            urls = [lead.website, *lead.source_urls, *(e.source_url for e in lead.evidence)]
            if any(not normalize_domain(url).endswith(".example") for url in urls) or (
                lead.public_email and not lead.public_email.endswith(".example")
            ):
                raise ValueError(
                    "Fixture provider only accepts reserved .example synthetic domains"
                )
            at = utcnow()
            lead.first_seen_at = at
            lead.last_researched_at = at
            for evidence in lead.evidence:
                evidence.retrieved_at = at
            data = lead.to_dict()
            yield Candidate(lead.company_name, lead.website, lead.website, utcnow(), fixture=data)


class BraveSearchProvider:
    """Official Brave Search API; never scrape a search result HTML page."""

    name = "brave"

    def __init__(self, config: DiscoveryConfig) -> None:
        self.config = config
        self.source_results: dict[str, dict[str, str | int]] = {}

    def discover(self, limit: int) -> Iterable[Candidate]:
        seen: set[str] = set()
        try:
            for candidate in self._discover(limit):
                seen.add(normalize_domain(candidate.website))
                yield candidate
        except (ValueError, OSError, TypeError, KeyError, AttributeError) as exc:
            self.source_results["brave"] = {"status": "skipped", "error": type(exc).__name__}
            from .free_providers import FreeDiscoveryProvider

            free = FreeDiscoveryProvider(self.config)
            for candidate in free.discover(limit):
                domain = normalize_domain(candidate.website)
                if domain not in seen:
                    seen.add(domain)
                    yield candidate
                if len(seen) >= limit:
                    break
            self.source_results.update(free.source_results)

    def _discover(self, limit: int) -> Iterable[Candidate]:
        key = os.environ.get("DSC_BRAVE_API_KEY")
        if not key or not self.config.queries:
            raise ValueError("Optional Brave credentials/queries unavailable")
        count = 0
        seen: set[str] = set()
        regions = self.config.query_regions
        region = regions[datetime.now(UTC).date().toordinal() % len(regions)] if regions else ""
        per_query = (
            max(1, math.ceil(limit / max(1, len(self.config.queries))))
            if self.config.location
            else 20
        )
        for configured_query in self.config.queries:
            query = f"{configured_query} {region}".strip()
            if count >= limit:
                break
            time.sleep(self.config.request_interval_seconds)
            url = "https://api.search.brave.com/res/v1/web/search?" + urlencode(
                {
                    "q": query,
                    "country": "DE",
                    "search_lang": "de",
                    "count": min(20, per_query, limit - count),
                }
            )
            request = Request(
                url,
                headers={
                    "X-Subscription-Token": key,
                    "Accept": "application/json",
                    "User-Agent": self.config.user_agent,
                },
            )
            with urlopen(request, timeout=self.config.timeout_seconds) as response:
                body = response.read(self.config.max_response_bytes + 1)
            if len(body) > self.config.max_response_bytes:
                raise ValueError("Search API response exceeds configured bound")
            results = json.loads(body).get("web", {}).get("results", [])
            for result in results[:per_query]:
                website = result.get("url", "")
                if not website.startswith(("http://", "https://")) or website in seen:
                    continue
                seen.add(website)
                count += 1
                yield Candidate("", website, website, utcnow())
                if count >= limit:
                    break


def provider_from_config(config: DiscoveryConfig) -> DiscoveryProvider:
    if config.provider == "free":
        from .free_providers import FreeDiscoveryProvider

        return FreeDiscoveryProvider(config)
    if config.provider == "fixture":
        return FixtureProvider(config.seeds_file)
    if config.provider == "brave":
        return BraveSearchProvider(config)
    return SeedProvider(config.seeds_file)
