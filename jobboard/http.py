"""Polite, bounded public-source transport. Never handles OpenAI credentials."""

import json
import time
import urllib.error
import urllib.request
import urllib.robotparser
from urllib.parse import urlsplit

from .common import BoardError, https_url

USER_AGENT = "AICD3JobBoard/0.1 (public-career-research; manual-review)"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    # A redirect can land on login, a bot challenge, or an unapproved host.
    # Report it instead of treating the resulting HTML as a job or sending secrets.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class FetchError(BoardError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class PublicClient:
    def __init__(self, hosts, *, delay=0.35, max_requests=1500, timeout=25):
        self.hosts = set(hosts)
        self.delay = delay
        self.max_requests = max_requests
        self.timeout = timeout
        self.requests = 0
        self._last = 0.0
        self._robots = {}
        self._opener = urllib.request.build_opener(NoRedirect)

    def _raw(self, url, payload=None):
        https_url(url, self.hosts)
        for attempt in range(3):
            if self.requests >= self.max_requests:
                raise FetchError("Run request limit reached; remaining sources need review")
            time.sleep(max(0, self.delay - (time.monotonic() - self._last)))
            self.requests += 1
            request = urllib.request.Request(
                url, data=json.dumps(payload).encode() if payload is not None else None,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*",
                         "Content-Type": "application/json"},
            )
            try:
                with self._opener.open(request, timeout=self.timeout) as response:
                    # Refuse unexpected payload sizes instead of exhausting memory.
                    raw = response.read(4_000_001)
                    if len(raw) > 4_000_000:
                        raise FetchError("Source response exceeds 4 MB limit")
                    return raw.decode("utf-8")
            except urllib.error.HTTPError as exc:
                status = exc.code
                retry_after = exc.headers.get("Retry-After", "")
                exc.close()
                if status in {429, 500, 502, 503, 504} and attempt < 2:
                    time.sleep(min(5, float(retry_after)) if retry_after.isdigit() else 2 ** attempt)
                    continue
                raise FetchError(f"Source HTTP {status}; not evidence of closure", status) from None
            except (urllib.error.URLError, TimeoutError, UnicodeError):
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise FetchError("Source network/encoding failure; last successful data retained") from None
            finally:
                self._last = time.monotonic()

    def _check_robots(self, url):
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            parser = urllib.robotparser.RobotFileParser()
            try:
                text = self._raw(origin + "/robots.txt")
            except FetchError as exc:
                if exc.status == 404:
                    text = "User-agent: *\nAllow: /"
                else:
                    # Fail closed on ambiguous policy. No CAPTCHA/auth workarounds.
                    raise FetchError("Unable to establish robots policy; manual review required") from None
            if "<html" in text.lower() or "<!doctype" in text.lower():
                raise FetchError("Robots endpoint returned HTML; manual policy review required")
            parser.parse(text.splitlines())
            self._robots[origin] = parser
        parser = self._robots[origin]
        if not parser.can_fetch(USER_AGENT, url):
            raise FetchError("Source robots policy disallows this endpoint; use manual review")
        self.delay = max(self.delay, parser.crawl_delay(USER_AGENT) or 0)

    def json(self, url, payload=None):
        https_url(url, self.hosts)
        self._check_robots(url)
        try:
            return json.loads(self._raw(url, payload))
        except json.JSONDecodeError:
            raise FetchError("Source returned non-JSON content; possible challenge or page change") from None
