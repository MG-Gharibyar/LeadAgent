"""Search profiles, independent of research/qualification and message templates."""

from __future__ import annotations

from .config import DiscoveryConfig

KARLSRUHE_REGION = (
    "Karlsruhe",
    "Ettlingen",
    "Rheinstetten",
    "Stutensee",
    "Bruchsal",
    "Pfinztal",
    "Waldbronn",
    "Wörth am Rhein",
    "Eggenstein-Leopoldshafen",
    "Linkenheim-Hochstetten",
    "Weingarten Baden",
    "Bretten",
    "Durmersheim",
    "Rastatt",
)
SEARCH_TERMS = {
    "law_firm": "Rechtsanwaltskanzlei Rechtsanwälte Team Mandantenportal",
    "medical_practice": "Arztpraxis Zahnarztpraxis MVZ digitales Patientenportal",
    "tax_advisor": "Steuerberatung Kanzlei digitale Zusammenarbeit",
    "it_service_provider": "IT Systemhaus Managed Services Security Partner",
}


def configure_search(config: DiscoveryConfig, sector: str | None, location: str | None) -> None:
    config.sector = sector or ""
    config.location = (location or "").strip()
    locations = (
        list(KARLSRUHE_REGION) if config.location.casefold() == "karlsruhe" else [config.location]
    )
    terms = [SEARCH_TERMS[sector]] if sector else list(SEARCH_TERMS.values())
    config.queries = [f"{term} {city}".strip() for city in locations for term in terms]
    if config.location.casefold() == "karlsruhe":
        # Include a regional query, so results aren't confined to the example towns.
        config.queries.insert(1, f"{terms[0]} Region Karlsruhe Umgebung 35 km")
    config.query_regions = []  # Explicit location replaces the daily nationwide rotation.
