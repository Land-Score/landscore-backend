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
    return round(area / 10_000, 4) if area > 1_000 else area


def _soil_summary(soil_payload: dict[str, Any]) -> dict[str, Any]:
    soil = soil_payload.get("soil") or {}
    topsoil = soil.get("topsoil") or {}
    subsoil = soil.get("subsoil") or {}
    return {
        "source": soil_payload.get("source"),
        "confidence": soil.get("sourceConfidence"),
        "spatial_resolution": soil.get("spatialResolution"),
        "texture_class": soil.get("textureClass"),
        "topsoil": {
            "clay_percent": (topsoil.get("clay") or {}).get("percent"),
            "sand_percent": (topsoil.get("sand") or {}).get("percent"),
            "silt_percent": (topsoil.get("silt") or {}).get("percent"),
            "soc_percent": (topsoil.get("soc") or {}).get("percent"),
            "ph": _ph((topsoil.get("phh2o") or {}).get("mean")),
        },
        "subsoil": {
            "clay_percent": (subsoil.get("clay") or {}).get("percent"),
            "sand_percent": (subsoil.get("sand") or {}).get("percent"),
            "silt_percent": (subsoil.get("silt") or {}).get("percent"),
            "soc_percent": (subsoil.get("soc") or {}).get("percent"),
            "ph": _ph((subsoil.get("phh2o") or {}).get("mean")),
        },
        "limitations": soil_payload.get("limitations") or [],
    }


def _ph(value: Any) -> float | None:
    ph = _round(value, 2)
    if ph is None:
        return None
    return round(ph / 10, 2) if ph > 14 else ph


def _infrastructure_summary(payload: dict[str, Any]) -> dict[str, Any]:
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


def _data_quality(ctx: AgentContext) -> dict[str, Any]:
    data_request = ctx.get("DataRequestAgent") or {}
    warnings = data_request.get("warnings") or []
    dataset_available = bool(data_request.get("dataset_available"))
    spatial_layers_available = bool(data_request.get("spatial_layers_available"))
    nspd_unavailable = any(
        "nspd" in str(item).lower() and ("failed" in str(item).lower() or "connection" in str(item).lower())
        for item in warnings
    )
    return {
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
        "data_quality": _data_quality(ctx),
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
        "infrastructure_summary": _infrastructure_summary(infrastructure) if infrastructure else {},
        "market_summary": {
            "source": market.get("source"),
            "success": market.get("success"),
            "items_count": market.get("itemsCount") or market.get("items_count"),
            "limitations": market.get("limitations") or [],
        } if market else {},
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
