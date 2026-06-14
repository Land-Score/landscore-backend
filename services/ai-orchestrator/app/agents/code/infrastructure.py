from __future__ import annotations

from app.pipeline.base import Agent, AgentResult
from app.pipeline.context import AgentContext
from app.clients.geo_client import GeoClient


_POI_TYPES = ["city", "road", "railway", "power_line", "gas_pipe", "water"]


def _to_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


class InfrastructureAgent(Agent):
    """Чисто вычислительный агент — без LLM.

    Анализирует расположение участка (доступ к дороге, инженерные сети,
    расстояния до объектов) через geo-service и формирует
    infrastructure_summary в ctx. Деградирует мягко.
    """

    name = "InfrastructureAgent"

    async def run(self, input: dict, ctx: AgentContext) -> AgentResult:
        plot = ctx.plot
        lat = _to_float(plot.lat)
        lng = _to_float(plot.lng)

        if not lat or not lng:
            summary = {
                "available": False,
                "reason": "координаты участка отсутствуют; анализ инфраструктуры пропущен",
            }
            ctx.set("infrastructure_summary", summary)
            return AgentResult(
                success=True,
                data={"infrastructure_available": False, "infrastructure_summary": summary},
            )

        client = GeoClient()
        warnings: list[str] = []
        location: dict = {}
        distances: dict = {}

        try:
            location = await client.analyze_plot_location(
                lat=lat, lng=lng, cadastral_number=plot.cadastral_number or ""
            )
        except Exception as exc:
            warnings.append(f"location_analysis_failed:{exc}")

        try:
            distances_resp = await client.get_distances(
                from_lat=lat, from_lng=lng, to_poi_types=_POI_TYPES
            )
            distances = distances_resp.get("distances_km", {}) or {}
        except Exception as exc:
            warnings.append(f"distances_failed:{exc}")

        if not location and not distances:
            summary = {
                "available": False,
                "reason": "geo-service недоступен; анализ инфраструктуры пропущен",
            }
            ctx.set("infrastructure_summary", summary)
            return AgentResult(
                success=True,
                data={
                    "infrastructure_available": False,
                    "infrastructure_summary": summary,
                    "warnings": warnings,
                },
            )

        utilities = {
            "electricity": bool(location.get("has_electricity")),
            "gas": bool(location.get("has_gas")),
            "water": bool(location.get("has_water")),
        }
        summary = {
            "available": True,
            "has_road_access": bool(location.get("has_road_access")),
            "distance_to_city_km": _to_float(location.get("distance_to_city_km")),
            "distance_to_road_km": _to_float(location.get("distance_to_road_km")),
            "distance_to_railway_km": _to_float(location.get("distance_to_railway_km")),
            "utilities": utilities,
            "protected_zones": list(location.get("protected_zones", []) or []),
            "accessibility_score": int(_to_float(location.get("accessibility_score"))),
            "region": location.get("region", ""),
            "district": location.get("district", ""),
            "locality": location.get("locality", ""),
            "distances_km": {k: _to_float(v) for k, v in distances.items()},
        }
        ctx.set("infrastructure_summary", summary)
        ctx.set("infrastructure_location", location)

        return AgentResult(
            success=True,
            data={
                "infrastructure_available": True,
                "infrastructure_summary": summary,
                "warnings": warnings,
            },
        )
