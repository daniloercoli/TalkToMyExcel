from __future__ import annotations

import json
import re
import struct
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree


SITE = Path(__file__).parents[1] / "public-site"
HTML_FILES = sorted(SITE.glob("*.html")) + sorted(SITE.glob("it/*.html")) + sorted(SITE.glob("en/*.html"))
LOCALIZED_FILES = sorted(SITE.glob("it/*.html")) + sorted(SITE.glob("en/*.html"))


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.elements: list[tuple[str, dict[str, str]]] = []
        self.json_ld: list[str] = []
        self._json_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name: value or "" for name, value in attrs}
        self.elements.append((tag, attributes))
        if tag == "script" and attributes.get("type") == "application/ld+json":
            self._json_parts = []

    def handle_data(self, data: str) -> None:
        if self._json_parts is not None:
            self._json_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._json_parts is not None:
            self.json_ld.append("".join(self._json_parts))
            self._json_parts = None


def parse_page(path: Path) -> PageParser:
    parser = PageParser()
    parser.feed(path.read_text(encoding="utf-8"))
    return parser


def test_public_site_local_links_and_assets_exist():
    missing: list[str] = []
    for page in HTML_FILES:
        for tag, attributes in parse_page(page).elements:
            names = ("href", "src", "srcset") if tag in {"a", "img", "link", "source"} else ()
            for name in names:
                value = attributes.get(name, "").strip()
                if not value:
                    continue
                raw = value.split()[0]
                if raw.startswith(("#", "mailto:", "tel:", "data:", "javascript:")):
                    continue
                parsed = urlsplit(raw)
                if parsed.scheme or parsed.netloc or not parsed.path:
                    continue
                local = unquote(parsed.path)
                target = SITE / local.lstrip("/") if local.startswith("/") else page.parent / local
                if local.endswith("/"):
                    target /= "index.html"
                if not target.exists():
                    missing.append(f"{page.relative_to(SITE)} -> {raw}")
    assert missing == []


def test_localized_pages_have_complete_metadata_and_accessibility_basics():
    for page in LOCALIZED_FILES:
        parser = parse_page(page)
        links = [attrs for tag, attrs in parser.elements if tag == "link"]
        metas = [attrs for tag, attrs in parser.elements if tag == "meta"]
        alternates = {link.get("hreflang") for link in links if "alternate" in link.get("rel", "").split()}
        properties = {meta.get("property"): meta.get("content") for meta in metas if meta.get("property")}
        names = {meta.get("name"): meta.get("content") for meta in metas if meta.get("name")}

        assert any("canonical" in link.get("rel", "").split() for link in links), page
        assert alternates == {"it", "en", "x-default"}, page
        assert properties.get("og:image", "").endswith("/assets/social-card.png"), page
        assert properties.get("og:image:type") == "image/png", page
        assert properties.get("og:image:width") == "1200", page
        assert properties.get("og:image:height") == "630", page
        assert properties.get("og:image:alt"), page
        assert names.get("twitter:card") == "summary_large_image", page
        assert names.get("twitter:image:alt"), page
        assert any(tag == "a" and attrs.get("class") == "skip-link" for tag, attrs in parser.elements), page
        assert any(tag == "main" and attrs.get("id") == "main-content" for tag, attrs in parser.elements), page
        assert any(tag == "a" and attrs.get("aria-current") == "page" for tag, attrs in parser.elements), page
        assert sum(tag == "h1" for tag, _attrs in parser.elements) == 1, page
        for tag, attrs in parser.elements:
            if tag == "img":
                assert "alt" in attrs and attrs.get("width") and attrs.get("height"), (page, attrs)


def test_root_and_home_json_ld_are_valid():
    for page in (SITE / "index.html", SITE / "it/index.html", SITE / "en/index.html"):
        documents = parse_page(page).json_ld
        assert documents, page
        for document in documents:
            assert json.loads(document)["@context"] == "https://schema.org", page


def test_root_has_x_default_metadata_and_non_looping_redirect():
    page = SITE / "index.html"
    parser = parse_page(page)
    links = [attrs for tag, attrs in parser.elements if tag == "link"]
    metas = [attrs for tag, attrs in parser.elements if tag == "meta"]
    alternates = {link.get("hreflang") for link in links if "alternate" in link.get("rel", "").split()}
    properties = {meta.get("property"): meta.get("content") for meta in metas if meta.get("property")}
    source = page.read_text(encoding="utf-8")

    assert alternates == {"it", "en", "x-default"}
    assert properties.get("og:image", "").endswith("/assets/social-card.png")
    assert properties.get("og:image:type") == "image/png"
    assert "window.location.replace(" in source
    assert "window.location.href" not in source


def test_italian_copy_does_not_regress_to_unaccented_placeholders():
    text = "\n".join(path.read_text(encoding="utf-8") for path in SITE.glob("it/*.html"))
    unaccented = re.compile(
        r"\b(perche|piu|puo|qualita|sovranita|tracciabilita|inattivita|auditabilita|comodita|visibilita|complessita|modalita)\b",
        re.IGNORECASE,
    )
    assert unaccented.search(text) is None
    assert "`" not in text


def test_sitemap_has_language_alternates_for_every_url():
    root = ElementTree.parse(SITE / "sitemap.xml").getroot()
    sitemap = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
    xhtml = "{http://www.w3.org/1999/xhtml}"
    urls = root.findall(f"{sitemap}url")
    assert len(urls) == len(HTML_FILES)
    for url in urls:
        assert date.fromisoformat(url.findtext(f"{sitemap}lastmod")) >= date(2026, 7, 15)
        languages = {link.attrib["hreflang"] for link in url.findall(f"{xhtml}link")}
        assert languages == {"it", "en", "x-default"}


def test_social_card_is_linkedin_sized_png():
    data = (SITE / "assets/social-card.png").read_bytes()[:24]
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", data[16:24]) == (1200, 630)
