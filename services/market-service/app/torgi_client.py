"""Real market comparables from torgi.gov.ru — the official Russian state-property
auction/sale portal.

This is a genuine, free, public source of land prices (sale and auction lots).
It is best-effort: land lots are often lease auctions with no sale price, so the
priced-comparable yield per region varies. The caller treats an empty result as
"no live comparables" and falls back to other signals (cadastral value, regional
estimate). This module never raises — on any failure it returns an empty list.
"""
from __future__ import annotations

import math
from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)

_SEARCH_URL = "https://torgi.gov.ru/new/api/public/lotcards/search"
# "земельный участок" url-encoded once; httpx will pass it as a query param value.
_LAND_QUERY = "земельный участок"

# Region (субъект РФ) name -> torgi dynSubjRF code. Covers the common ones; an
# unknown region simply means a country-wide search (still real data).
_REGION_TO_SUBJ: dict[str, str] = {
    "московская": "50", "москва": "77", "санкт-петербург": "78", "ленинградская": "47",
    "краснодарский": "23", "ростовская": "61", "ставропольский": "26", "татарстан": "16",
    "свердловская": "66", "новосибирская": "54", "нижегородская": "52", "самарская": "63",
    "воронежская": "36", "тульская": "71", "белгородская": "31", "калужская": "40",
    "владимирская": "33", "ярославская": "76", "тверская": "69", "рязанская": "62",
    "башкортостан": "02", "челябинская": "74", "крым": "91", "калининградская": "39",
}


def _subj_code(region: str) -> str:
    low = (region or "").lower()
    for key, code in _REGION_TO_SUBJ.items():
        if key in low:
            return code
    return ""


def _char_value(value: Any) -> Any:
    """torgi characteristic values come as a bare string, a dict {value:...},
    or a list of such dicts. Pull out the human value."""
    if isinstance(value, dict):
        return value.get("value")
    if isinstance(value, list) and value:
        first = value[0]
        return first.get("value") if isinstance(first, dict) else first
    return value


def _area_from_characteristics(characteristics: list[dict[str, Any]]) -> float:
    for ch in characteristics or []:
        name = str(ch.get("name") or ch.get("characteristicName") or "").lower()
        if "площад" in name:
            raw = _char_value(ch.get("val") if "val" in ch else ch.get("characteristicValue"))
            try:
                area = float(str(raw).replace(",", ".").split()[0])
            except (TypeError, ValueError, IndexError):
                continue
            if area and not math.isnan(area):
                return area
    return 0.0


def _str_characteristic(characteristics: list[dict[str, Any]], needle: str) -> str:
    for ch in characteristics or []:
        name = str(ch.get("name") or ch.get("characteristicName") or "").lower()
        if needle in name:
            raw = _char_value(ch.get("val") if "val" in ch else ch.get("characteristicValue"))
            if raw:
                return str(raw)
    return ""


def fetch_land_comparables(
    *,
    region: str,
    category: str = "",
    allowed_use: str = "",
    pages: int = 3,
    page_size: int = 50,
    timeout: float = 15.0,
) -> list[dict[str, Any]]:
    """Return real priced land comparables for a region from torgi.gov.ru.

    Each item: {price, area, price_per_sqm, region, category, allowed_use,
    source, listed_at, address}. Only lots with a positive price AND area and a
    sane price/m² are kept. Never raises.
    """
    subj = _subj_code(region)
    params_base: dict[str, Any] = {"text": _LAND_QUERY, "pageSize": page_size}
    if subj:
        params_base["dynSubjRF"] = subj

    comps: list[dict[str, Any]] = []
    try:
        with httpx.Client(timeout=timeout, verify=False, follow_redirects=True,
                          headers={"Accept": "application/json", "User-Agent": "LandScoreAI/0.1"}) as client:
            for page in range(max(1, pages)):
                params = dict(params_base, pageNumber=page)
                try:
                    resp = client.get(_SEARCH_URL, params=params)
                    resp.raise_for_status()
                    payload = resp.json()
                except (httpx.HTTPError, ValueError) as exc:
                    log.warning("torgi_page_failed", page=page, error=str(exc))
                    break

                content = payload.get("content") or []
                if not isinstance(content, list) or not content:
                    break

                for lot in content:
                    if not isinstance(lot, dict):
                        continue
                    price = lot.get("priceMin") or lot.get("priceFin") or 0
                    try:
                        price = float(price or 0)
                    except (TypeError, ValueError):
                        price = 0.0
                    chars = lot.get("characteristics") or []
                    area = _area_from_characteristics(chars)
                    if price <= 0 or area <= 0:
                        continue
                    ppsqm = price / area
                    if not (1.0 < ppsqm < 5_000_000.0):
                        continue
                    cat = _str_characteristic(chars, "назначение") or category
                    use = _str_characteristic(chars, "использован") or allowed_use
                    comps.append({
                        "id": str(lot.get("id") or lot.get("noticeNumber") or ""),
                        "price": round(price, 2),
                        "area": round(area, 2),
                        "price_per_sqm": round(ppsqm, 2),
                        "region": region,
                        "category": cat,
                        "allowed_use": use,
                        "source": "torgi.gov.ru",
                        "listed_at": str(lot.get("biddEndTime") or lot.get("createDate") or ""),
                        "address": str(lot.get("lotName") or "")[:200],
                    })

                if payload.get("last") is True:
                    break
    except Exception as exc:  # noqa: BLE001 - real market source must never break the RPC
        log.warning("torgi_fetch_failed", error=str(exc))
        return comps

    log.info("torgi_comparables", region=region, subj=subj, count=len(comps))
    return comps
