"""Workday adapter. Search discovers candidates; detail endpoints establish state.

These are public career-site endpoints, not a guaranteed vendor API. Contract
validation and run gaps make changes visible instead of silently losing jobs.
"""

from dataclasses import dataclass, field
import re
from urllib.parse import urlsplit

from .common import BoardError, https_url, iso_date, plain_text


@dataclass
class Collection:
    snapshots: list = field(default_factory=list)
    errors: dict = field(default_factory=dict)
    gaps: list = field(default_factory=list)
    discovered: int = 0
    queries_completed: int = 0
    excluded_by_title: int = 0


def _detail_url(config, external_path):
    if not isinstance(external_path, str) or not external_path.startswith("/job/"):
        raise BoardError("Unexpected Workday externalPath")
    if any(part in {".", ".."} for part in external_path.split("/")) or "?" in external_path:
        raise BoardError("Unexpected Workday path traversal/query")
    return f"{config['base_url']}/wday/cxs/{config['tenant']}/{config['site']}{external_path}"


def normalize(company, raw, source_url):
    if not isinstance(raw, dict):
        raise BoardError("Workday detail response is not an object")
    info = raw.get("jobPostingInfo")
    if not isinstance(info, dict) or not info.get("jobReqId") or not info.get("title"):
        raise BoardError("Workday detail is missing jobPostingInfo, ID or title")
    host = urlsplit(company["collector"]["base_url"]).hostname
    url = https_url(info.get("externalUrl", ""), {host})
    # Missing booleans are unknown, not false; expiry is never inferred from text.
    if info.get("posted") is False or info.get("canApply") is False:
        status = "closed"
    elif info.get("posted") is True and info.get("canApply") is True:
        status = "open"
    else:
        status = "needs_review"
    locations = [info.get("location", "")]
    locations.extend(x if isinstance(x, str) else x.get("descriptor", "")
                     for x in info.get("additionalLocations", []) or [])
    location = "; ".join(dict.fromkeys(x for x in locations if x))
    country = (info.get("country") or {}).get("descriptor")
    description = plain_text(info.get("jobDescription", ""))
    if status == "open" and not description:
        raise BoardError("Open Workday detail has no description")
    return {
        "company_id": company["id"], "company": company["name"],
        "job_id": str(info["jobReqId"]), "title": info["title"], "url": url,
        "source_url": source_url, "location": location, "country": country,
        "work_mode": info.get("remoteType"), "hours": info.get("timeType"),
        "posted_at": iso_date(info.get("startDate")), "deadline": iso_date(info.get("endDate")),
        "deadline_kind": "posted_end_date" if iso_date(info.get("endDate")) else "unknown",
        "status": status, "description": description,
        "availability_evidence": {"posted": info.get("posted"), "canApply": info.get("canApply")},
    }


def collect_workday(company, known_jobs, client, *, max_pages=25, max_details=100):
    result = Collection()
    config = company["collector"]
    endpoint = f"{config['base_url']}/wday/cxs/{config['tenant']}/{config['site']}"
    # Recheck known jobs independently of search ranking and search-page limits.
    detail_urls = {}
    for job in known_jobs:
        url = job.get("source_url")
        if url and url.startswith(endpoint + "/job/"):
            detail_urls[url] = job["job_id"]
        else:
            result.errors[job["job_id"]] = "Known job lacks a valid adapter detail URL"
    found, excluded = set(), set()
    title_patterns = [re.compile(p, re.I) for p in config.get("include_title_patterns", [])]
    for query in config["queries"]:
        offset, seen_pages = 0, set()
        for page in range(max_pages):
            try:
                data = client.json(endpoint + "/jobs", {"appliedFacets": {}, "limit": 20,
                                                        "offset": offset, "searchText": query})
                if not isinstance(data, dict):
                    raise BoardError("Workday search response is not an object")
                total, postings = data.get("total"), data.get("jobPostings")
                if type(total) is not int or total < 0 or not isinstance(postings, list):
                    raise BoardError("Workday search schema changed")
                if any(not isinstance(p, dict) for p in postings):
                    raise BoardError("Workday posting entry is not an object")
                paths = tuple(p.get("externalPath") for p in postings)
                if (total > offset and not paths) or (paths and paths in seen_pages):
                    raise BoardError("Workday pagination stalled or returned an unexpected empty page")
                seen_pages.add(paths)
                for path, posting in zip(paths, postings):
                    url = _detail_url(config, path)
                    if title_patterns:
                        title = posting.get("title")
                        if not isinstance(title, str):
                            result.gaps.append(f"Missing search title for {path}; queued for detail verification")
                        elif not any(pattern.search(title) for pattern in title_patterns):
                            excluded.add(url)
                            continue
                    found.add(url)
                    detail_urls.setdefault(url, None)
                offset += len(postings)
                if offset >= total:
                    result.queries_completed += 1
                    break
                if page == max_pages - 1:
                    result.gaps.append(f"Query {query!r} truncated at {offset}/{total}; raise --max-pages")
            except BoardError as exc:
                result.gaps.append(f"Query {query!r}: {exc}")
                break
    result.discovered = len(found)
    result.excluded_by_title = len(excluded)
    if len(detail_urls) > max_details:
        result.gaps.append(f"Detail limit: checked at most {max_details}/{len(detail_urls)} candidates")
    for index, (url, known_id) in enumerate(detail_urls.items()):
        if index >= max_details:
            if known_id:
                result.errors[known_id] = "Known job not checked: detail limit reached"
            continue
        try:
            raw = client.json(url)
            snapshot = normalize(company, raw, url)
            if known_id and snapshot["job_id"] != known_id:
                raise BoardError("Detail ID changed; source may redirect to another requisition")
            result.snapshots.append(snapshot)
        except BoardError as exc:
            if known_id:
                result.errors[known_id] = str(exc)
            result.gaps.append(f"Detail {url}: {exc}")
    return result
