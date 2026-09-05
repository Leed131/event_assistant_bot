import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from dateutil import parser as date_parser


@dataclass
class EventCandidate:
    title: str
    start_datetime: datetime
    city: str
    registration_url: str
    source_url: str
    source_name: str
    description: str | None = None
    venue_name: str | None = None
    address: str | None = None
    end_datetime: datetime | None = None
    image_url: str | None = None
    price_text: str | None = None
    is_free: bool | None = None
    external_id: str | None = None

    @property
    def dedup_key(self) -> str:
        raw = self.external_id or f"{self.title}|{self.start_datetime.isoformat()}|{self.venue_name or ''}"
        return hashlib.sha256(raw.casefold().encode()).hexdigest()


class BaseEventSource:
    name: str
    async def fetch(self, url: str) -> list[EventCandidate]:
        raise NotImplementedError


class GenericJsonLdSource(BaseEventSource):
    name = "generic-jsonld"

    async def fetch(self, url: str) -> list[EventCandidate]:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30, headers={"User-Agent": "EventAssistantBot/0.1"}) as client:
            response = await client.get(url)
            response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        found: list[EventCandidate] = []
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                payload = json.loads(script.string or "{}")
            except json.JSONDecodeError:
                continue
            nodes: Iterable[dict] = payload if isinstance(payload, list) else [payload]
            for node in nodes:
                if isinstance(node, dict) and "@graph" in node:
                    nodes = [*nodes, *[x for x in node.get("@graph", []) if isinstance(x, dict)]]
                if not isinstance(node, dict):
                    continue
                kinds = node.get("@type", [])
                kinds = [kinds] if isinstance(kinds, str) else kinds
                if "Event" not in kinds or not node.get("name") or not node.get("startDate"):
                    continue
                location = node.get("location") or {}
                address = location.get("address") if isinstance(location, dict) else None
                if isinstance(address, dict):
                    city = address.get("addressLocality") or "Copenhagen"
                    address_text = ", ".join(str(v) for v in [address.get("streetAddress"), address.get("postalCode"), city] if v)
                else:
                    city, address_text = "Copenhagen", str(address) if address else None
                offers = node.get("offers") or {}
                offer = offers[0] if isinstance(offers, list) and offers else offers if isinstance(offers, dict) else {}
                event_url = node.get("url") or offer.get("url") or url
                found.append(EventCandidate(
                    title=node["name"], description=node.get("description"),
                    start_datetime=date_parser.isoparse(node["startDate"]),
                    end_datetime=date_parser.isoparse(node["endDate"]) if node.get("endDate") else None,
                    venue_name=location.get("name") if isinstance(location, dict) else None,
                    address=address_text, city=city,
                    registration_url=urljoin(url, event_url), source_url=urljoin(url, event_url),
                    source_name=self.name, image_url=node.get("image") if isinstance(node.get("image"), str) else None,
                    price_text=str(offer.get("price")) if offer.get("price") is not None else None,
                    is_free=str(offer.get("price", "")).strip() in {"0", "0.0"},
                    external_id=str(node.get("@id")) if node.get("@id") else None,
                ))
        return found


class DACSource(GenericJsonLdSource):
    name = "Danish Architecture Center"
