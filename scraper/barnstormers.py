"""Scraper for Rans S-7 aircraft listings on barnstormers.com.

Listings come from Barnstormers' "Experimental - Rans" category, which
carries every Rans model, so only the S-7 is kept: each ad's title must name
an S-7 (or Courier) and no other Rans model, and titles that look like
parts/accessories/services/raffles are dropped. Surviving titles are
rewritten to a canonical "YEAR Rans MODEL" form so every listing follows the
same format.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from .common import (
    MIN_AIRCRAFT_PRICE,
    Listing,
    extract_date,
    extract_location,
    extract_price,
    fetch,
    format_aircraft_title,
    price_value,
)

SITE_NAME = "Barnstormers.com"
BASE = "https://www.barnstormers.com"
MAKE = "Rans"

# Barnstormers' "Experimental - Rans" category.
SOURCE_URLS = [
    f"{BASE}/category-18956-Experimental--Rans.html",
]

MAX_PAGES = 15
LISTING_LINK_RE = re.compile(r"^/classified-(\d+)-(.+)\.html$")
GENERIC_SITE_TITLE_SNIPPET = "barnstormers.com find aircraft"

# S-7 model code with its version suffix ("S-7", "S7S", "S-7LS", "S 7 C" - URL
# slugs read hyphens as spaces); the digit must not run into another one, so
# "S-70" doesn't match.
_S7_RE = re.compile(r"\bs\s?-?\s?7(?!\d)(?:\s?-?\s?(ls|s|c)\b)?(?![\da-z])", re.IGNORECASE)
_COURIER_RE = re.compile(r"\bcourier\b", re.IGNORECASE)
# Other Rans models (the S-6 Coyote II, S-10 Sakota, S-12/S-14 Airaile, S-16,
# S-19 Venterra, S-20 Raven, S-21 Outbound, ...). A title naming one of these
# is some other airplane in the category, or a mixed ad, and is dropped.
_OTHER_MODEL_RE = re.compile(
    r"\bs\s?-?\s?(?:[4-6]|1\d|2\d)(?!\d)|coyote|sakota|airaile|venterra|raven|outbound|stinger",
    re.IGNORECASE,
)


def _extract_model(title: str) -> tuple[str, str] | None:
    if _OTHER_MODEL_RE.search(title):
        return None
    match = _S7_RE.search(title)
    courier = bool(_COURIER_RE.search(title))
    if not match and not courier:
        return None
    model = "S-7" + (match.group(1).upper() if match and match.group(1) else "")
    return MAKE, model + (" Courier" if courier else "")


def _title_from_url(url: str) -> str:
    """Listing pages share a generic <title>/<h1>, but the URL slug is the ad's own title."""
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    match = LISTING_LINK_RE.match("/" + slug)
    if not match:
        return unquote(slug)
    return unquote(match.group(2)).replace("-", " ").strip()


def _find_listing_links(html: str) -> set[str]:
    soup = BeautifulSoup(html, "lxml")
    links = set()
    for a in soup.find_all("a", href=True):
        href = urlparse(urljoin(BASE, a["href"])).path
        if LISTING_LINK_RE.match(href):
            links.add(urljoin(BASE, href))
    return links


def _page_url(source_url: str, page: int) -> str:
    """Build a results page's URL directly, for either kind of source.

    Barnstormers' pagers render as page-number buttons with no "Next" text or
    rel="next" attribute for a link-following heuristic to find, so each
    page's URL is built from the known pattern instead: an advanced search
    takes a page=<n> query parameter, and a category page takes
    ?seocategory=<url-encoded-path>&page=<n>.
    """
    if page <= 1:
        return source_url
    parts = urlparse(source_url)
    if parts.path.endswith("cat_search.php"):
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "page"]
        query.append(("page", str(page)))
        return urlunparse(parts._replace(query=urlencode(query)))
    return f"{source_url}?seocategory={quote(parts.path, safe='')}&page={page}"

def _debug_dump_hrefs(html: str, limit: int = 25) -> None:
    soup = BeautifulSoup(html, "lxml")
    hrefs = [a["href"] for a in soup.find_all("a", href=True)]
    interesting = [h for h in hrefs if "classified" in h.lower() or "rans" in h.lower()]
    sample = interesting[:limit] or hrefs[:limit]
    print(f"  [debug] {len(hrefs)} total <a href> on page; sample: {sample}")


def _parse_detail_page(url: str, html: str) -> Listing | None:
    soup = BeautifulSoup(html, "lxml")

    title_tag = soup.find("h1") or soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else None
    if title:
        title = re.sub(r"\s*[\|\-]\s*Barnstormers.*$", "", title, flags=re.IGNORECASE).strip()
    if not title or GENERIC_SITE_TITLE_SNIPPET in title.lower():
        title = _title_from_url(url)
    if not title:
        return None

    text = soup.get_text(" ", strip=True)

    formatted_title = format_aircraft_title(title, text, _extract_model)
    if not formatted_title:
        return None
    title = formatted_title

    price = extract_price(text)
    value = price_value(price)
    if value is not None and value < MIN_AIRCRAFT_PRICE:
        return None
    location = extract_location(text)
    date_posted = extract_date(text)

    return Listing(
        title=title,
        price=price,
        location=location,
        date_posted=date_posted,
        site=SITE_NAME,
        url=url,
    )


def scrape() -> list[Listing]:
    print(f"[{SITE_NAME}] starting scrape")
    all_links: set[str] = set()

    for source_url in SOURCE_URLS:
        seen_this_source: set[str] = set()
        for page in range(1, MAX_PAGES + 1):
            url = _page_url(source_url, page)
            html = fetch(url)
            if not html:
                break
            links = _find_listing_links(html)
            new_links = links - seen_this_source
            print(f"  [{'search' if 'cat_search' in source_url else 'category'} page {page}]: {len(links)} links ({len(new_links)} new)")
            if page == 1 and not links:
                _debug_dump_hrefs(html)
            seen_this_source |= links
            if not new_links:
                break
        all_links |= seen_this_source

    print(f"[{SITE_NAME}] {len(all_links)} unique listing URLs found")

    listings: list[Listing] = []
    for url in sorted(all_links):
        html = fetch(url)
        if not html:
            continue
        listing = _parse_detail_page(url, html)
        if listing:
            listings.append(listing)

    print(f"[{SITE_NAME}] parsed {len(listings)} listings")
    return listings
