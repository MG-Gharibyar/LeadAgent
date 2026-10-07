from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlsplit


def normalize_domain(value: str) -> str:
    raw = value.strip()
    parsed = urlsplit(raw if "://" in raw else f"https://{raw}")
    if parsed.scheme not in {"https", "http"} or parsed.username or parsed.password:
        raise ValueError("Only public HTTP(S) domains without credentials are supported")
    host = (parsed.hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("Invalid domain") from exc
    if len(host) > 253 or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host):
        raise ValueError("Invalid domain")
    if "." not in host or any(not part or len(part) > 63 for part in host.split(".")):
        raise ValueError("Public fully qualified domain required")
    return host


def normalize_name(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold().replace("ß", "ss"))
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = re.sub(r"\b(gmbh|mbh|ag|kg|ohg|gbr|partg|partnerschaft|co)\b", " ", value)
    return " ".join(re.findall(r"[a-z0-9]+", value))


def company_key(name: str, city: str) -> str:
    # Never merge unrelated generic names without a publicly sourced city.
    return f"{normalize_name(name)}|{normalize_name(city)}" if city.strip() else ""
