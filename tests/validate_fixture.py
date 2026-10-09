#!/usr/bin/env python3
"""Validate this fixed, public static fixture using only Python's standard library.

No external requests are made. The only HTTP target is a server owned by this
process, bound to an ephemeral IPv4 loopback port and a project-path prefix.
"""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import hashlib
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import re
import threading
from urllib.error import HTTPError
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener


PROJECT_PREFIX = "/link-checker-test-site/"
SOURCE_PAGES = ("index.html", "nested/page.html")
EXPECTED_COUNTS = {"working": 9, "missing_resource": 2, "missing_fragment": 1}
JAPANESE_HREF = "%E6%97%A5%E6%9C%AC%E8%AA%9E.html#%E9%A0%85%E7%9B%AE"
CASES = (
    ("index.html", "ok.html", "working", "ok.html", ""),
    ("index.html", "missing.html", "missing_resource", "missing.html", ""),
    ("index.html", "ok.html#section-1", "working", "ok.html", "section-1"),
    ("index.html", "ok.html#absent", "missing_fragment", "ok.html", "absent"),
    ("index.html", "nested/page.html", "working", "nested/page.html", ""),
    ("index.html", JAPANESE_HREF, "working", "日本語.html", "項目"),
    ("index.html", "assets/sample.txt", "working", "assets/sample.txt", ""),
    ("index.html", "ok.html", "working", "ok.html", ""),
    ("nested/page.html", "../ok.html", "working", "ok.html", ""),
    ("nested/page.html", "../ok.html#section-1", "working", "ok.html", "section-1"),
    ("nested/page.html", "../missing-deep.html", "missing_resource", "missing-deep.html", ""),
    ("nested/page.html", "../" + JAPANESE_HREF, "working", "日本語.html", "項目"),
)
# Preserve the approved payload exactly, including visible text, styles, and links.
SITE_SHA256 = {
    ".nojekyll": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "404.html": "102b40986994011c4d733262fdb6c9d8fe23f9914355a19dbd5430ea4c2cbe41",
    "assets/sample.txt": "22ead41a62c471d404b7b905fba7b38e640faf22e3d03b0f64d624da6b0e3037",
    "index.html": "0460235962ae53445207cf7f6fd5264c47d4dd8d04dd9eda34ecf67862c3eda5",
    "nested/page.html": "b1542c365e9ee470627c928c117d109b98115be28106129ff17738548361a828",
    "ok.html": "15ad9aeeccbf4bfa5eb7f6f214e7249ed7ed2583714b664f86efcde9f966aa11",
    "style.css": "76803406d708cd757b57257195f847e86b5c9d4828138f226d6f9b1b5577b960",
    "日本語.html": "8624318a3c9acfd7b6dcccc337902069db3bcef77b6f0b0d145009ea5066aa21",
}
MANIFEST_SHA256 = "e56643ba939588c87a9ba101229b9fd32298aa1e2400441628d12cde5cba04ae"
ALLOWED_ATTRIBUTES = {
    "html": {"lang"}, "head": set(), "meta": {"charset", "name", "content"},
    "title": set(), "link": {"rel", "href"}, "body": set(), "main": set(),
    "h1": {"id"}, "h2": {"id"}, "p": {"id"}, "aside": set(),
    "ol": set(), "li": set(), "a": {"href", "id", "name"},
}


class FixtureError(Exception):
    """One or more fixture assertions failed."""


def check_relative_url(value: str) -> None:
    parsed = urlsplit(value)
    decoded_path = unquote(parsed.path, encoding="utf-8", errors="strict")
    if (parsed.scheme or parsed.netloc or parsed.query or not parsed.path
            or decoded_path.startswith("/") or "\\" in decoded_path
            or any(ord(c) < 32 for c in value)):
        raise FixtureError(f"only project-relative URLs are allowed: {value!r}")


class FixtureHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.urls: list[str] = []
        self.ids: set[str] = set()
        self.errors: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in ALLOWED_ATTRIBUTES:
            self.errors.append(f"forbidden or unsupported HTML tag: {tag}")
        allowed = ALLOWED_ATTRIBUTES.get(tag, set())
        names = [name for name, _ in attrs]
        if len(names) != len(set(names)):
            self.errors.append(f"duplicate attributes on {tag}")
        for name, value in attrs:
            if name not in allowed:
                self.errors.append(f"forbidden or unsupported attribute: {tag}.{name}")
            if name in {"id", "name"} and (name == "id" or tag == "a"):
                if value in self.ids:
                    self.errors.append(f"duplicate anchor: {value!r}")
                if value is not None:
                    self.ids.add(value)
            if name == "href" and value is not None:
                self.urls.append(value)
                try:
                    check_relative_url(value)
                except (FixtureError, ValueError) as exc:
                    self.errors.append(str(exc))
        attributes = dict(attrs)
        if tag == "a":
            href = attributes.get("href")
            if href is None:
                self.errors.append("anchor without href")
            else:
                self.links.append(href)
        if tag == "link" and attributes.get("rel") != "stylesheet":
            self.errors.append("only a local stylesheet link is allowed")
        if tag == "meta" and attributes.get("http-equiv"):
            self.errors.append("meta refresh is forbidden")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)


def parse_html(content: bytes) -> FixtureHTML:
    parser = FixtureHTML()
    parser.feed(content.decode("utf-8"))
    parser.close()
    return parser


@contextmanager
def serve_fixture(site: Path):
    """Serve only site/ beneath the same path prefix as the project Pages site."""
    root = site.resolve()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if not path.startswith(PROJECT_PREFIX):
                self.send_error(404)
                return
            try:
                relative = unquote(path[len(PROJECT_PREFIX):], encoding="utf-8", errors="strict")
                parts = relative.split("/")
                if ".." in parts or "\\" in relative:
                    self.send_error(404)
                    return
                target = (root / relative).resolve()
                if not target.is_relative_to(root):
                    self.send_error(404)
                    return
                if target.is_dir():
                    target = target / "index.html"
                if not target.is_file():
                    self.send_error(404)
                    return
                content = target.read_bytes()
            except (OSError, ValueError):
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *_args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise FixtureError("the loopback fixture must not redirect")


def fetch_local(opener, origin: str, url: str) -> tuple[int, bytes]:
    parsed = urlsplit(url)
    if (parsed.scheme, parsed.netloc) != ("http", urlsplit(origin).netloc):
        raise FixtureError("refusing an HTTP request outside the owned loopback server")
    request_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))
    try:
        with opener.open(request_url, timeout=5) as response:
            return response.status, response.read()
    except HTTPError as exc:
        with exc:
            return exc.code, exc.read()


def validate_fixture(repo: Path) -> list[dict]:
    site = repo / "site"
    manifest_file = repo / "expected-results.json"
    if not site.is_dir() or site.is_symlink():
        raise FixtureError("site/ must be a real directory")
    if not manifest_file.is_file() or manifest_file.is_symlink():
        raise FixtureError("expected-results.json must be a regular file")
    entries = list(site.rglob("*"))
    # Stop before reading or serving any payload if a link/special file is present.
    if any(p.is_symlink() or not (p.is_file() or p.is_dir()) for p in entries):
        raise FixtureError("site/ must not contain symlinks or special files")
    errors: list[str] = []
    files = {p.relative_to(site).as_posix() for p in entries if p.is_file()}
    directories = {p.relative_to(site).as_posix() for p in entries if p.is_dir()}
    if files != set(SITE_SHA256):
        errors.append(f"site inventory differs: missing={sorted(set(SITE_SHA256) - files)!r}, extra={sorted(files - set(SITE_SHA256))!r}")
    if directories != {"assets", "nested"}:
        errors.append(f"site directory inventory differs: {sorted(directories)!r}")

    manifest_bytes = manifest_file.read_bytes()
    manifest = json.loads(manifest_bytes)
    expected_manifest = {
        "synthetic": True, "source_pages": 2, "link_occurrences": 12,
        "expected_counts": EXPECTED_COUNTS,
        "cases": [dict(case=i, source=source, href=href, expected=expected)
                  for i, (source, href, expected, _, _) in enumerate(CASES, 1)],
        "purpose": "Expected static fixture outcomes, not a hosted HTTP measurement",
    }
    if manifest != expected_manifest:
        errors.append("expected-results.json differs from the fixed 2-source, 12-occurrence contract")
    if hashlib.sha256(manifest_bytes).hexdigest() != MANIFEST_SHA256:
        errors.append("expected-results.json bytes changed")

    pages: dict[str, FixtureHTML] = {}
    for name in sorted(files):
        content = (site / name).read_bytes()
        if name.endswith(".html"):
            page = parse_html(content)
            pages[name] = page
            errors.extend(f"{name}: {error}" for error in page.errors)
        elif name.endswith(".css"):
            css = content.decode("utf-8")
            if re.search(r"@import\b|url\s*\(|(?:https?|ftp|file|data|javascript):|//", css, re.I):
                errors.append(f"{name}: external or embedded CSS resources are forbidden")
    source_pages = tuple(source for source in SOURCE_PAGES if source in pages)
    if source_pages != SOURCE_PAGES:
        raise FixtureError("declared source pages are missing: " + ", ".join(set(SOURCE_PAGES) - set(source_pages)))
    actual_links = [(source, href) for source in SOURCE_PAGES for href in pages[source].links]
    expected_links = [(source, href) for source, href, _, _, _ in CASES]
    if actual_links != expected_links:
        errors.append(f"ordered link occurrences differ: expected exactly 12, found {len(actual_links)}; duplicates and order are significant")
    for name, page in pages.items():
        if name not in SOURCE_PAGES and page.links:
            errors.append(f"{name}: links outside the two declared source pages are forbidden")

    observations = []
    # Never follow proxy configuration, external URLs, or redirects.
    opener = build_opener(ProxyHandler({}), NoRedirects())
    with serve_fixture(site) as origin:
        status, _ = fetch_local(opener, origin, origin + "/")
        if status != 404:
            errors.append("the loopback server must not expose the fixture at the domain root")
        status, body = fetch_local(opener, origin, origin + PROJECT_PREFIX)
        if status != 200 or body != (site / "index.html").read_bytes():
            errors.append("project-prefix index must return the unchanged index.html with HTTP 200")
        for name in sorted(files):
            status, body = fetch_local(opener, origin, origin + PROJECT_PREFIX + quote(name))
            if status != 200 or body != (site / name).read_bytes():
                errors.append(f"{name}: HTTP serving changed the file bytes or did not return 200")
        for source, page in pages.items():
            for href in page.urls:
                # Safety failures were collected above; never request untrusted hrefs.
                try:
                    check_relative_url(href)
                    resolved = urljoin(origin + PROJECT_PREFIX + quote(source), href)
                    decoded = unquote(urlsplit(resolved).path, encoding="utf-8", errors="strict")
                    if not decoded.startswith(PROJECT_PREFIX):
                        errors.append(f"{source}: URL escapes the project prefix: {href!r}")
                except (FixtureError, ValueError):
                    pass
        for number, (source, href, expected, target, fragment) in enumerate(CASES, 1):
            url = urljoin(origin + PROJECT_PREFIX + quote(source), href)
            parsed = urlsplit(url)
            decoded_path = unquote(parsed.path, encoding="utf-8", errors="strict")
            decoded_fragment = unquote(parsed.fragment, encoding="utf-8", errors="strict")
            if (decoded_path, decoded_fragment) != (PROJECT_PREFIX + target, fragment):
                errors.append(f"case {number}: percent-decoded project path or fragment differs")
            status, content = fetch_local(opener, origin, url)
            if status == 404:
                actual = "missing_resource"
            elif status != 200:
                actual = f"unexpected_http_{status}"
            elif fragment and fragment not in parse_html(content).ids:
                actual = "missing_fragment"
            else:
                actual = "working"
            observations.append(dict(case=number, source=source, href=href, http=status, actual=actual))
            if actual != expected:
                errors.append(f"case {number}: expected {expected}, got {actual} (HTTP {status})")
    counts = dict(Counter(row["actual"] for row in observations))
    if counts != EXPECTED_COUNTS:
        errors.append(f"observed counts differ: expected {EXPECTED_COUNTS}, got {counts}")
    for name, digest in SITE_SHA256.items():
        if name in files and hashlib.sha256((site / name).read_bytes()).hexdigest() != digest:
            errors.append(f"{name}: approved fixture bytes changed")
    if errors:
        raise FixtureError("\n".join(errors))
    return observations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1],
                        help="repository root; defaults to this script's parent repository")
    args = parser.parse_args()
    try:
        results = validate_fixture(args.root.resolve())
    except (FixtureError, OSError, ValueError) as exc:
        print(f"FAIL: {exc}")
        return 1
    for result in results:
        print(f"PASS case {result['case']:02d}: {result['actual']} (HTTP {result['http']})")
    print("PASS: 2 source pages; 12 ordered link occurrences; 9 working, 2 missing_resource, 1 missing_fragment")
    print("PASS: fixed payload bytes, percent-decoded Japanese URLs/anchors, project-prefix paths, and site-only inventory")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
