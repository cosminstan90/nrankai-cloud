"""
Site Design Detector — async, zero LLM tokens.

Analyses a URL and returns a technical snapshot useful for scoring
lead quality without spending any LLM budget.

Usage:
    from workers.site_design_detector import detect

    result = await detect("https://example.com")
"""

import os
import re
import logging
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

TIMEOUT = 10.0  # seconds per request

STACK_SIGNATURES = {
    "WordPress": [
        r"wp-content/",
        r"wp-includes/",
        r"wp-json/",
        r"/wp-login\.php",
        r'name=["\']generator["\'][^>]*content=["\']WordPress',
        r'content=["\']WordPress[^"\']*["\'][^>]*name=["\']generator',
    ],
    "Wix": [
        r"static\.wixstatic\.com",
        r"wix\.com/",
        r"X-Wix-Published-Version",
    ],
    "Shopify": [
        r"cdn\.shopify\.com",
        r"myshopify\.com",
        r"Shopify\.theme",
        r'"shop":\s*"[^"]+\.myshopify\.com"',
    ],
    "Squarespace": [
        r"squarespace\.com",
        r"squarespace-cdn\.com",
        r'name=["\']generator["\'][^>]*content=["\']Squarespace',
        r'content=["\']Squarespace[^"\']*["\'][^>]*name=["\']generator',
    ],
    "Joomla": [
        r"/components/com_",
        r"/media/jui/",
        r'name=["\']generator["\'][^>]*content=["\']Joomla',
        r'content=["\']Joomla[^"\']*["\'][^>]*name=["\']generator',
    ],
    "Drupal": [
        r"/sites/default/files/",
        r"/misc/drupal\.js",
        r'name=["\']generator["\'][^>]*content=["\']Drupal',
        r'content=["\']Drupal[^"\']*["\'][^>]*name=["\']generator',
        r"Drupal\.settings",
    ],
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalise_url(url: str) -> str:
    """Ensure URL has a scheme."""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def _detect_stack(html: str) -> str:
    """Return CMS name or 'custom' / 'unknown'."""
    for cms, patterns in STACK_SIGNATURES.items():
        for pat in patterns:
            if re.search(pat, html, re.IGNORECASE):
                return cms
    return "custom"


def _detect_year(html: str, soup: BeautifulSoup) -> int | None:
    """
    Try to extract site vintage from:
      1. Copyright © YYYY in footer / body text
      2. <meta name="generator"> content (e.g. WordPress 5.2)
      3. First <pubDate> in RSS (not parsed here — caller can pass RSS text)
    Returns the most recent year found, or None.
    """
    candidates: list[int] = []
    current_year = 2026

    # 1. Copyright pattern anywhere in the HTML
    for m in re.finditer(r"©\s*(\d{4})", html):
        y = int(m.group(1))
        if 1990 <= y <= current_year:
            candidates.append(y)

    # 2. <meta name="generator">
    generator = soup.find("meta", attrs={"name": re.compile(r"generator", re.I)})
    if generator:
        content = generator.get("content", "")
        m = re.search(r"(\d{4})", content)
        if m:
            y = int(m.group(1))
            if 1990 <= y <= current_year:
                candidates.append(y)

    if candidates:
        return max(candidates)
    return None


def _detect_year_from_rss(rss_text: str) -> int | None:
    """Extract year from the first <pubDate> element in RSS XML."""
    m = re.search(r"<pubDate>[^<]*(\d{4})[^<]*</pubDate>", rss_text, re.IGNORECASE)
    if m:
        y = int(m.group(1))
        if 1990 <= 2026 >= y:
            return y
    return None


def _wp_version(html: str) -> str | None:
    """Extract WordPress version string from meta generator."""
    m = re.search(
        r'(?:name=["\']generator["\'][^>]*content=["\']WordPress\s*([\d.]+)'
        r'|content=["\']WordPress\s*([\d.]+)[^"\']*["\'][^>]*name=["\']generator)',
        html,
        re.IGNORECASE,
    )
    if m:
        return m.group(1) or m.group(2)
    return None


def _is_old_site(stack: str, year: int | None, html: str) -> bool:
    """
    Consider a site old if:
      - estimated_year < 2020, OR
      - stack is Joomla or Drupal, OR
      - stack is WordPress with a version < 5.0
    """
    if year is not None and year < 2020:
        return True
    if stack in ("Joomla", "Drupal"):
        return True
    if stack == "WordPress":
        ver = _wp_version(html)
        if ver:
            try:
                major = int(ver.split(".")[0])
                if major < 5:
                    return True
            except ValueError:
                pass
    return False


def _design_score(
    has_ssl: bool,
    has_schema: bool,
    has_og: bool,
    has_robots: bool,
    mobile_score: int | None,
) -> int:
    score = 0
    if has_ssl:
        score += 20
    if has_schema:
        score += 20
    if has_og:
        score += 10
    if has_robots:
        score += 10
    if mobile_score is not None:
        score += round(mobile_score / 100 * 40)
    return min(score, 100)


def _opportunity_contribution(design_score: int) -> int:
    """0-40, inversely proportional to design_score."""
    return round(40 * (1 - design_score / 100))


# ── PageSpeed ─────────────────────────────────────────────────────────────────

async def _pagespeed_mobile(url: str, client: httpx.AsyncClient) -> int | None:
    """
    Call Google PageSpeed Insights API (mobile strategy).
    Returns 0-100 performance score or None if key missing / request fails.
    """
    api_key = os.environ.get("PAGESPEED_API_KEY", "").strip()
    if not api_key:
        return None

    api_url = (
        "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
        f"?url={url}&strategy=mobile&key={api_key}"
        "&category=performance"
    )
    try:
        resp = await client.get(api_url, timeout=30.0)  # PSI is slow
        resp.raise_for_status()
        data = resp.json()
        score = (
            data.get("lighthouseResult", {})
            .get("categories", {})
            .get("performance", {})
            .get("score")
        )
        if score is not None:
            return round(float(score) * 100)
    except Exception as exc:
        logger.debug("PageSpeed API error for %s: %s", url, exc)
    return None


# ── Main detect function ───────────────────────────────────────────────────────

async def detect(url: str) -> dict:
    """
    Analyse *url* technically and return a design/quality snapshot dict.

    No LLM calls — purely HTTP + HTML parsing.
    """
    default = {
        "stack": "unknown",
        "estimated_year": None,
        "has_ssl": False,
        "mobile_score": None,
        "has_schema": False,
        "has_og_tags": False,
        "has_robots_txt": False,
        "design_score": 0,
        "is_old_site": False,
        "opportunity_score_contribution": 40,
    }

    if not url:
        return default

    try:
        url = _normalise_url(url)
    except Exception:
        return default

    # ── SSL ───────────────────────────────────────────────────────────────────
    has_ssl = url.startswith("https://")

    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    html = ""
    soup = BeautifulSoup("", "html.parser")
    stack = "unknown"
    estimated_year: int | None = None
    has_schema = False
    has_og = False
    has_robots = False
    mobile_score: int | None = None

    async with httpx.AsyncClient(
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (compatible; nrankaiBot/1.0)"},
        timeout=TIMEOUT,
    ) as client:

        # ── Fetch main page ───────────────────────────────────────────────────
        try:
            resp = await client.get(url, timeout=TIMEOUT)
            resp.raise_for_status()
            html = resp.text
            # Update SSL flag based on final redirected URL
            has_ssl = str(resp.url).startswith("https://")
            soup = BeautifulSoup(html, "html.parser")
        except Exception as exc:
            logger.info("Failed to fetch %s: %s", url, exc)
            return default

        # ── Stack detection ───────────────────────────────────────────────────
        stack = _detect_stack(html)

        # ── Year detection ────────────────────────────────────────────────────
        estimated_year = _detect_year(html, soup)

        # If not found yet and site is WordPress, try RSS feed
        if estimated_year is None:
            try:
                rss_url = urljoin(base_url, "/feed/")
                rss_resp = await client.get(rss_url, timeout=TIMEOUT)
                if rss_resp.status_code == 200:
                    estimated_year = _detect_year_from_rss(rss_resp.text)
            except Exception:
                pass

        # ── Schema.org ────────────────────────────────────────────────────────
        has_schema = bool(
            soup.find("script", attrs={"type": "application/ld+json"})
        )

        # ── Open Graph tags ───────────────────────────────────────────────────
        has_og = bool(
            soup.find("meta", attrs={"property": re.compile(r"^og:title$", re.I)})
        )

        # ── robots.txt ────────────────────────────────────────────────────────
        try:
            robots_resp = await client.get(
                urljoin(base_url, "/robots.txt"), timeout=TIMEOUT
            )
            has_robots = robots_resp.status_code == 200
        except Exception:
            has_robots = False

        # ── PageSpeed Insights (mobile) ───────────────────────────────────────
        mobile_score = await _pagespeed_mobile(url, client)

    # ── Derived scores ────────────────────────────────────────────────────────
    d_score = _design_score(has_ssl, has_schema, has_og, has_robots, mobile_score)
    old_site = _is_old_site(stack, estimated_year, html)
    opportunity = _opportunity_contribution(d_score)

    return {
        "stack": stack,
        "estimated_year": estimated_year,
        "has_ssl": has_ssl,
        "mobile_score": mobile_score,
        "has_schema": has_schema,
        "has_og_tags": has_og,
        "has_robots_txt": has_robots,
        "design_score": d_score,
        "is_old_site": old_site,
        "opportunity_score_contribution": opportunity,
    }
