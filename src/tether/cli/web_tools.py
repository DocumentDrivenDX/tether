"""Bounded standard-library web helpers for the trusted reference tools."""

from __future__ import annotations

import json
from html.parser import HTMLParser
from http.client import HTTPMessage
from typing import IO
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_WEB_BYTES = 262144
MAX_TEXT = 32768


def validate_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username is not None:
        raise ValueError("http_url_required")
    return url


class Redirects(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> Request | None:
        return super().redirect_request(req, fp, code, msg, headers, validate_url(newurl))


def fetch_text(url: str) -> tuple[str, str, str, bool]:
    # Both initial URLs and redirect targets are restricted to HTTP(S).
    request = Request(  # noqa: S310 — validate_url enforces the scheme
        validate_url(url), headers={"User-Agent": "Tether-reference/1.0"}
    )
    with build_opener(Redirects()).open(request, timeout=10) as response:
        kind = response.headers.get_content_type()
        if kind not in {"text/html", "text/plain", "application/xhtml+xml"}:
            raise ValueError("unsupported_page_content_type")
        data = response.read(MAX_WEB_BYTES + 1)
        charset = response.headers.get_content_charset() or "utf-8"
        return (
            response.geturl(),
            kind,
            data[:MAX_WEB_BYTES].decode(charset, errors="replace"),
            len(data) > MAX_WEB_BYTES,
        )


class PageText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.hidden += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data: str) -> None:
        if not self.hidden and data.strip():
            self.parts.append(data.strip())


def load_page(url: str) -> str:
    final_url, kind, text, truncated = fetch_text(url)
    if kind != "text/plain":
        parser = PageText()
        parser.feed(text)
        text = "\n".join(parser.parts)
    return json.dumps(
        {
            "url": final_url,
            "content_type": kind,
            "text": text[:MAX_TEXT],
            "truncated": truncated or len(text) > MAX_TEXT,
        }
    )


class SearchResults(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self.field: str | None = None
        self.end_tag: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        classes = (values.get("class") or "").split()
        if tag == "a" and ({"result__a", "result-link"} & set(classes)):
            href = urljoin("https://duckduckgo.com", values.get("href") or "")
            target = parse_qs(urlsplit(href).query).get("uddg", [href])[0]
            validate_url(target)
            self.results.append({"title": "", "url": target, "snippet": ""})
            self.field, self.end_tag = "title", tag
        elif {"result__snippet", "result-snippet"} & set(classes) and self.results:
            self.field, self.end_tag = "snippet", tag

    def handle_endtag(self, tag: str) -> None:
        if tag == self.end_tag:
            self.field = self.end_tag = None

    def handle_data(self, data: str) -> None:
        if self.field is not None:
            self.results[-1][self.field] += data


def search_web(query: str) -> str:
    url = "https://lite.duckduckgo.com/lite/?" + urlencode({"q": query})
    final_url, _, text, truncated = fetch_text(url)
    parser = SearchResults()
    parser.feed(text)
    truncated = (
        truncated
        or len(parser.results) > 5
        or any(
            len(item["title"]) > 512 or len(item["url"]) > 8192 or len(item["snippet"]) > 2048
            for item in parser.results[:5]
        )
    )
    results = [
        {"title": item["title"][:512], "url": item["url"], "snippet": item["snippet"][:2048]}
        for item in parser.results[:5]
        if len(item["url"]) <= 8192
    ]
    return json.dumps(
        {
            "query": query,
            "source": final_url,
            "results": results,
            "truncated": truncated,
            "notice": "No parsed results; provider may be blocked or changed."
            if not parser.results
            else None,
        }
    )
