"""Real land-plot candidate search backed by torgi.gov.ru — the official Russian
state-property auction/sale portal.

This is the real-mode source for ``DataCollectorService.SearchPlots``. It queries
the public ``lotcards`` endpoint for land lots in a region and maps each lot onto
the ``PlotDataResponse`` shape the search pipeline expects.

Unlike the market-comparables client (market-service), this search KEEPS lease /
auction lots even when they carry no sale price: a lease auction is a perfectly
valid candidate for the "find a plot" scenario, and price may legitimately be the
auction start price or absent. The caller filters and ranks afterwards.

It is best-effort and never raises: on any failure (network, proxy, parsing, empty
result) it returns an empty list so the pipeline soft-degrades.

Proxy: httpx is created with ``trust_env=True`` (the default) so ``HTTPS_PROXY`` /
``HTTP_PROXY`` from the environment are honoured automatically — the РФ node routes
gov traffic through a residential proxy. TLS verification is disabled to mirror the
market-service torgi client.
"""
from __future__ import annotations

import math
import re
from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)

_SEARCH_URL = "https://torgi.gov.ru/new/api/public/lotcards/search"
# "земельный участок" — passed as the free-text query param value.
_LAND_QUERY = "земельный участок"

# Cadastral-number pattern, identical to rosreestr_client.CADASTRAL_NUMBER_RE.
_CADASTRAL_NUMBER_RE = re.compile(r"\b\d{1,2}:\d{1,2}:\d{1,10}:\d{1,10}(?:/\d+)?\b")

# Region (субъект РФ) name fragment -> torgi dynSubjRF code. Ported and extended
# from market-service/app/torgi_client.py. Keys are lowercase substrings matched
# against the requested region; an unknown region falls back to a country-wide
# search (still real data). Codes follow the ОКАТО/ГИС subject numbering used by
# torgi's dynSubjRF facet.
_REGION_TO_SUBJ: dict[str, str] = {
    "адыге": "01", "башкорт": "02", "бурят": "03", "алтай республика": "04",
    "дагестан": "05", "ингуш": "06", "кабардино": "07", "калмык": "08",
    "карачаево": "09", "карел": "10", "коми": "11", "марий": "12",
    "мордов": "13", "саха": "14", "якут": "14", "северная осети": "15",
    "осети": "15", "татарстан": "16", "тыва": "17", "тува": "17",
    "удмурт": "18", "хакас": "19", "чечен": "20", "чуваш": "21",
    "алтайский": "22", "краснодар": "23", "красноярск": "24", "приморск": "25",
    "ставропол": "26", "хабаровск": "27", "амурск": "28", "архангельск": "29",
    "астрахан": "30", "белгород": "31", "брянск": "32", "владимир": "33",
    "волгоград": "34", "вологод": "35", "воронеж": "36", "иванов": "37",
    "иркут": "38", "калининград": "39", "калуж": "40", "камчат": "41",
    "кемеров": "42", "киров": "43", "костром": "44", "курган": "45",
    "курск": "46", "ленинград": "47", "липецк": "48", "магадан": "49",
    "московская": "50", "мурман": "51", "нижегород": "52", "новгород": "53",
    "новосибир": "54", "омск": "55", "оренбург": "56", "орлов": "57",
    "пенз": "58", "пермск": "59", "псков": "60", "ростов": "61",
    "рязан": "62", "самар": "63", "саратов": "64", "сахалин": "65",
    "свердлов": "66", "смолен": "67", "тамбов": "68", "тверск": "69",
    "томск": "70", "тульск": "71", "тюмен": "72", "ульянов": "73",
    "челябин": "74", "забайкал": "75", "ярослав": "76", "москва": "77",
    "санкт-петербург": "78", "спб": "78", "питер": "78", "еврейск": "79",
    "ненецк": "83", "ханты": "86", "югра": "86", "чукот": "87",
    "ямало": "89", "ямал": "89", "крым": "91", "севастопол": "92",
}


def _subj_code(region: str) -> str:
    low = (region or "").lower()
    # Longer keys first so "московская" wins over "москва", "осети" over generic.
    for key in sorted(_REGION_TO_SUBJ, key=len, reverse=True):
        if key in low:
            return _REGION_TO_SUBJ[key]
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


def _char_name(ch: dict[str, Any]) -> str:
    return str(ch.get("name") or ch.get("characteristicName") or "").lower()


def _char_raw(ch: dict[str, Any]) -> Any:
    return _char_value(ch.get("val") if "val" in ch else ch.get("characteristicValue"))


def _area_from_characteristics(characteristics: list[dict[str, Any]]) -> float:
    for ch in characteristics or []:
        if not isinstance(ch, dict):
            continue
        if "площад" in _char_name(ch):
            raw = _char_raw(ch)
            try:
                area = float(str(raw).replace(",", ".").split()[0])
            except (TypeError, ValueError, IndexError):
                continue
            if area and not math.isnan(area):
                return area
    return 0.0


def _str_characteristic(characteristics: list[dict[str, Any]], needle: str) -> str:
    for ch in characteristics or []:
        if not isinstance(ch, dict):
            continue
        if needle in _char_name(ch):
            raw = _char_raw(ch)
            if raw:
                return str(raw)
    return ""


def _cadastral_from_characteristics(characteristics: list[dict[str, Any]]) -> str:
    for ch in characteristics or []:
        if not isinstance(ch, dict):
            continue
        name = _char_name(ch)
        if "кадастр" in name:
            raw = _char_raw(ch)
            match = _CADASTRAL_NUMBER_RE.search(str(raw or ""))
            if match:
                return match.group(0)
    return ""


def _extract_cadastral(lot: dict[str, Any], characteristics: list[dict[str, Any]]) -> str:
    """Resolve a lot's cadastral number, preferring structured fields, then a
    cadastral characteristic, then a regex over the lot name and the whole lot."""
    for key in ("cadastralNumber", "cadNumber", "cadastral_number"):
        match = _CADASTRAL_NUMBER_RE.search(str(lot.get(key) or ""))
        if match:
            return match.group(0)

    from_chars = _cadastral_from_characteristics(characteristics)
    if from_chars:
        return from_chars

    match = _CADASTRAL_NUMBER_RE.search(str(lot.get("lotName") or ""))
    if match:
        return match.group(0)

    # Last resort: scan all characteristic values, then the raw lot text.
    for ch in characteristics or []:
        if isinstance(ch, dict):
            match = _CADASTRAL_NUMBER_RE.search(str(_char_raw(ch) or ""))
            if match:
                return match.group(0)
    return ""


def _to_float(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(result) or math.isinf(result):
        return 0.0
    return result


def _coords_from_lot(lot: dict[str, Any]) -> tuple[float, float]:
    """Best-effort lat/lng. torgi sometimes carries a point geometry on the lot
    or on an estate object; fall back to (0, 0) when absent."""
    for container in (lot, lot.get("estateAddress") or {}, lot.get("lotAddress") or {}):
        if not isinstance(container, dict):
            continue
        lat = container.get("lat") or container.get("latitude")
        lng = container.get("lng") or container.get("lon") or container.get("longitude")
        if lat not in (None, "") and lng not in (None, ""):
            return _to_float(lat), _to_float(lng)
    return 0.0, 0.0


def _lot_to_plot(lot: dict[str, Any], region: str, category: str, allowed_use: str) -> dict[str, Any]:
    """Map one torgi lot onto the PlotDataResponse field set. Defensive against
    missing fields — every value has a sane default."""
    chars = lot.get("characteristics") or []
    if not isinstance(chars, list):
        chars = []

    cadastral = _extract_cadastral(lot, chars)
    area = _area_from_characteristics(chars)
    price = _to_float(lot.get("priceMin") or lot.get("priceFin") or lot.get("startPrice"))

    address = (
        _str_characteristic(chars, "адрес")
        or str(lot.get("estateAddress") or "")
        or str(lot.get("lotName") or "")
    )[:500]
    cat = _str_characteristic(chars, "категори") or category or ""
    use = _str_characteristic(chars, "использован") or _str_characteristic(chars, "разрешен") or allowed_use or ""
    status = str(lot.get("lotStatus") or lot.get("status") or "")
    lat, lng = _coords_from_lot(lot)

    return {
        "cadastral_number": cadastral,
        "address": address,
        "area": round(area, 2),
        "category": cat,
        "allowed_use": use,
        "price": round(price, 2),
        "lat": lat,
        "lng": lng,
        "status": status,
        "raw_json": {
            "source": "torgi.gov.ru",
            "lot_id": str(lot.get("id") or ""),
            "notice_number": str(lot.get("noticeNumber") or ""),
            "lot_name": str(lot.get("lotName") or "")[:500],
            "bidd_type": str((lot.get("biddType") or {}).get("name") or "") if isinstance(lot.get("biddType"), dict) else str(lot.get("biddType") or ""),
            "price_min": _to_float(lot.get("priceMin")),
            "price_fin": _to_float(lot.get("priceFin")),
            "bidd_end_time": str(lot.get("biddEndTime") or ""),
            "subject_rf": str(lot.get("subjectRF") or lot.get("dynSubjRF") or ""),
        },
    }


async def search_land_lots(
    region: str,
    category: str = "",
    allowed_use: str = "",
    area_min: float = 0.0,
    area_max: float = 0.0,
    price_min: float = 0.0,
    price_max: float = 0.0,
    limit: int = 20,
    *,
    pages: int = 4,
    page_size: int = 50,
    timeout: float = 20.0,
) -> list[dict[str, Any]]:
    """Search torgi.gov.ru for land lots in a region and return candidate plots.

    Returns a list of dicts matching the PlotDataResponse fields
    ``{cadastral_number, address, area, category, allowed_use, price, lat, lng,
    status, raw_json}``. Lease/auction lots are kept even with no/zero price.

    Area filters (``area_min`` / ``area_max``) are interpreted in HECTARES to
    match the SearchPlots contract; lot area from torgi is in m². Price filters
    are absolute roubles and are only applied to lots that actually carry a
    positive price (a priceless lease lot is never rejected on price).

    Never raises — returns ``[]`` on any error or empty result.
    """
    limit = int(limit) if limit and int(limit) > 0 else 20
    subj = _subj_code(region)

    params_base: dict[str, Any] = {"text": _LAND_QUERY, "pageSize": page_size}
    if subj:
        params_base["dynSubjRF"] = subj

    area_min_m2 = area_min * 10_000.0 if area_min else 0.0
    area_max_m2 = area_max * 10_000.0 if area_max else 0.0

    plots: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        # trust_env=True (default) → HTTPS_PROXY/HTTP_PROXY honoured automatically.
        async with httpx.AsyncClient(
            timeout=timeout,
            verify=False,
            follow_redirects=True,
            headers={"Accept": "application/json", "User-Agent": "LandScoreAI/0.1"},
        ) as client:
            for page in range(max(1, pages)):
                if len(plots) >= limit:
                    break
                params = dict(params_base, pageNumber=page)
                try:
                    resp = await client.get(_SEARCH_URL, params=params)
                    resp.raise_for_status()
                    payload = resp.json()
                except (httpx.HTTPError, ValueError) as exc:
                    log.warning("torgi_search_page_failed", page=page, error=str(exc))
                    break

                content = payload.get("content") if isinstance(payload, dict) else None
                if not isinstance(content, list) or not content:
                    break

                for lot in content:
                    if not isinstance(lot, dict):
                        continue
                    plot = _lot_to_plot(lot, region, category, allowed_use)

                    area = plot["area"]
                    if area_min_m2 and area and area < area_min_m2:
                        continue
                    if area_max_m2 and area and area > area_max_m2:
                        continue
                    price = plot["price"]
                    # Only filter on price when the lot actually has one — keep
                    # priceless lease/auction lots.
                    if price > 0:
                        if price_min and price < price_min:
                            continue
                        if price_max and price > price_max:
                            continue

                    dedupe_key = plot["cadastral_number"] or plot["raw_json"].get("lot_id") or plot["raw_json"].get("notice_number")
                    if dedupe_key and dedupe_key in seen:
                        continue
                    if dedupe_key:
                        seen.add(dedupe_key)

                    plots.append(plot)
                    if len(plots) >= limit:
                        break

                if payload.get("last") is True:
                    break
    except Exception as exc:  # noqa: BLE001 - real source must never break the RPC
        log.warning("torgi_search_failed", region=region, error=str(exc))
        return plots[:limit]

    log.info("torgi_land_search", region=region, subj=subj, count=len(plots))
    return plots[:limit]
