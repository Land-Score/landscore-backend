from __future__ import annotations

import json
from typing import Any

from app.pipeline.base import Agent, AgentResult
from app.pipeline.context import AgentContext


def _to_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _json_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _collect_encumbrances(ctx: AgentContext, nspd: dict, egrn: dict) -> list[str]:
    found: list[str] = []
    for source in (nspd.get("encumbrances"), egrn.get("encumbrances")):
        if isinstance(source, list):
            found.extend(str(item) for item in source if item)
    extracted = ctx.get("DocumentExtractionAgent") or {}
    if isinstance(extracted, dict):
        doc_enc = extracted.get("encumbrances")
        if isinstance(doc_enc, list):
            found.extend(str(item) for item in doc_enc if item)
    # Дедупликация с сохранением порядка.
    seen: set[str] = set()
    result: list[str] = []
    for item in found:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


class FactNormalizationAgent(Agent):
    """Чисто вычислительный агент — без LLM.

    Сводит данные DataRequestAgent (nspd/spatial_layers), geo-анализа и
    паспорт участка в единый нормализованный словарь фактов. Результат
    потребляют LLM-агенты Legal / LandUse / Restrictions.
    """

    name = "FactNormalizationAgent"

    async def run(self, input: dict, ctx: AgentContext) -> AgentResult:
        plot = ctx.plot
        nspd = ctx.get("nspd") or {}
        data_request = ctx.get("DataRequestAgent") or {}
        spatial_layers = ctx.get("spatial_layers") or data_request.get("spatial_layers") or {}
        geo_analysis = ctx.get("geo_analysis") or {}
        egrn = _json_dict(plot.egrn_data) if isinstance(plot.egrn_data, dict) else {}

        cadastral_area = _to_float(plot.area)
        # Геометрическая и расчётная площади из geo-анализа (в гектарах).
        geometry_area_ha = _to_float(geo_analysis.get("parcel_area_ha"))
        restricted_area_ha = _to_float(geo_analysis.get("restricted_area_ha"))
        usable_area_ha = _to_float(geo_analysis.get("usable_area_ha"))
        loss_percent = _to_float(geo_analysis.get("loss_percent"))

        # Если geo-анализа нет — полезная площадь = кадастровая (в м²).
        cadastral_area_ha = cadastral_area / 10_000.0 if cadastral_area > 1000 else cadastral_area
        if not usable_area_ha and cadastral_area_ha:
            usable_area_ha = cadastral_area_ha

        encumbrances = _collect_encumbrances(ctx, nspd, egrn)
        restriction_layers = spatial_layers.get("restriction_layers", []) if isinstance(spatial_layers, dict) else []

        normalized = {
            "cadastral_number": plot.cadastral_number or nspd.get("cadastral_number", ""),
            "address": plot.address or nspd.get("address", ""),
            "coordinates": {"lat": _to_float(plot.lat), "lng": _to_float(plot.lng)},
            "areas": {
                "cadastral_area_sqm": cadastral_area,
                "cadastral_area_ha": round(cadastral_area_ha, 4) if cadastral_area_ha else 0.0,
                "geometry_area_ha": round(geometry_area_ha, 4) if geometry_area_ha else 0.0,
                "restricted_area_ha": round(restricted_area_ha, 4) if restricted_area_ha else 0.0,
                "usable_area_ha": round(usable_area_ha, 4) if usable_area_ha else 0.0,
                "loss_percent": round(loss_percent, 2) if loss_percent else 0.0,
            },
            "category": plot.category or nspd.get("category", ""),
            "allowed_use": plot.allowed_use or nspd.get("allowed_use", ""),
            "owner_type": plot.owner_type or nspd.get("owner_type", ""),
            "owner": egrn.get("owner") or nspd.get("owner", ""),
            "encumbrances": encumbrances,
            "encumbrances_count": len(encumbrances),
            "restriction_layers_count": len(restriction_layers) if isinstance(restriction_layers, list) else 0,
            "land_use_composition": geo_analysis.get("land_use_composition", []),
            "price": _to_float(plot.price),
            "sources": {
                "nspd_available": bool(nspd),
                "spatial_layers_available": bool(spatial_layers),
                "geo_analysis_available": bool(geo_analysis),
                "egrn_available": bool(egrn),
            },
        }

        ctx.set("normalized_facts", normalized)
        return AgentResult(success=True, data={"normalized_facts": normalized})
