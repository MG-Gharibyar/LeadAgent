"""Free, bounded discovery. Directory hints never constitute qualification evidence."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Iterable
from dataclasses import asdict, replace
from itertools import islice
from pathlib import Path
from typing import Protocol
from urllib.parse import urlencode, urlsplit

from .config import DiscoveryConfig
from .identity import normalize_domain
from .providers import Candidate, DiscoveryProvider, SeedProvider
from .research import Document
from .web import Page, PublicWebClient

logger = logging.getLogger(__name__)


class DirectoryClient(Protocol):
    def fetch(self, url: str) -> Page: ...


class CachedDirectoryClient:
    """Cache successful bounded responses for a day; never cache access failures.

    Cached hints do not replace current company research. Cache lives outside SQLite
    and may be removed without affecting identity, suppression or delivery history.
    """

    def __init__(self, config: DiscoveryConfig) -> None:
        self.client = PublicWebClient(
            replace(config, request_interval_seconds=max(5, config.request_interval_seconds))
        )
        self.directory = Path(config.cache_directory)
        self.max_bytes = config.max_response_bytes

    def fetch(self, url: str) -> Page:
        path = self.directory / (hashlib.sha256(url.encode()).hexdigest() + ".json")
        try:
            if (
                time.time() - path.stat().st_mtime < 86400
                and path.stat().st_size <= self.max_bytes * 6
            ):
                cached = json.loads(path.read_text())
                if cached["url"] == url and len(cached["text"].encode()) <= self.max_bytes * 4:
                    return Page(**cached)
        except (OSError, ValueError, KeyError, TypeError):
            pass
        page = self.client.fetch(url)
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(asdict(page)), encoding="utf-8")
            temporary.replace(path)
        except OSError:
            logger.warning("discovery_cache_write_failed")
        return page


OSM_FILTERS = {
    "law_firm": ['["office"="lawyer"]'],
    "medical_practice": ['["amenity"~"^(doctors|dentist|clinic)$"]'],
    "tax_advisor": ['["office"~"^(tax_advisor|accountant)$"]'],
    "it_service_provider": ['["office"="it"]'],
}


class OpenStreetMapDirectory:
    """OpenStreetMap contributors, ODbL; Private.coffee permits commercial API use."""

    name = "openstreetmap"

    def __init__(self, config: DiscoveryConfig, client: DirectoryClient) -> None:
        self.config, self.client = config, client

    def discover(self, limit: int) -> Iterable[Candidate]:
        # One regional query covers all requested towns and additional nearby towns.
        if self.config.location.casefold() == "karlsruhe":
            prefix, scope = "", "(around:35000,49.0069,8.4037)"
        else:
            city = self.config.location
            if not city:
                regions = self.config.query_regions or ["Berlin"]
                city = regions[int(time.time() // 86400) % len(regions)]
            prefix = f'area["boundary"="administrative"]["name"={json.dumps(city, ensure_ascii=False)}]->.a;'
            scope = "(area.a)"
        filters = (
            OSM_FILTERS[self.config.sector]
            if self.config.sector
            else [f for values in OSM_FILTERS.values() for f in values]
        )
        selections = "".join(f"nwr{f}{scope};" for f in filters)
        query = f"[out:json][timeout:10][maxsize:16777216];{prefix}({selections});out tags 500;"
        url = "https://overpass.private.coffee/api/interpreter?" + urlencode({"data": query})
        page = self.client.fetch(url)
        data = json.loads(page.text)
        if data.get("remark"):
            raise ValueError("Incomplete directory response")
        count = 0
        for item in data.get("elements", []):
            tags = item.get("tags", {})
            website = tags.get("website") or tags.get("contact:website") or ""
            if website and not website.startswith(("http://", "https://")):
                website = "https://" + website
            if (
                not website
                or not tags.get("name")
                or item.get("type") not in {"node", "way", "relation"}
            ):
                continue
            if not isinstance(item.get("id"), int):
                continue
            yield Candidate(
                tags["name"],
                website,
                f"https://www.openstreetmap.org/{item['type']}/{item['id']}",
                page.retrieved_at,
                tags.get("addr:city", ""),
            )
            count += 1
            if count >= limit:
                break


class LawAssociationDirectory:
    """Public local association listing; no member search, login or personal enrichment."""

    name = "karlsruhe_law_association"
    url = "https://anwaltsverein-karlsruhe.de/de/ausbildungsbereite-kanzleien"

    def __init__(self, client: DirectoryClient) -> None:
        self.client = client

    def discover(self, limit: int) -> Iterable[Candidate]:
        page = self.client.fetch(self.url)
        doc = Document(page.text, page.url)
        count = 0
        for website in dict.fromkeys(doc.links):
            label = doc.link_labels.get(website, "").strip()
            if (
                not website.startswith(("http://", "https://"))
                or normalize_domain(website) == normalize_domain(self.url)
                or not label
                or not any(
                    word in label.casefold() for word in ("anw", "kanzlei", "fachanw", "partner")
                )
            ):
                continue
            parsed = urlsplit(website)
            # Research the organization homepage, not the directory or a job listing.
            root = f"{parsed.scheme}://{parsed.netloc}/"
            yield Candidate(label, root, page.url, page.retrieved_at)
            count += 1
            if count >= limit:
                break


class FreeDiscoveryProvider:
    name = "free"

    def __init__(
        self, config: DiscoveryConfig, sources: list[DiscoveryProvider] | None = None
    ) -> None:
        if sources is None:
            client = CachedDirectoryClient(config)
            sources = [OpenStreetMapDirectory(config, client)]
            if config.sector in {"", "law_firm"} and config.location.casefold() == "karlsruhe":
                sources.append(LawAssociationDirectory(client))
            if Path(config.seeds_file).is_file():
                sources.append(SeedProvider(config.seeds_file))
        self.sources = sources
        self.source_results: dict[str, dict[str, str | int]] = {}
        self.duplicate_domains = 0

    def discover(self, limit: int) -> Iterable[Candidate]:
        candidates: dict[str, Candidate] = {}
        # Round robin after bounded collection prevents a large first source monopolizing
        # research. Each independent source receives one call, never a blocked-source retry.
        batches: list[list[Candidate]] = []
        for source in self.sources:
            try:
                batch = list(islice(source.discover(limit), limit))
                self.source_results[source.name] = {"status": "ok", "candidates": len(batch)}
                batches.append(batch)
            except (ValueError, OSError, TypeError, KeyError, AttributeError) as exc:
                self.source_results[source.name] = {
                    "status": "skipped",
                    "error": type(exc).__name__,
                    "candidates": 0,
                }
                logger.warning(
                    "discovery_source_skipped",
                    extra={"source": source.name, "error_type": type(exc).__name__},
                )
        for index in range(max((len(batch) for batch in batches), default=0)):
            for batch in batches:
                if index >= len(batch):
                    continue
                candidate = batch[index]
                try:
                    parsed = urlsplit(candidate.website)
                    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
                        continue
                    domain = normalize_domain(candidate.website)
                    if not domain:
                        continue
                except ValueError:
                    continue
                if domain in candidates:
                    self.duplicate_domains += 1
                    candidates[domain].discovery_sources.extend(
                        candidate.discovery_sources
                        or [(candidate.source_url, candidate.retrieved_at)]
                    )
                elif len(candidates) < limit:
                    candidate.discovery_sources = candidate.discovery_sources or [
                        (candidate.source_url, candidate.retrieved_at)
                    ]
                    candidates[domain] = candidate
        yield from candidates.values()
