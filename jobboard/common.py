"""Small shared primitives; no network or database side effects on import."""

import hashlib
import json
import os
import re
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit


class BoardError(Exception):
    """A user-actionable failure, safe to display without secrets or response bodies."""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today():
    return datetime.now(timezone.utc).date().isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    """Atomic replace avoids partially written reports after an interrupted run."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def iso_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:
        return None


def age_days(value, as_of=None):
    parsed = iso_date(value)
    return (date.fromisoformat(as_of or today()) - date.fromisoformat(parsed)).days if parsed else None


def https_url(value, hosts=None):
    """Validate public destination syntax; collectors also restrict exact hostnames."""
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise BoardError("Expected an HTTPS URL without embedded credentials")
    if parts.port not in (None, 443) or (hosts is not None and parts.hostname not in hosts):
        raise BoardError("URL is outside the configured source hosts")
    return value


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag in {"p", "li", "br", "div", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        if tag in {"p", "li", "div"}:
            self.parts.append("\n")

    def handle_data(self, value):
        if not self.hidden:
            self.parts.append(value)


def plain_text(html):
    parser = _Text()
    parser.feed(html)
    return "\n".join(re.sub(r"\s+", " ", line).strip() for line in "".join(parser.parts).splitlines() if line.strip())


def load_env(path):
    """Read simple KEY=value lines, with no shell evaluation or variable expansion."""
    if not Path(path).exists():
        return
    for line in Path(path).read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key.strip()):
            raise BoardError("Invalid .env assignment; use plain KEY=value lines")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def load_companies(path):
    companies = read_json(path)
    ids = set()
    for company in companies:
        cid = company["id"]
        if cid in ids or not re.fullmatch(r"[a-z][a-z0-9-]*", cid):
            raise BoardError("Company IDs must be unique lowercase slugs")
        ids.add(cid)
        collector = company["collector"]
        if collector["adapter"] not in {"workday", "manual"}:
            raise BoardError(f"Unsupported collector for {cid}")
        https_url(company["cycle"]["source_url"])
        if collector["adapter"] == "workday":
            https_url(collector["base_url"])
            host = urlsplit(collector["base_url"]).hostname
            if not host.endswith(".myworkdayjobs.com"):
                raise BoardError("Workday collector requires a myworkdayjobs.com host")
            for field in ("tenant", "site"):
                if not re.fullmatch(r"[A-Za-z0-9_-]+", collector[field]):
                    raise BoardError(f"Invalid Workday {field}")
            if not collector.get("queries"):
                raise BoardError("Workday sources need explicit search queries")
            for pattern in collector.get("include_title_patterns", []):
                try:
                    re.compile(pattern)
                except re.error:
                    raise BoardError(f"Invalid title pattern for {cid}") from None
    return companies
