from __future__ import annotations

import asyncio
import concurrent.futures
import re
import statistics
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


BING_RSS_URL = "https://www.bing.com/search"
PRICE_SEARCH_DOMAINS = (
    "farpost.ru",
    "rosrealt.ru",
    "b2b-center.ru",
    "fabrikant.ru",
    "fedresurs.ru",
    "sales.lot-online.ru",
    "rad.lot-online.ru",
    "lot-online.ru",
    "move.ru",
    "restate.ru",
    "sob.ru",
    "tender.pro",
    "tektorg.ru",
)
SUPPORTED_DOMAINS = set(PRICE_SEARCH_DOMAINS) | {
    "manual",
    "avito.ru",
    "cian.ru",
    "domclick.ru",
    "realty.yandex.ru",
    "youla.ru",
}
BAD_DIRECT_PARSE_DOMAINS = {"avito.ru", "cian.ru", "domclick.ru", "realty.yandex.ru", "youla.ru"}

LAND_KEYWORDS = (
    "\u0437\u0435\u043c",
    "\u0443\u0447\u0430\u0441\u0442",
    "\u0441\u0435\u043b\u044c\u0445\u043e\u0437",
    "\u0441/\u0445",
    "\u043f\u0430\u0448",
    "\u043f\u0430\u0435\u0432",
    "\u0443\u0433\u043e\u0434",
    "\u043b\u043f\u0445",
    "\u0438\u0436\u0441",
    "\u0430\u0440\u0435\u043d\u0434",
    "\u0442\u043e\u0440\u0433",
    "\u043b\u043e\u0442",
    "\u043a\u0430\u0434\u0430\u0441\u0442\u0440",
)
HOUSE_KEYWORDS = (
    "\u043a\u0432\u0430\u0440\u0442\u0438\u0440\u0430",
    "\u043a\u043e\u043c\u043d\u0430\u0442\u0430",
    "\u0430\u043f\u0430\u0440\u0442\u0430\u043c\u0435\u043d\u0442",
    "\u0434\u043e\u043c ",
    "\u043a\u043e\u0442\u0442\u0435\u0434\u0436",
    "\u0442\u0430\u0443\u043d\u0445\u0430\u0443\u0441",
)
AGRICULTURE_WORDS = (
    "\u0441\u0435\u043b\u044c\u0445\u043e\u0437",
    "\u0441/\u0445",
    "\u043f\u0430\u0448",
    "\u0444\u0435\u0440\u043c\u0435\u0440",
    "\u043a\u0440\u0435\u0441\u0442\u044c\u044f\u043d",
    "\u0443\u0433\u043e\u0434",
)
CONSTRUCTION_WORDS = (
    "\u0438\u0436\u0441",
    "\u043b\u043f\u0445",
    "\u0441\u0442\u0440\u043e\u0438\u0442\u0435\u043b\u044c\u0441\u0442\u0432",
    "\u043f\u043e\u0441\u0435\u043b\u0435\u043d",
)


@dataclass(frozen=True)
class PriceSubject:
    cadastral_number: str
    region: str
    district: str
    category: str
    allowed_use: str
    cadastral_area_ha: float | None
    cadastral_price_rub: float | None


def build_price_analysis(
    *,
    cadastral_number: str,
    nspd: dict[str, Any],
    manual_candidates: list[dict[str, Any]] | None = None,
    max_items: int = 20,
    timeout: float = 12.0,
) -> dict[str, Any]:
    started = time.monotonic()
    subject = _subject_from_nspd(cadastral_number, nspd)
    raw_items, diagnostics = _collect_market_signals(subject, timeout=timeout)
    raw_items.extend(_manual_items(manual_candidates or []))
    classified = [_classify_item(item, subject) for item in _dedupe(raw_items)]

    included = [item for item in classified if item["score_status"] == "included_in_score"]
    signal_only = [item for item in classified if item["score_status"] == "signal_only"]
    excluded = [item for item in classified if item["score_status"] == "excluded_from_score"]

    price_per_ha_values = [
        item["price_per_ha_rub"]
        for item in included
        if isinstance(item.get("price_per_ha_rub"), int | float) and item["price_per_ha_rub"] > 0
    ]
    median_price_per_ha = round(statistics.median(price_per_ha_values), 2) if price_per_ha_values else None
    min_price_per_ha = round(min(price_per_ha_values), 2) if price_per_ha_values else None
    max_price_per_ha = round(max(price_per_ha_values), 2) if price_per_ha_values else None

    cadastral_price_per_ha = _safe_ratio(subject.cadastral_price_rub, subject.cadastral_area_ha)
    cadastral_to_market_ratio = _safe_ratio(cadastral_price_per_ha, median_price_per_ha)

    return {
        "success": True,
        "source": "Bing RSS + cadastral price",
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "subject": {
            "cadastral_number": subject.cadastral_number,
            "region": subject.region,
            "district": subject.district,
            "category": subject.category,
            "allowed_use": subject.allowed_use,
            "cadastral_area_ha": subject.cadastral_area_ha,
            "cadastral_price_rub": subject.cadastral_price_rub,
        },
        "cadastral_price_summary": {
            "price_rub": subject.cadastral_price_rub,
            "area_ha": subject.cadastral_area_ha,
            "price_per_ha_rub": round(cadastral_price_per_ha, 2) if cadastral_price_per_ha else None,
            "price_per_sotka_rub": round(cadastral_price_per_ha / 100, 2) if cadastral_price_per_ha else None,
            "source": "nspd",
        },
        "score_summary": {
            "included_count": len(included),
            "signal_only_count": len(signal_only),
            "excluded_count": len(excluded),
            "minimum_required_count": 3,
            "is_reliable": len(included) >= 3,
            "median_price_per_ha_rub": median_price_per_ha,
            "min_price_per_ha_rub": min_price_per_ha,
            "max_price_per_ha_rub": max_price_per_ha,
            "cadastral_to_market_price_per_ha_ratio": round(cadastral_to_market_ratio, 4)
            if cadastral_to_market_ratio
            else None,
            "note": (
                "\u0420\u044b\u043d\u043e\u0447\u043d\u044b\u0439 \u043e\u0440\u0438\u0435\u043d\u0442\u0438\u0440 "
                "\u0441\u0447\u0438\u0442\u0430\u0435\u0442\u0441\u044f \u043f\u0440\u0435\u0434\u0432\u0430\u0440\u0438\u0442\u0435\u043b\u044c\u043d\u044b\u043c: "
                "\u0438\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u044e\u0442\u0441\u044f \u043e\u0431\u044a\u044f\u0432\u043b\u0435\u043d\u0438\u044f, "
                "\u0442\u043e\u0440\u0433\u0438 \u0438 \u043f\u043e\u0438\u0441\u043a\u043e\u0432\u044b\u0435 \u0441\u0438\u0433\u043d\u0430\u043b\u044b, "
                "\u0430 \u043d\u0435 \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u043d\u044b\u0435 \u0441\u0434\u0435\u043b\u043a\u0438."
            ),
        },
        "similar_plots": sorted(included, key=lambda item: item.get("similarity_score", 0), reverse=True)[:max_items],
        "signals": sorted(signal_only, key=lambda item: item.get("similarity_score", 0), reverse=True)[:max_items],
        "excluded": excluded[:max_items],
        "diagnostics": diagnostics,
        "limitations": [
            "market_signals_are_not_verified_transactions",
            "search_snippets_can_miss_price_or_area",
            "direct_parsing_of_cian_avito_domclick_is_disabled_due_to_antibot_or_auth",
            "use_minimum_3_included_items_before_showing_market_median_as_reliable",
        ],
    }


async def build_price_analysis_async(**kwargs: Any) -> dict[str, Any]:
    return await asyncio.to_thread(build_price_analysis, **kwargs)


def _subject_from_nspd(cadastral_number: str, nspd: dict[str, Any]) -> PriceSubject:
    nspd = _deep_fix_text(nspd)
    address = str(nspd.get("address") or "")
    features = ((nspd.get("raw_json") or {}).get("data") or {}).get("features") or []
    first_options = {}
    if features:
        first_options = (((features[0] or {}).get("properties") or {}).get("options") or {})
    area_sqm = _to_float(nspd.get("area") or first_options.get("specified_area") or first_options.get("declared_area"))
    price = _to_float(nspd.get("price") or first_options.get("cost_value"))
    return PriceSubject(
        cadastral_number=cadastral_number,
        region=_region_from_text(address),
        district=_district_from_text(address),
        category=str(nspd.get("category") or first_options.get("land_record_category_type") or ""),
        allowed_use=str(nspd.get("allowed_use") or first_options.get("permitted_use_established_by_document") or ""),
        cadastral_area_ha=round(area_sqm / 10_000, 4) if area_sqm else None,
        cadastral_price_rub=price or None,
    )


def _collect_market_signals(subject: PriceSubject, *, timeout: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    area_sotka = round(subject.cadastral_area_ha * 100) if subject.cadastral_area_ha else None
    purpose = subject.allowed_use or subject.category
    base_queries = [
        f"{subject.region} {subject.district} \u043f\u0440\u043e\u0434\u0430\u0436\u0430 \u0437\u0435\u043c\u0435\u043b\u044c\u043d\u044b\u0439 \u0443\u0447\u0430\u0441\u0442\u043e\u043a {area_sotka or ''} \u0441\u043e\u0442\u043e\u043a \u0446\u0435\u043d\u0430",
        f"{subject.region} {subject.district} \u041b\u041f\u0425 \u0443\u0447\u0430\u0441\u0442\u043e\u043a {area_sotka or ''} \u0441\u043e\u0442\u043e\u043a",
        f"{subject.region} {subject.district} {purpose} \u0443\u0447\u0430\u0441\u0442\u043e\u043a \u0446\u0435\u043d\u0430",
        f'"{subject.cadastral_number}" \u0437\u0435\u043c\u0435\u043b\u044c\u043d\u044b\u0439 \u0443\u0447\u0430\u0441\u0442\u043e\u043a',
    ]
    queries = []
    for base in base_queries:
        base = " ".join(base.split())
        if not base:
            continue
        queries.append(base)
        for domain in PRICE_SEARCH_DOMAINS:
            queries.append(f"site:{domain} {base}")

    selected_queries = queries[:64]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda query: _bing_rss(query, timeout=timeout), selected_queries))

    items: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for result in results:
        diagnostics.append({k: result.get(k) for k in ("query", "ok", "status", "items_count", "error")})
        items.extend(result.get("items") or [])
    return items, diagnostics


def _bing_rss(query: str, *, timeout: float) -> dict[str, Any]:
    params = urllib.parse.urlencode({"mkt": "ru-RU", "cc": "ru", "q": query, "format": "rss"})
    request = urllib.request.Request(
        f"{BING_RSS_URL}?{params}",
        headers={"User-Agent": "LandScorePriceAnalysis/0.1", "Accept": "application/rss+xml,application/xml,text/xml,*/*"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8", errors="replace")
        parsed = _parse_rss(text)
        return {"query": query, "ok": True, "status": 200, "items_count": len(parsed), "items": parsed}
    except Exception as exc:
        return {"query": query, "ok": False, "error": str(exc), "items": []}


def _parse_rss(text: str) -> list[dict[str, Any]]:
    root = ET.fromstring(text)
    result = []
    for item in root.findall(".//item"):
        title = _fix_mojibake(item.findtext("title") or "")
        url = item.findtext("link") or ""
        snippet = _fix_mojibake(item.findtext("description") or "")
        result.append({"title": title, "url": url, "snippet": snippet, "domain": _domain(url)})
    return result


def _manual_items(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for index, candidate in enumerate(candidates, start=1):
        title = _fix_mojibake(str(candidate.get("title") or candidate.get("name") or f"Manual candidate {index}"))
        url = str(candidate.get("url") or f"manual://candidate/{index}")
        result.append(
            {
                "title": title,
                "url": url,
                "snippet": _fix_mojibake(str(candidate.get("snippet") or candidate.get("description") or "")),
                "domain": _domain(url) if "://" in url and not url.startswith("manual://") else "manual",
                "price_rub": _to_float(candidate.get("price_rub") or candidate.get("price")),
                "area_ha": _to_float(candidate.get("area_ha")),
                "area_sqm": _to_float(candidate.get("area_sqm")),
                "deal_type": candidate.get("deal_type") or "",
                "source": "manual",
            }
        )
    return result


def _classify_item(item: dict[str, Any], subject: PriceSubject) -> dict[str, Any]:
    item = _deep_fix_text(item)
    text = _normalize_text(f"{item.get('title', '')} {item.get('snippet', '')}")
    domain = item.get("domain") or _domain(item.get("url", ""))
    explicit_area_ha = _to_float(item.get("area_ha"))
    explicit_area_sqm = _to_float(item.get("area_sqm"))
    price = _to_float(item.get("price_rub")) or _extract_price_rub(text)
    area_ha = explicit_area_ha or (explicit_area_sqm / 10_000 if explicit_area_sqm else None) or _extract_area_ha(text)
    price_per_ha = _safe_ratio(price, area_ha)
    deal_type = str(item.get("deal_type") or "").strip() or _deal_type(text)
    reasons: list[str] = []
    penalties: list[str] = []
    status = "included_in_score"

    if domain not in SUPPORTED_DOMAINS:
        status = "excluded_from_score"
        reasons.append("\u0418\u0441\u0442\u043e\u0447\u043d\u0438\u043a \u043d\u0435 \u0432\u0445\u043e\u0434\u0438\u0442 \u0432 \u0441\u043f\u0438\u0441\u043e\u043a \u043f\u043e\u0434\u0434\u0435\u0440\u0436\u0430\u043d\u043d\u044b\u0445 \u0434\u043e\u043c\u0435\u043d\u043e\u0432")
    if domain in BAD_DIRECT_PARSE_DOMAINS:
        status = "signal_only"
        reasons.append("\u0418\u0441\u0442\u043e\u0447\u043d\u0438\u043a \u0438\u043c\u0435\u0435\u0442 \u0430\u043d\u0442\u0438\u0431\u043e\u0442/\u0430\u0432\u0442\u043e\u0440\u0438\u0437\u0430\u0446\u0438\u044e; \u0438\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u0435\u043c \u0442\u043e\u043b\u044c\u043a\u043e \u043f\u043e\u0438\u0441\u043a\u043e\u0432\u044b\u0439 \u0441\u0438\u0433\u043d\u0430\u043b")
    if domain != "manual" and not any(keyword in text for keyword in LAND_KEYWORDS):
        status = "excluded_from_score"
        reasons.append("\u0412 \u0442\u0435\u043a\u0441\u0442\u0435 \u043d\u0435\u0442 \u043d\u0430\u0434\u0435\u0436\u043d\u044b\u0445 \u043f\u0440\u0438\u0437\u043d\u0430\u043a\u043e\u0432 \u0437\u0435\u043c\u0435\u043b\u044c\u043d\u043e\u0433\u043e \u0443\u0447\u0430\u0441\u0442\u043a\u0430")
    if any(keyword in text for keyword in HOUSE_KEYWORDS) and "\u0443\u0447\u0430\u0441\u0442" not in text:
        status = "excluded_from_score"
        reasons.append("\u041f\u043e\u0445\u043e\u0436\u0435 \u043d\u0430 \u0436\u0438\u043b\u043e\u0439 \u043e\u0431\u044a\u0435\u043a\u0442 \u0431\u0435\u0437 \u0441\u0430\u043c\u043e\u0441\u0442\u043e\u044f\u0442\u0435\u043b\u044c\u043d\u043e\u0433\u043e \u0437\u0435\u043c\u0435\u043b\u044c\u043d\u043e\u0433\u043e \u0443\u0447\u0430\u0441\u0442\u043a\u0430")
    if not price:
        status = _weaken(status)
        reasons.append("\u041d\u0435\u0442 \u0446\u0435\u043d\u044b, \u043d\u0435\u043b\u044c\u0437\u044f \u043f\u043e\u0441\u0447\u0438\u0442\u0430\u0442\u044c \u0446\u0435\u043d\u0443 \u0437\u0430 \u0433\u0435\u043a\u0442\u0430\u0440")
    if not area_ha:
        status = _weaken(status)
        reasons.append("\u041d\u0435\u0442 \u043f\u043b\u043e\u0449\u0430\u0434\u0438, \u043d\u0435\u043b\u044c\u0437\u044f \u043f\u043e\u0441\u0447\u0438\u0442\u0430\u0442\u044c \u0446\u0435\u043d\u0443 \u0437\u0430 \u0433\u0435\u043a\u0442\u0430\u0440")
    if deal_type == "rent":
        status = "signal_only"
        reasons.append("\u0410\u0440\u0435\u043d\u0434\u0430 \u043d\u0435 \u0441\u043c\u0435\u0448\u0438\u0432\u0430\u0435\u0442\u0441\u044f \u0441 \u043f\u0440\u043e\u0434\u0430\u0436\u0435\u0439 \u0432 \u0440\u0430\u0441\u0447\u0435\u0442\u0435 \u0446\u0435\u043d\u044b")
    if subject.cadastral_area_ha and area_ha:
        ratio = area_ha / subject.cadastral_area_ha
        if ratio < 0.33 or ratio > 3:
            status = "excluded_from_score"
            reasons.append("\u041f\u043b\u043e\u0449\u0430\u0434\u044c \u043e\u0442\u043b\u0438\u0447\u0430\u0435\u0442\u0441\u044f \u043e\u0442 \u0438\u0441\u0445\u043e\u0434\u043d\u043e\u0433\u043e \u0443\u0447\u0430\u0441\u0442\u043a\u0430 \u0431\u043e\u043b\u0435\u0435 \u0447\u0435\u043c \u0432 3 \u0440\u0430\u0437\u0430")
        elif ratio < 0.67 or ratio > 1.5:
            penalties.append("\u041f\u043b\u043e\u0449\u0430\u0434\u044c \u0437\u0430\u043c\u0435\u0442\u043d\u043e \u043e\u0442\u043b\u0438\u0447\u0430\u0435\u0442\u0441\u044f \u043e\u0442 \u0438\u0441\u0445\u043e\u0434\u043d\u043e\u0433\u043e \u0443\u0447\u0430\u0441\u0442\u043a\u0430")
    if _is_agriculture_subject(subject) and any(word in text for word in CONSTRUCTION_WORDS) and not any(
        word in text for word in AGRICULTURE_WORDS
    ):
        status = "excluded_from_score"
        reasons.append("\u0412\u0420\u0418/\u043d\u0430\u0437\u043d\u0430\u0447\u0435\u043d\u0438\u0435 \u043f\u043e\u0445\u043e\u0436\u0435 \u043d\u0430 \u0418\u0416\u0421/\u041b\u041f\u0425, \u0430 \u0438\u0441\u0445\u043e\u0434\u043d\u044b\u0439 \u0443\u0447\u0430\u0441\u0442\u043e\u043a \u0441\u0435\u043b\u044c\u0445\u043e\u0437\u043d\u0430\u0437\u043d\u0430\u0447\u0435\u043d\u0438\u044f")

    similarity = _similarity_score(
        f"{text} {item.get('url', '')}",
        subject,
        price=price,
        area_ha=area_ha,
        penalties=penalties,
    )
    if status == "included_in_score" and similarity < 45:
        status = "signal_only"
        reasons.append("\u0421\u043b\u0430\u0431\u0430\u044f \u043f\u043e\u0445\u043e\u0436\u0435\u0441\u0442\u044c \u043f\u043e \u0440\u0435\u0433\u0438\u043e\u043d\u0443, \u043d\u0430\u0437\u043d\u0430\u0447\u0435\u043d\u0438\u044e \u0438\u043b\u0438 \u043f\u043b\u043e\u0449\u0430\u0434\u0438")
    if not reasons:
        reasons.append("\u0415\u0441\u0442\u044c \u0446\u0435\u043d\u0430, \u043f\u043b\u043e\u0449\u0430\u0434\u044c \u0438 \u0431\u0430\u0437\u043e\u0432\u0430\u044f \u0441\u043e\u043f\u043e\u0441\u0442\u0430\u0432\u0438\u043c\u043e\u0441\u0442\u044c \u0441 \u0438\u0441\u0445\u043e\u0434\u043d\u044b\u043c \u0443\u0447\u0430\u0441\u0442\u043a\u043e\u043c")

    return {
        **item,
        "domain": domain,
        "deal_type": deal_type,
        "price_rub": price,
        "area_ha": area_ha,
        "price_per_ha_rub": round(price_per_ha, 2) if price_per_ha else None,
        "price_per_sotka_rub": round(price_per_ha / 100, 2) if price_per_ha else None,
        "similarity_score": similarity,
        "score_status": status,
        "included_in_score": status == "included_in_score",
        "reasons": reasons,
        "penalties": penalties,
    }


def _similarity_score(
    text: str,
    subject: PriceSubject,
    *,
    price: float | None,
    area_ha: float | None,
    penalties: list[str],
) -> int:
    score = 0
    if "manual://candidate/" in text:
        score += 45
    if subject.region and _normalize_text(subject.region) in text:
        score += 25
    if subject.district and _normalize_text(subject.district) in text:
        score += 20
    if _is_agriculture_subject(subject) and any(word in text for word in AGRICULTURE_WORDS):
        score += 20
    if price:
        score += 10
    if area_ha:
        score += 10
    if subject.cadastral_area_ha and area_ha:
        ratio = area_ha / subject.cadastral_area_ha
        if 0.67 <= ratio <= 1.5:
            score += 15
        elif 0.33 <= ratio <= 3:
            score += 8
    if "\u043a\u0430\u0434\u0430\u0441\u0442\u0440" in text:
        score += 5
    score -= len(penalties) * 7
    return max(0, min(100, score))


def _extract_price_rub(text: str) -> float | None:
    patterns = [
        r"(\d[\d\s.,]{2,})\s*(?:\u20bd|\u0440\u0443\u0431(?:\.|\u043b\u0435\u0439|\u043b\u044f|\u043b\u044c)?|\u0440\.)",
        r"(?:\u0446\u0435\u043d\u0430|\u0441\u0442\u043e\u0438\u043c\u043e\u0441\u0442\u044c|\u043d\u0430\u0447\u0430\u043b\u044c\u043d\u0430\u044f \u0446\u0435\u043d\u0430|\u0441\u0442\u0430\u0440\u0442\u043e\u0432\u0430\u044f \u0446\u0435\u043d\u0430)\D{0,20}(\d[\d\s.,]{2,})",
    ]
    values = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.I):
            value = _number(match.group(1))
            if value and value >= 10_000:
                values.append(value)
    return max(values) if values else None


def _extract_area_ha(text: str) -> float | None:
    patterns = [
        (r"(\d[\d\s.,]*)\s*(?:\u0433\u0430|\u0433\u0435\u043a\u0442\u0430\u0440)", 1.0),
        (r"(\d[\d\s.,]*)\s*(?:\u0441\u043e\u0442|\u0441\u043e\u0442\u043a)", 0.01),
        (r"(\d[\d\s.,]*)\s*(?:\u043a\u0432\.?\s*\u043c|\u043c2|\u043c\u00b2)", 0.0001),
    ]
    values = []
    for pattern, multiplier in patterns:
        for match in re.finditer(pattern, text, flags=re.I):
            value = _number(match.group(1))
            if value and value > 0:
                values.append(value * multiplier)
    plausible = [value for value in values if 0.01 <= value <= 100_000]
    return round(max(plausible), 4) if plausible else None


def _deal_type(text: str) -> str:
    if "\u0430\u0440\u0435\u043d\u0434" in text:
        return "rent"
    if "\u0442\u043e\u0440\u0433" in text or "\u0430\u0443\u043a\u0446\u0438\u043e\u043d" in text or "\u043b\u043e\u0442" in text:
        return "auction"
    return "sale"


def _region_from_text(text: str) -> str:
    text = _fix_mojibake(text)
    prefix_match = re.search(r"(\u043a\u0440\u0430\u0439|\u043e\u0431\u043b\u0430\u0441\u0442\u044c|\u0440\u0435\u0441\u043f\u0443\u0431\u043b\u0438\u043a\u0430)\s+([\u0410-\u042f\u0430-\u044f\u0401\u0451-]+)", text, re.I)
    if prefix_match:
        return f"{prefix_match.group(2).strip()} {prefix_match.group(1).strip()}"
    match = re.search(r"([\u0410-\u042f\u0430-\u044f\u0401\u0451-]+\s+(?:\u043a\u0440\u0430\u0439|\u043e\u0431\u043b\u0430\u0441\u0442\u044c|\u0440\u0435\u0441\u043f\u0443\u0431\u043b\u0438\u043a\u0430))", text, re.I)
    if match:
        return match.group(1).strip()
    if "\u0421\u0442\u0430\u0432\u0440\u043e\u043f\u043e\u043b" in text:
        return "\u0421\u0442\u0430\u0432\u0440\u043e\u043f\u043e\u043b\u044c\u0441\u043a\u0438\u0439 \u043a\u0440\u0430\u0439"
    return ""


def _district_from_text(text: str) -> str:
    text = _fix_mojibake(text)
    match = re.search(r"(?:\u0440-\u043d|\u0440\u0430\u0439\u043e\u043d|\u043e\u043a\u0440\u0443\u0433)\s+([^,.;]+)", text, re.I)
    if match:
        value = match.group(1).strip()
        return value if "\u0440\u0430\u0439\u043e\u043d" in value.lower() else f"{value} \u0440\u0430\u0439\u043e\u043d"
    if "\u0413\u0440\u0430\u0447\u0435\u0432" in text or "\u0413\u0440\u0430\u0447\u0451\u0432" in text:
        return "\u0413\u0440\u0430\u0447\u0435\u0432\u0441\u043a\u0438\u0439 \u0440\u0430\u0439\u043e\u043d"
    if "\u0428\u043f\u0430\u043a\u043e\u0432" in text:
        return "\u0428\u043f\u0430\u043a\u043e\u0432\u0441\u043a\u0438\u0439 \u0440\u0430\u0439\u043e\u043d"
    return ""


def _is_agriculture_subject(subject: PriceSubject) -> bool:
    text = _normalize_text(f"{subject.category} {subject.allowed_use}")
    return any(word in text for word in AGRICULTURE_WORDS) or "\u0441\u0435\u043b\u044c\u0441\u043a\u043e\u0445\u043e\u0437\u044f\u0439" in text


def _fix_mojibake(value: str) -> str:
    if not value or ("Р" not in value and "С" not in value):
        return value
    try:
        fixed = value.encode("cp1251", errors="strict").decode("utf-8", errors="strict")
    except UnicodeError:
        return value
    return fixed if fixed.count("\ufffd") == 0 else value


def _deep_fix_text(value: Any) -> Any:
    if isinstance(value, str):
        return _fix_mojibake(value)
    if isinstance(value, list):
        return [_deep_fix_text(item) for item in value]
    if isinstance(value, dict):
        return {key: _deep_fix_text(item) for key, item in value.items()}
    return value


def _weaken(status: str) -> str:
    return status if status == "excluded_from_score" else "signal_only"


def _number(value: str) -> float | None:
    cleaned = value.replace("\xa0", " ").replace(" ", "").replace(",", ".")
    if cleaned.count(".") > 1:
        cleaned = cleaned.replace(".", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _to_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _safe_ratio(numerator: float | None, denominator: float | None) -> float | None:
    if not numerator or not denominator:
        return None
    return numerator / denominator


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", _fix_mojibake(text).replace("\xa0", " ")).strip().lower()


def _domain(url: str) -> str:
    host = urlparse(url).netloc.lower().split("@")[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host


def _dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    result = []
    for item in items:
        key = item.get("url") or item.get("title")
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result
