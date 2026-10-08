from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlsplit

from .identity import company_key, normalize_domain, normalize_name
from .models import Evidence, Lead, Segment
from .providers import Candidate
from .sectors import KARLSRUHE_REGION
from .web import Page, PublicWebClient


class Document(HTMLParser):
    def __init__(self, markup: str, url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.url = url
        self.parts: list[str] = []
        self.links: list[str] = []
        self.link_labels: dict[str, str] = {}
        self.current_link = ""
        self.title_parts: list[str] = []
        self.site_name = ""
        self.ignored = 0
        self.in_title = False
        self.title_complete = False
        self.feed(markup)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag in {"script", "style", "noscript"}:
            self.ignored += 1
        if tag == "title" and not self.title_complete:
            self.in_title = True
        if tag == "meta" and values.get("property") == "og:site_name":
            self.site_name = values.get("content") or ""
        if tag == "a" and values.get("href"):
            self.current_link = urljoin(self.url, values["href"] or "")
            self.links.append(self.current_link)
        if tag in {"p", "div", "li", "br", "h1", "h2", "h3", "footer"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self.current_link = ""
        if tag in {"script", "style", "noscript"}:
            self.ignored = max(0, self.ignored - 1)
        if tag == "title" and self.in_title:
            self.in_title = False
            self.title_complete = True
        if tag in {"p", "div", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.ignored:
            return
        if self.current_link:
            self.link_labels[self.current_link] = self.link_labels.get(self.current_link, "") + data
        if self.in_title:
            self.title_parts.append(data)
        self.parts.append(data)

    @property
    def lines(self) -> list[str]:
        return [
            re.sub(r"\s+", " ", line).strip()
            for line in "".join(self.parts).splitlines()
            if line.strip()
        ]


SEGMENT_PATTERNS = {
    Segment.MEDICAL.value: r"\b(arztpraxis|gemeinschaftspraxis|zahnarztpraxis|psychotherapiepraxis|mvz|medizinisches versorgungszentrum)\b",
    Segment.TAX.value: r"\b(steuerberater|steuerberatung|steuerberatungsgesellschaft|lohn.*buchhaltung)\b",
    Segment.LAW.value: r"\b(rechtsanwälte|rechtsanwältin|rechtsanwältinnen|rechtsanwaltskanzlei|rechtsanwalt|rechtsanwaltsgesellschaft|anwaltskanzlei)\b",
    Segment.IT.value: r"\b(it-systemhaus|it systemhaus|managed service provider|it-dienstleister|it dienstleister|systemhaus)\b",
}
PATTERNS = {
    "service_need": {
        "penetration_testing": r"\b(wir suchen|benötigen|anfrage).{0,50}(penetrationstest|pentest)\b",
        "training": r"\b(wir suchen|benötigen|anfrage).{0,50}(security awareness|sicherheitsschulung)\b",
        "managed_it": r"\b(wir suchen|benötigen|anfrage).{0,50}(it-betreuung|it betreuung)\b",
        "backup": r"\b(wir suchen|benötigen|anfrage).{0,50}(backup|datensicherung)\b",
        "microsoft_365": r"\b(wir suchen|benötigen|anfrage).{0,50}(microsoft 365 sicherheit|m365 security)\b",
    },
    "technology": {
        "windows": r"\bwindows\b",
        "microsoft_365": r"\b(microsoft\s*365|office\s*365|m365)\b",
    },
    "operations": {
        "backup": r"\b(backup|datensicherung|wiederherstellung)\b",
        "managed_it": r"\b(managed services|managed it|it-betreuung|it betreuung)\b",
    },
    "digital": {
        "client_portal": r"\b(mandantenportal|patientenportal|digitaler dokumentenaustausch|digitale zusammenarbeit|online-terminbuchung|webakte|online-akte|onlineakte)\b",
        "remote_work": r"\b(homeoffice|remote work|mobiles arbeiten)\b",
    },
    "security": {
        "penetration_testing": r"\b(penetrationstest[s]?|penetration testing|pentest[s]?)\b",
        "security_assessment": r"\b(security assessment[s]?|sicherheitsassessment[s]?|sicherheitsaudit[s]?)\b",
        "security_team": r"\b(security team|sicherheitsteam|it-sicherheitsabteilung)\b",
    },
    "intent": {
        "explicit_request": r"\b(wir suchen.{0,60}(it-partner|it partner|externen it|projektunterstützung)|anfrage.{0,30}it-projekt)\b",
        "partner_opportunity": r"\b(wir suchen.{0,60}(partner|dienstleister)|technologiepartner gesucht|partner werden)\b",
        "security_hiring": r"\b(wir suchen.{0,60}(it-administrator|security|systemadministrator)|stellenangebot.{0,60}(security|it-administrator))\b",
        "transformation": r"\b((wir|unsere|aktuell|geplant|planen).{0,50}(migration.{0,30}(microsoft 365|cloud)|wechsel.{0,30}microsoft 365))\b",
        "external_it": r"\b(unsere it.{0,60}(extern|dienstleister)|externe it-betreuung)\b",
        "multiple_locations": r"\b(unsere standorte|mehrere standorte|[2-9] standorte|standorten?\s+in\s+[^.!?\n]{1,60}\s+und\s+[A-ZÄÖÜ][a-zäöüß]+)\b",
        "security_concern": r"\b(wir.{0,50}(ransomware|sicherheitsvorfall)|unsere.{0,30}sicherheitsbedenken)\b",
    },
    "size": {
        "small_medium_team": r"\b(unser team.{0,40}[3-9]|(?:[3-9]|[1-9][0-9])\s+(mitarbeiter|beschäftigte|ärzte|rechtsanwälte|steuerberater))\b"
    },
    "risk": {"large_enterprise": r"\b([1-9][0-9]{3,}\s+(mitarbeiter|beschäftigte))\b"},
}


def extract_evidence(page: Page, doc: Document, include_segment: bool = False) -> list[Evidence]:
    result: list[Evidence] = []
    patterns = dict(PATTERNS)
    if include_segment:
        patterns["segment"] = SEGMENT_PATTERNS
    for kind, rules in patterns.items():
        for value, pattern in rules.items():
            for line in doc.lines:
                # Avoid matching negative claims, vacancies closed, hypothetical text or testimonials.
                if re.search(
                    r"\b(kein\w*|nicht|geschlossen|besetzt|beispielsweise|kunde\w*|referenz\w*)\b",
                    line,
                    re.I,
                ):
                    continue
                match = re.search(pattern, line, re.I)
                if match:
                    # Short verbatim observation from company text; no LLM factual generation.
                    excerpt = line[max(0, match.start() - 60) : match.end() + 100][:240]
                    result.append(Evidence(kind, value, excerpt, page.url, page.retrieved_at))
                    break
    return result


def _contact(lead: Lead, page: Page, doc: Document, region: str = "") -> None:
    for link in doc.links:
        if link.startswith("mailto:"):
            email = unquote(link[7:].split("?", 1)[0]).strip()
            # Collect common public business/role inboxes only; do not derive personal addresses.
            if (
                re.fullmatch(
                    r"(info|kontakt|contact|office|kanzlei|praxis|service|hello|partner|post|mail|sekretariat|team|empfang|zentrale|buero|verwaltung|support|anwaelte|rechtsanwaelte|rezeption|termin|beratung|karlsruhe)@[a-zA-Z0-9.-]+",
                    email,
                    re.I,
                )
                and not lead.public_email
            ):
                lead.public_email = email
                lead.evidence.append(
                    Evidence("contact", "public_email", email, page.url, page.retrieved_at)
                )
    if re.search(r"kontakt|contact", urlsplit(page.url).path, re.I):
        lead.contact_page = page.url
    text = "\n".join(doc.lines)
    if not lead.public_email:
        # Only explicitly printed role inboxes; do not derive addresses from names.
        role_email = re.search(
            r"\b(?:info|kontakt|contact|office|kanzlei|praxis|service|hello|partner|post|mail|sekretariat|team|empfang|zentrale|buero|verwaltung|support|anwaelte|rechtsanwaelte|rezeption|termin|beratung|karlsruhe)@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b",
            text,
            re.I,
        )
        if role_email:
            lead.public_email = role_email[0]
            lead.evidence.append(
                Evidence("contact", "public_email", role_email[0], page.url, page.retrieved_at)
            )
    if region.casefold() == "karlsruhe":
        for city in KARLSRUHE_REGION:
            city_pattern = (
                r"Weingarten\s+\(?Baden\)?" if city == "Weingarten Baden" else re.escape(city)
            )
            regional_address = re.search(r"\b(\d{5})\s+(" + city_pattern + r")(?!\w)", text, re.I)
            if regional_address:
                lead.postal_code, lead.city, lead.country = (
                    regional_address[1],
                    city.replace("Weingarten Baden", "Weingarten (Baden)"),
                    "Germany",
                )
                lead.evidence.append(
                    Evidence(
                        "location",
                        "Germany",
                        regional_address[0]
                        + "; publicly stated address in a German Karlsruhe-region municipality",
                        page.url,
                        page.retrieved_at,
                    )
                )
                break
    address = re.search(r"\b(\d{5})\s+([A-ZÄÖÜ][a-zäöüßA-ZÄÖÜ -]{2,40})(?:\n|$)", text)
    if address and re.search(r"\b(deutschland|germany)\b", text, re.I):
        lead.postal_code, lead.city = address.group(1), address.group(2).strip()
        lead.country = "Germany"
        lead.evidence.append(
            Evidence(
                "location",
                "Germany",
                address.group(0).strip() + "; Deutschland",
                page.url,
                page.retrieved_at,
            )
        )


def public_company_name(doc: Document) -> str:
    candidates = [doc.site_name, *re.split(r"\||\s+[–—-]\s+", "".join(doc.title_parts))]
    for candidate in candidates:
        candidate = candidate.strip(" -–—")
        # A site's own title/site-name is sourced data, but a generic category+city
        # cannot serve as the organization's identity.
        if not company_key(candidate, " ".join(KARLSRUHE_REGION)):
            continue
        return candidate
    raise ValueError("No distinctive public company name found")


class Researcher:
    def __init__(self, client: PublicWebClient) -> None:
        self.client = client

    def research(self, candidate: Candidate) -> tuple[Lead, list[tuple[str, str]]]:
        if candidate.fixture is not None:
            lead = Lead.from_dict(candidate.fixture)
            # Treat fixture timestamps as supplied evidence; don't fabricate recency.
            lead.last_researched_at = max((e.retrieved_at for e in lead.evidence), default="")
            return lead, candidate.aliases
        original_domain = normalize_domain(candidate.website)
        root_page = self.client.fetch(candidate.website)
        root = Document(root_page.text, root_page.url)
        name = public_company_name(root)
        if not name:
            raise ValueError("No public company name found")
        lead = Lead(name, root_page.url)
        if candidate.latitude is not None and candidate.longitude is not None:
            lead.latitude = candidate.latitude
            lead.longitude = candidate.longitude
            lead.location_resolution_source = "OpenStreetMap discovery geometry"
        lead.evidence.append(
            Evidence("company_name", name, name, root_page.url, root_page.retrieved_at)
        )
        lead.source_urls.append(candidate.source_url)
        for source_url, retrieved_at in candidate.discovery_sources or [
            (candidate.source_url, candidate.retrieved_at)
        ]:
            lead.source_urls.append(source_url)
            lead.evidence.append(
                Evidence(
                    "discovery",
                    "directory_hint",
                    "Discovery only; independently research company website",
                    source_url,
                    retrieved_at,
                )
            )
        root_domain = normalize_domain(root_page.url)
        # Search results can be directory entries. Require a home/profile segment and legal identity.
        parsed = urlsplit(root_page.url)
        home = f"{parsed.scheme}://{parsed.netloc}/"
        queue = [home] if urlsplit(root_page.url).path not in {"", "/"} else []
        seen = {root_page.url}
        documents = [(root_page, root)]
        priority = re.compile(
            r"impressum|kontakt|contact|about|ueber|über|team|leistung|service|karriere|jobs|partner|rechtsanw[aä]lte|kanzlei|mandantenportal|digital",
            re.I,
        )
        queue.extend(
            sorted(
                (
                    link
                    for link in root.links
                    if priority.search(link) or priority.search(root.link_labels.get(link, ""))
                ),
                key=lambda link: (not bool(re.search(r"impressum|kontakt", link, re.I)), link),
            )
        )
        while queue and len(documents) < self.client.config.maximum_pages_per_company:
            url = queue.pop(0).split("#")[0]
            if url in seen or not url.startswith(("https://", "http://")):
                continue
            if normalize_domain(url) != root_domain:
                continue
            seen.add(url)
            try:
                page = self.client.fetch(url)
            except (ValueError, OSError):
                continue
            if normalize_domain(page.url) != root_domain or any(
                existing.url == page.url for existing, _ in documents
            ):
                continue
            document = Document(page.text, page.url)
            documents.append((page, document))
            queue.extend(
                link
                for link in document.links
                if (priority.search(link) or priority.search(document.link_labels.get(link, "")))
                and link not in seen
            )
        homepage = next(
            ((page, doc) for page, doc in documents if urlsplit(page.url).path in {"", "/"}), None
        )
        if homepage:
            page, document = homepage
            name = public_company_name(document)
            if name:
                lead.company_name = name
                lead.evidence.append(
                    Evidence("company_name", name, name, page.url, page.retrieved_at)
                )
        identity_words = set(normalize_name(lead.company_name).split())
        legal_pages = [
            (page, doc)
            for page, doc in documents
            if re.search(r"impressum", urlsplit(page.url).path, re.I)
            or any(
                re.search(r"impressum", document.link_labels.get(page.url, ""), re.I)
                for _, document in documents
            )
        ]
        if not identity_words or not any(
            identity_words <= set(normalize_name(" ".join(doc.lines)).split())
            for _, doc in legal_pages
        ):
            lead.evidence.append(
                Evidence(
                    "risk",
                    "unverified_legal_identity",
                    "Company identity not verified against a public legal notice",
                    root_page.url,
                    root_page.retrieved_at,
                )
            )
        for page, document in documents:
            # Classify only from home and company title, not incidental customer descriptions.
            is_home = urlsplit(page.url).path in {"", "/", "/index.html"}
            lead.evidence.extend(extract_evidence(page, document, include_segment=is_home))
            _contact(lead, page, document, self.client.config.location)
            lead.source_urls.append(page.url)
            lead.last_researched_at = page.retrieved_at
        need = next((e for e in lead.evidence if e.kind == "service_need"), None)
        if need:
            lead.evidence.append(
                Evidence(
                    "intent", "explicit_request", need.excerpt, need.source_url, need.retrieved_at
                )
            )
        if re.search(r"verzeichnis|directory|branchenbuch|firmenliste", lead.company_name, re.I):
            lead.evidence.append(
                Evidence(
                    "risk",
                    "directory_not_company",
                    "Public directory is a discovery source, not a qualified company",
                    root_page.url,
                    root_page.retrieved_at,
                )
            )
        # Partner fit is explicitly derived from coexisting sourced service claims.
        if any(e.kind == "segment" and e.value == Segment.IT.value for e in lead.evidence):
            managed = next(
                (e for e in lead.evidence if e.kind == "operations" and e.value == "managed_it"),
                None,
            )
            if managed:
                lead.evidence.append(
                    Evidence(
                        "intent",
                        "partner_fit",
                        managed.excerpt,
                        managed.source_url,
                        managed.retrieved_at,
                    )
                )
        if not lead.country:
            lead.risk_disqualification_signals.append("German public business address not verified")
            lead.evidence.append(
                Evidence(
                    "risk",
                    "unverified_german_location",
                    "No German public business address verified in retrieved pages",
                    root_page.url,
                    root_page.retrieved_at,
                )
            )
        lead.source_urls = list(dict.fromkeys(lead.source_urls))
        aliases = [(original_domain, candidate.website)] if original_domain != root_domain else []
        return lead, aliases


def plain_excerpt(value: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", value)).strip()
