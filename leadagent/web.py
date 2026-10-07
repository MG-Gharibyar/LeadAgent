from __future__ import annotations

import ipaddress
import socket
import time
from dataclasses import dataclass
from urllib.error import HTTPError
from urllib.parse import unquote, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.robotparser import RobotFileParser

from .config import DiscoveryConfig
from .models import utcnow


class AccessDenied(ValueError):
    """A source must not be fetched under the configured access policy."""


@dataclass
class Page:
    url: str
    text: str
    retrieved_at: str


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Request, fp: object, code: int, msg: str, headers: object, newurl: str
    ) -> None:
        return None


def validate_public_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise AccessDenied("Only public HTTP(S) URLs allowed")
    if parsed.username or parsed.password or parsed.port not in {None, 80, 443}:
        raise AccessDenied("Credentials or nonstandard ports are not allowed")
    addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise AccessDenied("Private, local, reserved or mixed DNS addresses are prohibited")


def disallow_patterns(text: str, user_agent: str) -> list[str]:
    groups: list[tuple[list[str], list[str]]] = []
    agents: list[str] = []
    paths: list[str] = []
    directives = False
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        name, value = (part.strip() for part in line.split(":", 1))
        if name.lower() == "user-agent":
            if directives:
                groups.append((agents, paths))
                agents, paths, directives = [], [], False
            agents.append(value.lower())
        elif agents:
            directives = True
            if name.lower() == "disallow" and value:
                paths.append(value)
    groups.append((agents, paths))
    matching = [
        (
            max(
                (
                    len(agent)
                    for agent in group_agents
                    if agent != "*" and agent in user_agent.lower()
                ),
                default=0,
            ),
            rules,
        )
        for group_agents, rules in groups
    ]
    longest = max((length for length, _ in matching), default=0)
    if longest:
        return [path for length, rules in matching if length == longest for path in rules]
    return [path for group_agents, rules in groups if "*" in group_agents for path in rules]


class PublicWebClient:
    """Bounded public HTML access, fail-closed robots checks and explicit redirect checks.

    Deploy with outbound private-network denial as well: DNS checking alone cannot
    eliminate DNS rebinding between validation and urllib's connection resolution.
    """

    def __init__(self, config: DiscoveryConfig) -> None:
        self.config = config
        self.opener = build_opener(NoRedirect())
        self.robots: dict[str, RobotFileParser] = {}
        self.disallows: dict[str, list[str]] = {}
        self.last_request = 0.0
        self.blocked_hosts: set[str] = set()

    def _check(self, url: str) -> None:
        validate_public_url(url)
        host = urlsplit(url).hostname or ""
        if host in self.blocked_hosts:
            raise AccessDenied("Host paused after access/rate-limit response")
        if self.config.allowed_hosts and host not in self.config.allowed_hosts:
            raise AccessDenied("Host not in configured source allowlist")

    def _request(self, url: str, accept: str = "text/html,text/plain") -> tuple[str, str]:
        self._check(url)
        time.sleep(
            max(0, self.config.request_interval_seconds - (time.monotonic() - self.last_request))
        )
        self.last_request = time.monotonic()
        request = Request(url, headers={"User-Agent": self.config.user_agent, "Accept": accept})
        try:
            with self.opener.open(request, timeout=self.config.timeout_seconds) as response:
                content_type = response.headers.get_content_type()
                if content_type not in {
                    "text/html",
                    "text/plain",
                    "application/xhtml+xml",
                    "application/json",
                }:
                    raise AccessDenied("Unsupported content type")
                body = response.read(self.config.max_response_bytes + 1)
                if len(body) > self.config.max_response_bytes:
                    raise AccessDenied("Response exceeds size limit")
                encoding = response.headers.get_content_charset() or "utf-8"
                return body.decode(encoding, errors="replace"), content_type
        except HTTPError as exc:
            if exc.code in {401, 403, 429}:
                self.blocked_hosts.add(urlsplit(url).hostname or "")
            raise

    def _robots(self, url: str) -> RobotFileParser:
        parsed = urlsplit(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin in self.robots:
            return self.robots[origin]
        parser = RobotFileParser()
        try:
            text, _ = self._request(origin + "/robots.txt", "text/plain")
        except HTTPError as exc:
            if exc.code == 404:
                text = "User-agent: *\nDisallow:"
            else:
                raise AccessDenied("robots.txt unavailable; access denied") from exc
        parser.parse(text.splitlines())
        self.disallows[origin] = disallow_patterns(text, self.config.user_agent)
        delay = parser.crawl_delay(self.config.user_agent) or parser.crawl_delay("*") or 0
        rate = parser.request_rate(self.config.user_agent) or parser.request_rate("*")
        interval = max(self.config.request_interval_seconds, float(delay))
        if rate:
            interval = max(interval, rate.seconds / rate.requests)
        if interval > 60:
            raise AccessDenied("Source crawl delay exceeds supported interval; skip source")
        self.config.request_interval_seconds = interval
        self.robots[origin] = parser
        return parser

    def fetch(self, url: str) -> Page:
        for _ in range(5):
            self._check(url)
            robots = self._robots(url)
            parsed = urlsplit(url)
            origin = f"{parsed.scheme}://{parsed.netloc}"
            path = unquote(parsed.path or "/") + ("?" + parsed.query if parsed.query else "")
            # Deny conservatively for wildcards and Allow precedence; never broaden access.
            denied = any(
                path.startswith(unquote(pattern).split("*", 1)[0].rstrip("$"))
                for pattern in self.disallows.get(origin, [])
            )
            if denied or not robots.can_fetch(self.config.user_agent, url):
                raise AccessDenied("robots.txt disallows this URL")
            try:
                text, _ = self._request(url)
                if any(
                    marker in text.casefold()
                    for marker in (
                        "g-recaptcha",
                        "hcaptcha",
                        "cf-chl-",
                        "verify you are human",
                    )
                ):
                    raise AccessDenied("Human verification encountered; skip source")
                return Page(url, text, utcnow())
            except HTTPError as exc:
                if exc.code in {301, 302, 303, 307, 308} and exc.headers.get("Location"):
                    url = urljoin(url, exc.headers["Location"])
                    continue
                raise
        raise AccessDenied("Redirect limit exceeded")
