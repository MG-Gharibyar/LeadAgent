"""Free local German place resolution and exact-radius matching."""

from __future__ import annotations

import io
import json
import math
import re
import unicodedata
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .models import Lead

GEONAMES_DE_URL = "https://download.geonames.org/export/dump/DE.zip"
GEONAMES_ATTRIBUTION = "GeoNames, CC BY 4.0, https://www.geonames.org/"


def _key(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value.casefold())
    plain = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", plain).strip()


@dataclass(frozen=True)
class Place:
    name: str
    latitude: float
    longitude: float
    population: int = 0


@dataclass(frozen=True)
class Area:
    city: str
    radius_km: float
    latitude: float
    longitude: float

    @property
    def label(self) -> str:
        return f"{self.city}:{self.radius_km:g}"


class GeoIndex:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        if not self.path.is_file():
            raise ValueError(
                f"Geo database missing: {self.path}. Run: python -m leadagent geo update"
            )
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("places"), dict):
            raise ValueError("Invalid GeoNames local index")
        self.places = data["places"]

    def resolve(self, city: str) -> Place | None:
        item = self.places.get(_key(city))
        if not item:
            return None
        return Place(
            item["name"],
            float(item["latitude"]),
            float(item["longitude"]),
            int(item.get("population", 0)),
        )


def update_geo_database(path: str, url: str = GEONAMES_DE_URL) -> int:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "DSCLeadAgent/0.1 (+https://digitalskills-campus.de)"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read(80_000_001)
    if len(raw) > 80_000_000:
        raise ValueError("GeoNames Germany download exceeds expected bound")
    places: dict[str, dict[str, object]] = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members = [name for name in archive.namelist() if name.endswith(".txt")]
        if not members:
            raise ValueError("GeoNames archive contains no text dataset")
        with archive.open(members[0]) as handle:
            for raw_line in handle:
                fields = raw_line.decode("utf-8", errors="replace").rstrip("\n").split("\t")
                if len(fields) < 15 or fields[6] != "P" or fields[8] != "DE":
                    continue
                try:
                    latitude = float(fields[4])
                    longitude = float(fields[5])
                    population = int(fields[14] or 0)
                except ValueError:
                    continue
                name = fields[1].strip()
                aliases = {name, fields[2].strip()}
                aliases.update(a.strip() for a in fields[3].split(",") if a.strip())
                record = {
                    "name": name,
                    "latitude": latitude,
                    "longitude": longitude,
                    "population": population,
                }
                for alias in aliases:
                    key = _key(alias)
                    if not key:
                        continue
                    previous = places.get(key)
                    if previous is None or int(previous.get("population", 0)) < population:
                        places[key] = record
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(
        json.dumps(
            {"source": url, "attribution": GEONAMES_ATTRIBUTION, "places": places},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    tmp.replace(target)
    return len(places)


def parse_area(value: str) -> tuple[str, float]:
    if ":" not in value:
        raise ValueError('Area must be CITY:RADIUS_KM, e.g. "Karlsruhe:50"')
    city, radius = value.rsplit(":", 1)
    city = city.strip()
    try:
        radius_km = float(radius)
    except ValueError as exc:
        raise ValueError("Area radius must be numeric") from exc
    if not city or not 1 <= radius_km <= 300:
        raise ValueError("Area requires a city and a radius between 1 and 300 km")
    return city, radius_km


def resolve_areas(values: list[str], index: GeoIndex) -> list[Area]:
    result = []
    for value in values:
        city, radius = parse_area(value)
        place = index.resolve(city)
        if place is None:
            raise ValueError(f"German city not found in local GeoNames index: {city}")
        result.append(Area(place.name, radius, place.latitude, place.longitude))
    return result


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * radius * math.asin(math.sqrt(a))


def apply_area_match(lead: Lead, areas: list[Area], index: GeoIndex) -> str:
    if not lead.city:
        lead.location_review_required = True
        return "REVIEW"
    place = index.resolve(lead.city)
    if place is None:
        lead.location_review_required = True
        return "REVIEW"
    lead.latitude, lead.longitude = place.latitude, place.longitude
    lead.location_resolution_source = GEONAMES_ATTRIBUTION
    lead.country = "Germany"
    lead.evidence = [
        e
        for e in lead.evidence
        if not (e.kind == "risk" and e.value == "unverified_german_location")
    ]
    distances = [
        (
            haversine_km(place.latitude, place.longitude, area.latitude, area.longitude),
            area,
        )
        for area in areas
    ]
    distance, _ = min(distances, key=lambda item: item[0])
    lead.distance_km = round(distance, 2)
    matching = [item for item in distances if item[0] <= item[1].radius_km]
    if not matching:
        return "OUTSIDE"
    lead.search_areas = [area.label for dist, area in matching]
    _, selected = min(matching, key=lambda item: item[0])
    lead.campaign_region = selected.city
    lead.location_review_required = False
    return "MATCH"
