import json
import logging
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from openai import AsyncOpenAI

from app.config import settings


logger = logging.getLogger(__name__)
BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
USER_AGENT = "EventAssistantBot/0.5"


def _host_allowed(url: str, allowed_hosts: tuple[str, ...] | None) -> bool:
    if not allowed_hosts:
        return True
    host = urlparse(url).netloc.casefold().removeprefix("www.")
    return any(host == allowed or host.endswith(f".{allowed}") for allowed in allowed_hosts)


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _json_payload(text: str) -> dict:
    clean = _strip_code_fence(text)
    try:
        payload = json.loads(clean)
    except json.JSONDecodeError:
        start = clean.find("{")
        end = clean.rfind("}")
        if start == -1 or end <= start:
            raise
        payload = json.loads(clean[start : end + 1])
    return payload if isinstance(payload, dict) else {"events": []}


def _event_nodes(payload) -> list[dict]:
    if isinstance(payload, list):
        nodes = [item for item in payload if isinstance(item, dict)]
    elif isinstance(payload, dict):
        nodes = [payload]
    else:
        return []

    expanded: list[dict] = []
    for node in nodes:
        expanded.append(node)
        graph = node.get("@graph")
        if isinstance(graph, list):
            expanded.extend(item for item in graph if isinstance(item, dict))
    return expanded


def _jsonld_events(html: str, page_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    events: list[dict] = []

    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or "{}")
        except (json.JSONDecodeError, TypeError):
            continue

        for node in _event_nodes(payload):
            kinds = node.get("@type", [])
            kinds = [kinds] if isinstance(kinds, str) else kinds
            if "Event" not in kinds or not node.get("name") or not node.get("startDate"):
                continue

            location = node.get("location") if isinstance(node.get("location"), dict) else {}
            address = location.get("address") if isinstance(location, dict) else None
            if isinstance(address, dict):
                address_text = ", ".join(
                    str(value)
                    for value in (
                        address.get("streetAddress"),
                        address.get("postalCode"),
                        address.get("addressLocality"),
                        address.get("addressCountry"),
                    )
                    if value
                )
            else:
                address_text = str(address).strip() if address else None

            offers = node.get("offers") or {}
            if isinstance(offers, list):
                offer = offers[0] if offers and isinstance(offers[0], dict) else {}
            else:
                offer = offers if isinstance(offers, dict) else {}

            direct_url = node.get("url") or offer.get("url") or page_url
            direct_url = urljoin(page_url, str(direct_url))
            price = offer.get("price")
            attendance = str(node.get("eventAttendanceMode") or "").casefold()
            event_format = "online" if "online" in attendance else "in_person"

            events.append(
                {
                    "title": str(node.get("name") or "").strip(),
                    "start_datetime": str(node.get("startDate")),
                    "end_datetime": str(node.get("endDate")) if node.get("endDate") else None,
                    "venue_name": str(location.get("name") or "").strip() or None,
                    "address": address_text,
                    "registration_url": direct_url,
                    "source_url": page_url,
                    "description": str(node.get("description") or "").strip()[:1200],
                    "is_free": str(price).strip() in {"0", "0.0", "0.00"} if price is not None else None,
                    "format": event_format,
                    "time_confirmed": True,
                }
            )

    return events


def _dedupe_events(events: list[dict]) -> list[dict]:
    seen: set[str] = set()
    unique: list[dict] = []
    for event in events:
        key = "|".join(
            [
                str(event.get("title") or "").casefold().strip(),
                str(event.get("start_datetime") or "").strip(),
                str(event.get("source_url") or event.get("registration_url") or "").split("?", 1)[0].rstrip("/"),
            ]
        )
        if not key.strip("|") or key in seen:
            continue
        seen.add(key)
        unique.append(event)
    return unique


async def brave_web_search(
    queries: list[str],
    *,
    allowed_hosts: tuple[str, ...] | None = None,
    max_results: int = 12,
) -> list[dict]:
    """Search with Brave and return a small deduplicated URL/title/snippet set."""
    if not settings.brave_search_api_key:
        logger.warning("BRAVE_SEARCH_API_KEY is missing; Brave discovery is disabled")
        return []

    headers = {
        "Accept": "application/json",
        "X-Subscription-Token": settings.brave_search_api_key,
        "User-Agent": USER_AGENT,
    }
    results: list[dict] = []
    seen: set[str] = set()

    async with httpx.AsyncClient(timeout=25, follow_redirects=True, headers=headers) as client:
        for query in queries:
            try:
                response = await client.get(
                    BRAVE_SEARCH_URL,
                    params={
                        "q": query,
                        "count": min(10, max_results),
                        "safesearch": "moderate",
                        "text_decorations": "false",
                    },
                )
                response.raise_for_status()
                payload = response.json()
            except Exception:
                logger.exception("Brave search failed for query: %s", query)
                continue

            for item in ((payload.get("web") or {}).get("results") or []):
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url") or "").strip()
                if not url or url in seen or not _host_allowed(url, allowed_hosts):
                    continue
                seen.add(url)
                results.append(
                    {
                        "title": str(item.get("title") or "").strip(),
                        "url": url,
                        "snippet": str(item.get("description") or "").strip(),
                    }
                )
                if len(results) >= max_results:
                    return results

    return results


async def _evidence_from_results(
    results: list[dict],
    *,
    max_pages: int = 8,
    max_chars_per_page: int = 4500,
) -> tuple[str, list[dict]]:
    blocks: list[str] = []
    structured_events: list[dict] = []

    async with httpx.AsyncClient(
        timeout=20,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        for index, result in enumerate(results[:max_pages], start=1):
            url = result["url"]
            page_text = ""
            try:
                response = await client.get(url)
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").casefold()
                if "text/html" in content_type:
                    html = response.text
                    structured_events.extend(_jsonld_events(html, str(response.url)))
                    soup = BeautifulSoup(html, "html.parser")
                    for tag in soup(["script", "style", "noscript", "svg"]):
                        tag.decompose()
                    page_text = " ".join(soup.stripped_strings)
            except Exception:
                logger.info("Could not fetch Brave result page %s", url, exc_info=True)

            if not page_text:
                page_text = result.get("snippet") or ""
            page_text = page_text[:max_chars_per_page]
            blocks.append(
                f"SOURCE {index}\nTITLE: {result.get('title') or ''}\nURL: {url}\nTEXT: {page_text}"
            )

    return "\n\n".join(blocks), _dedupe_events(structured_events)


async def discover_event_payload(
    *,
    queries: list[str],
    extraction_prompt: str,
    allowed_hosts: tuple[str, ...] | None = None,
    max_results: int = 12,
    max_pages: int = 8,
    max_output_tokens: int = 3500,
) -> dict:
    """Brave-first event discovery with deterministic JSON-LD before AI fallback.

    Brave performs the web search. We fetch a small set of result pages ourselves.
    If those pages expose Schema.org Event data, no OpenAI call is needed at all.
    Otherwise one compact OpenAI call structures only the supplied evidence and
    never receives the web_search tool.
    """
    results = await brave_web_search(
        queries,
        allowed_hosts=allowed_hosts,
        max_results=max_results,
    )
    if not results:
        return {"events": []}

    evidence, structured_events = await _evidence_from_results(
        results,
        max_pages=max_pages,
    )
    if structured_events:
        logger.info("Brave discovery resolved %s events from JSON-LD without OpenAI", len(structured_events))
        return {"events": structured_events}

    if not settings.openai_api_key:
        logger.warning("OPENAI_API_KEY is missing; no model fallback is available for Brave results")
        return {"events": []}

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    prompt = (
        extraction_prompt.strip()
        + "\n\nUse ONLY the evidence below. Do not browse the web and do not invent missing facts. "
        + "If the evidence does not explicitly support a concrete event date, omit that event.\n\n"
        + evidence
    )
    try:
        response = await client.responses.create(
            model=settings.openai_discovery_model,
            input=prompt,
            max_output_tokens=max_output_tokens,
        )
        return _json_payload(response.output_text)
    except Exception:
        logger.exception("OpenAI extraction of Brave results failed")
        return {"events": []}
