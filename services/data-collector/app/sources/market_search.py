from __future__ import annotations

import asyncio
import time
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urlparse

import httpx

MARKET_ALLOWED_DOMAINS = {
    # Existing real-estate/open listing sources.
    "avito.ru",
    "cian.ru",
    "domclick.ru",
    "realty.yandex.ru",
    "yandex.ru",
    "youla.ru",
    "irr.ru",
    "farpost.ru",
    "move.ru",
    "mirkvartir.ru",
    "gdeetotdom.ru",
    "rosrealt.ru",
    "n1.ru",
    "etagi.com",
    "restate.ru",
    "sob.ru",
    # Auction and tender sources extracted from "Torgi SK 12.05.26.xlsx".
    "trade.saby.ru",
    "torgi.gov.ru",
    "fedresurs.ru",
    "b2b-center.ru",
    "portal-da.ru",
    "bankrot.cdtrf.ru",
    "m-ets.ru",
    "sales.lot-online.ru",
    "privatization.lot-online.ru",
    "zalog.lot-online.ru",
    "rad.lot-online.ru",
    "fabrikant.ru",
    "utp.sberbank-ast.ru",
    "torgiasv.ru",
    "nistp.ru",
    "bankrupt.centerr.ru",
    "bidzaar.com",
    "roseltorg.ru",
    "etpgpb.ru",
    "new.etpgpb.ru",
    "atctrade.ru",
    "torgi.rts-tender.ru",
    "i.rts-tender.ru",
    "utender.ru",
    "tektorg.ru",
    "etp-aktiv.ru",
    "autosale.ru",
    "tender.pro",
    "akosta.info",
    "etp-profit.ru",
    "bankrupt.utpl.ru",
    "komission.vtb.ru",
    "sistematorg.com",
    "regtorg.com",
    "bankrupt.tender.one",
    "bankrupt.etpu.ru",
    "rus-on.ru",
    "etp.torgi82.ru",
    "torgibankrot.ru",
    "xn--e1adnd0h.xn--d1aqf.xn--p1ai",
    "tenderstandart.ru",
    "ausib.ru",
    "sovcombank.ru",
    "bankruptcy.gloriaservice.ru",
    "sale.etprf.ru",
    "bankrot.vertrades.ru",
    "etp.interrao-zakupki.ru",
    "sale.zakazrf.ru",
    "ru-trade24.ru",
    "eshoprzd.ru",
    "etpugra.ru",
    "torgi.tatneft.ru",
}

MARKET_SEARCH_DOMAINS = (
    "torgi.gov.ru",
    "trade.saby.ru",
    "fedresurs.ru",
    "b2b-center.ru",
    "portal-da.ru",
    "sales.lot-online.ru",
    "privatization.lot-online.ru",
    "fabrikant.ru",
    "utp.sberbank-ast.ru",
    "roseltorg.ru",
    "m-ets.ru",
    "torgi.rts-tender.ru",
    "tektorg.ru",
    "etpgpb.ru",
    "tender.pro",
    "avito.ru",
    "cian.ru",
    "domclick.ru",
    "realty.yandex.ru",
    "youla.ru",
)

MARKET_SEARCH_CONCURRENCY = 8


class MarketSearchClient:
    def __init__(self, base_url: str = "https://www.bing.com/search", timeout: float = 12.0) -> None:
        self.base_url = base_url
        self.timeout = timeout

    async def collect(self, *, cadastral_number: str, region: str, district: str = "") -> dict[str, Any]:
        started = time.monotonic()
        base_queries = [
            f"{region} {district} продажа сельхозземли гектар".strip(),
            f"{region} аренда земли сельхозназначения цена за гектар".strip(),
            f"\"{cadastral_number}\" земельный участок",
        ]
        queries = _site_restricted_queries(base_queries)
        semaphore = asyncio.Semaphore(MARKET_SEARCH_CONCURRENCY)
        items: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        diagnostics = []

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            results = await asyncio.gather(
                *(self._search_limited(client=client, query=query, semaphore=semaphore) for query in queries),
                return_exceptions=True,
            )

        for query, result in zip(queries, results, strict=False):
            if isinstance(result, Exception):
                diagnostics.append({"query": query, "ok": False, "error": str(result)})
                continue
            diagnostics.append(result["diagnostics"])
            items.extend(result["accepted"])
            rejected.extend(result["rejected"][:3])

        deduped = _dedupe_items(items)
        return {
            "success": True,
            "source": "Bing RSS market search",
            "elapsedMs": int((time.monotonic() - started) * 1000),
            "itemsCount": len(deduped),
            "items": deduped[:20],
            "allowedDomains": sorted(MARKET_ALLOWED_DOMAINS),
            "searchDomains": list(MARKET_SEARCH_DOMAINS),
            "rejectedSample": _dedupe_items(rejected)[:20],
            "diagnostics": diagnostics,
            "limitations": [
                "search_results_are_market_signals_not_verified_transactions",
                "search_results_are_limited_to_allowed_market_domains",
                "bing_rss_can_ignore_or_relax_site_filters_when_no_good_results",
                "prices_need_manual_or_detail_page_extraction",
            ],
        }

    async def _search_limited(
        self,
        *,
        client: httpx.AsyncClient,
        query: str,
        semaphore: asyncio.Semaphore,
    ) -> dict[str, Any]:
        async with semaphore:
            return await self._search_one(client=client, query=query)

    async def _search_one(self, *, client: httpx.AsyncClient, query: str) -> dict[str, Any]:
        response = await client.get(
            self.base_url,
            params={"mkt": "ru-RU", "cc": "ru", "q": query, "format": "rss"},
            headers={"Accept": "application/rss+xml,application/xml,text/xml,*/*"},
        )
        response.raise_for_status()
        parsed_items = _parse_rss(response.text)
        accepted_items, rejected_items = _filter_allowed_items(parsed_items)
        return {
            "diagnostics": {
                "query": query,
                "ok": True,
                "status": response.status_code,
                "items": len(parsed_items),
                "accepted": len(accepted_items),
                "rejected": len(rejected_items),
            },
            "accepted": accepted_items,
            "rejected": rejected_items,
        }


def _site_restricted_queries(base_queries: list[str]) -> list[str]:
    queries = []
    for base_query in base_queries:
        if not base_query:
            continue
        for domain in MARKET_SEARCH_DOMAINS:
            queries.append(f"site:{domain} {base_query}")
    return queries


def _parse_rss(text: str) -> list[dict[str, Any]]:
    root = ET.fromstring(text)
    items = []
    for item in root.findall(".//item"):
        title = item.findtext("title") or ""
        link = item.findtext("link") or ""
        description = item.findtext("description") or ""
        if title or link:
            items.append({"title": title, "url": link, "snippet": description})
    return items


def _dedupe_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    result = []
    for item in items:
        key = item.get("url") or item.get("title")
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _filter_allowed_items(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted = []
    rejected = []
    for item in items:
        domain = _domain(item.get("url", ""))
        enriched = {**item, "domain": domain}
        if _is_allowed_domain(domain):
            accepted.append(enriched)
        else:
            rejected.append(enriched)
    return accepted, rejected


def _domain(url: str) -> str:
    host = urlparse(url).netloc.lower().split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def _is_allowed_domain(domain: str) -> bool:
    if not domain:
        return False
    return any(domain == allowed or domain.endswith(f".{allowed}") for allowed in MARKET_ALLOWED_DOMAINS)
