from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from app.pipeline.context import AgentContext


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _round(value: Any, digits: int = 4) -> float | None:
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _area_ha_from_sqm(value: Any) -> float | None:
    area = _round(value)
    if area is None:
        return None
    # NSPD/EGRN area is always m²; convert to ha unconditionally (a 945 m² plot must
    # become 0.0945 ha, not stay 945). The old `> 1000` guard broke parcels ≤1000 m².
    return round(area / 10_000, 4) if area else 0.0


def _soil_band(band: dict[str, Any]) -> dict[str, Any]:
    """Flatten one SoilGrids depth band, including nitrogen/cec when present."""
    band = band or {}
    return {
        "clay_percent": (band.get("clay") or {}).get("percent"),
        "sand_percent": (band.get("sand") or {}).get("percent"),
        "silt_percent": (band.get("silt") or {}).get("percent"),
        "soc_percent": (band.get("soc") or {}).get("percent"),
        "ph": _ph((band.get("phh2o") or {}).get("mean")),
        # nitrogen/cec carry no `percent`; surface their raw mean + unit so the
        # client can show fertility indicators when the source provides them.
        "nitrogen": (band.get("nitrogen") or {}).get("mean"),
        "nitrogen_unit": (band.get("nitrogen") or {}).get("unit"),
        "cec": (band.get("cec") or {}).get("mean"),
        "cec_unit": (band.get("cec") or {}).get("unit"),
    }


def _soil_summary(soil_payload: dict[str, Any]) -> dict[str, Any]:
    soil = soil_payload.get("soil") or {}
    topsoil = soil.get("topsoil") or {}
    subsoil = soil.get("subsoil") or {}
    # SoilGrids currently summarizes only 0-5cm (topsoil) and 15-30cm (subsoil);
    # surface the intermediate 5-15cm band too if a future source exposes it.
    midsoil = soil.get("midsoil") or soil.get("subsoil_5_15") or {}
    summary = {
        "source": soil_payload.get("source"),
        "confidence": soil.get("sourceConfidence"),
        "spatial_resolution": soil.get("spatialResolution"),
        "texture_class": soil.get("textureClass"),
        "topsoil": _soil_band(topsoil),
        "subsoil": _soil_band(subsoil),
        "limitations": soil_payload.get("limitations") or [],
    }
    if midsoil:
        summary["midsoil"] = _soil_band(midsoil)
    return summary


def _ph(value: Any) -> float | None:
    ph = _round(value, 2)
    if ph is None:
        return None
    return round(ph / 10, 2) if ph > 14 else ph


def _infrastructure_summary(rich: dict[str, Any], raw_osm: dict[str, Any]) -> dict[str, Any]:
    """Prefer the InfrastructureAgent's rich analysis (road access, utilities,
    distances, accessibility_score, protected zones, locality). Only when that
    is absent/unavailable do we fall back to reshaping the raw OSM dataset.
    """
    rich = rich or {}
    if rich.get("available"):
        return {
            "available": True,
            "has_road_access": rich.get("has_road_access"),
            "distance_to_city_km": rich.get("distance_to_city_km"),
            "distance_to_road_km": rich.get("distance_to_road_km"),
            "distance_to_railway_km": rich.get("distance_to_railway_km"),
            "utilities": rich.get("utilities") or {},
            "protected_zones": rich.get("protected_zones") or [],
            "accessibility_score": rich.get("accessibility_score"),
            "region": rich.get("region"),
            "district": rich.get("district"),
            "locality": rich.get("locality"),
            "distances_km": rich.get("distances_km") or {},
        }
    if rich and rich.get("available") is False:
        # geo-service / coords unavailable: pass through the explicit reason.
        return {"available": False, "reason": rich.get("reason")}

    payload = raw_osm or {}
    samples = payload.get("samples") or {}
    return {
        "source": payload.get("source"),
        "confidence": payload.get("sourceConfidence"),
        "radius_meters": payload.get("radiusMeters"),
        "counts": payload.get("counts") or {},
        "signals": payload.get("signals") or {},
        "sample_power_objects": (samples.get("power") or [])[:3],
        "limitations": payload.get("limitations") or [],
    }


def _dataset_parts(ctx: AgentContext) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    dataset = ctx.get("plot_dataset") or {}
    nspd = ctx.get("nspd") or _json_dict(dataset.get("nspd_json"))
    soil = _json_dict(dataset.get("soil_json"))
    infrastructure = _json_dict(dataset.get("infrastructure_json"))
    market = _json_dict(dataset.get("market_json"))
    return nspd, soil, infrastructure, market


def _warnings_match(warnings: list[Any], *needles: str) -> bool:
    for item in warnings:
        text = str(item).lower()
        if all(n in text for n in needles):
            return True
    return False


def _data_quality(
    ctx: AgentContext,
    *,
    nspd: dict[str, Any],
    soil: dict[str, Any],
    infrastructure: dict[str, Any],
    market: dict[str, Any],
) -> dict[str, Any]:
    """Per-section data-quality passport.

    Each section gets a status of verified | partial | unverified | unavailable
    plus its source, and (where relevant) confidence / reason / user_message.
    Everything degrades gracefully — agents can fail and data can be absent.
    """
    data_request = ctx.get("DataRequestAgent") or {}
    warnings = list(data_request.get("warnings") or [])
    dataset_available = bool(data_request.get("dataset_available"))
    spatial_layers_available = bool(data_request.get("spatial_layers_available"))
    nspd_failed = _warnings_match(warnings, "plot_dataset", "failed") or _warnings_match(warnings, "nspd", "failed")
    nspd_unavailable = nspd_failed or any(
        "nspd" in str(item).lower() and ("failed" in str(item).lower() or "connection" in str(item).lower())
        for item in warnings
    )

    egrn = ctx.get("egrn") or (data_request.get("egrn") or {})
    egrn_failed = _warnings_match(warnings, "egrn", "failed")
    egrn_owner = (egrn or {}).get("owner") or (ctx.plot.egrn_data or {}).get("owner")
    ownership_missing = (not egrn_owner) or egrn_failed

    infra = infrastructure if isinstance(infrastructure, dict) else {}
    infra_rich = ctx.get("infrastructure_summary") or {}
    infra_available = bool(infra_rich.get("available")) or bool(infra)

    map_summary = ctx.get("map_summary") or (ctx.get("GeoAgent") or {}).get("map_summary") or {}
    postgis_available = spatial_layers_available or bool(map_summary)

    market_rich = ctx.get("market_summary") or {}
    market_available = bool(market_rich.get("available")) or bool(market)
    market_count = market_rich.get("comparables_count") or market.get("itemsCount") or market.get("items_count") or 0

    sections: dict[str, Any] = {}

    # NSPD (public cadastral)
    sections["nspd"] = {
        "status": "unavailable" if nspd_unavailable else ("verified" if (dataset_available or nspd) else "unverified"),
        "source": "NSPD",
        "reason": "Публичный источник NSPD недоступен" if nspd_unavailable else None,
    }

    # EGRN ownership / rights
    sections["ownership"] = {
        "status": "unavailable" if ownership_missing else "verified",
        "source": "EGRN",
        "confidence": None if ownership_missing else 0.9,
        "reason": ("Право собственности не подтверждено (403/нет владельца)" if ownership_missing else None),
        "user_message": (
            "Право собственности не подтверждено; закажите свежую выписку ЕГРН" if ownership_missing else None
        ),
    }

    # PostGIS spatial layers / restrictions
    sections["spatial"] = {
        "status": "verified" if spatial_layers_available else ("partial" if map_summary else "unavailable"),
        "source": "PostGIS",
        "reason": None if postgis_available else "Пространственные слои не получены",
    }

    # OSM infrastructure
    sections["infrastructure"] = {
        "status": "verified" if infra_rich.get("available") else ("partial" if infra else "unavailable"),
        "source": "OSM",
        "confidence": infra.get("sourceConfidence"),
        "reason": infra_rich.get("reason") if not infra_available else None,
    }

    # SoilGrids
    soil_block = (soil or {}).get("soil") or {}
    sections["soil"] = {
        "status": "verified" if soil_block else "unavailable",
        "source": "SoilGrids",
        "confidence": soil_block.get("sourceConfidence"),
        "reason": None if soil_block else "Данные по почве недоступны",
    }

    # torgi.gov.ru market comparables
    sections["market"] = {
        "status": (
            "verified" if (market_available and market_count) else ("partial" if market_available else "unavailable")
        ),
        "source": "torgi",
        "reason": None if market_available else "Рыночные сопоставимые объекты недоступны",
    }

    # Drop null keys so the section objects stay compact.
    for section in sections.values():
        for key in [k for k, v in section.items() if v is None]:
            del section[key]

    statuses = [s.get("status") for s in sections.values()]
    verified_n = statuses.count("verified")
    unavailable_n = statuses.count("unavailable")
    if verified_n >= 5 and unavailable_n == 0:
        overall_confidence = "high"
    elif unavailable_n >= 3 or verified_n <= 2:
        overall_confidence = "low"
    else:
        overall_confidence = "medium"

    missing_critical: list[str] = []
    if ownership_missing:
        missing_critical.append("ownership")
    if nspd_unavailable:
        missing_critical.append("nspd")

    return {
        "overall_confidence": overall_confidence,
        "sections": sections,
        "global_warnings": [str(w) for w in warnings],
        "missing_critical": missing_critical,
        # Legacy flags kept so existing LLM prompts (Legal/CriticalRisk/Chief) and
        # the frontend that already read these continue to work unchanged.
        "dataset_available": dataset_available,
        "spatial_layers_available": spatial_layers_available,
        "nspd_unavailable": nspd_unavailable,
        "warnings": warnings,
        "instruction": (
            "Если nspd_unavailable=true, объясни, что публичные данные NSPD/карты временно недоступны. "
            "Не трактуй недоступность источника как подтвержденный юридический дефект или стоп-фактор ЕГРН."
        ),
    }


def build_report_context(ctx: AgentContext) -> dict[str, Any]:
    nspd, soil, infrastructure, market = _dataset_parts(ctx)
    map_summary = ctx.get("map_summary") or (ctx.get("GeoAgent") or {}).get("map_summary") or {}
    cadastral_area_ha = _area_ha_from_sqm(ctx.plot.area or nspd.get("area"))

    return {
        "profile": asdict(ctx.profile),
        "data_quality": _data_quality(
            ctx, nspd=nspd, soil=soil, infrastructure=infrastructure, market=market
        ),
        "plot": asdict(ctx.plot),
        "nspd": {
            "cadastral_number": nspd.get("cadastral_number") or ctx.plot.cadastral_number,
            "address": nspd.get("address") or ctx.plot.address,
            "area_sqm": nspd.get("area") or ctx.plot.area,
            "area_ha": cadastral_area_ha,
            "category": nspd.get("category") or ctx.plot.category,
            "allowed_use": nspd.get("allowed_use") or ctx.plot.allowed_use,
            "owner_type": nspd.get("owner_type") or ctx.plot.owner_type,
            "lat": nspd.get("lat") or ctx.plot.lat,
            "lng": nspd.get("lng") or ctx.plot.lng,
            "cadastral_price": nspd.get("price") or ctx.plot.price,
            "status": nspd.get("status") or (ctx.plot.egrn_data or {}).get("nspd_status"),
        },
        "area_summary": {
            "cadastral_area_ha": cadastral_area_ha,
            "geometry_area_ha": map_summary.get("parcel_area_ha"),
            "restricted_area_ha": map_summary.get("restricted_area_ha"),
            "usable_area_ha": map_summary.get("usable_area_ha"),
            "loss_percent": map_summary.get("loss_percent"),
            "note": "Use cadastral_area_ha as official area and usable_area_ha as area available after counted map restrictions.",
        },
        "map_summary": map_summary,
        "soil_summary": _soil_summary(soil) if soil else {},
        # Prefer the InfrastructureAgent's rich ctx summary; fall back to raw OSM.
        "infrastructure_summary": (
            _infrastructure_summary(ctx.get("infrastructure_summary") or {}, infrastructure)
            if (ctx.get("infrastructure_summary") or infrastructure)
            else {}
        ),
        # Prefer the MarketAgent's real analysis (market-service: cadastral anchor +
        # live torgi.gov.ru comparables). Fall back to the dataset's market_json note.
        "market_summary": (ctx.get("market_summary") or {
            "source": market.get("source"),
            "success": market.get("success"),
            "items_count": market.get("itemsCount") or market.get("items_count"),
            "limitations": market.get("limitations") or [],
        }) if (ctx.get("market_summary") or market) else {},
        "agent_outputs": {
            "legal": ctx.get("LegalAgent"),
            "land_use": ctx.get("LandUseAgent"),
            "restrictions": ctx.get("RestrictionsAgent"),
            "critical_risk": ctx.get("CriticalRiskAgent"),
            "scenario_ranking": ctx.get("ScenarioRankingAgent"),
            "chief_decision": ctx.get("ChiefDecisionAgent"),
        },
    }


def report_context_json(ctx: AgentContext) -> str:
    return json.dumps(build_report_context(ctx), ensure_ascii=False, default=str)
