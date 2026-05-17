from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx


OVERPASS_QUERIES = {
    "road": 'way(around:{radius},{lat},{lon})["highway"]["highway"!~"footway|path|cycleway|steps"];',
    "settlement": 'node(around:{radius},{lat},{lon})["place"~"city|town|village|hamlet"];',
    "power": 'nwr(around:{radius},{lat},{lon})["power"];',
    "railway": 'way(around:{radius},{lat},{lon})["railway"];',
    "poi": 'nwr(around:{radius},{lat},{lon})["amenity"];',
}


class OverpassInfrastructureClient:
    def __init__(
        self,
        endpoints: tuple[str, ...] = (
            "https://overpass.kumi.systems/api/interpreter",
            "https://overpass-api.de/api/interpreter",
            "https://overpass.osm.ch/api/interpreter",
        ),
        timeout: float = 12.0,
    ) -> None:
        self.endpoints = endpoints
        self.timeout = timeout

    async def collect(self, *, lat: float, lon: float, radius_m: int = 5000) -> dict[str, Any]:
        started = time.monotonic()
        samples: dict[str, list[dict[str, Any]]] = {}
        counts: dict[str, int] = {}
        diagnostics = []

        results = await asyncio.gather(
            *(
                self._query_category(category=category, query_template=query_template, lat=lat, lon=lon, radius_m=radius_m)
                for category, query_template in OVERPASS_QUERIES.items()
            )
        )

        for category, result in results:
            diagnostics.append({"category": category, **result["diagnostics"]})
            elements = result.get("elements") or []
            counts[category] = len(elements)
            samples[category] = [_element_sample(element) for element in elements[:5]]

        return {
            "success": True,
            "source": "OpenStreetMap Overpass",
            "elapsedMs": int((time.monotonic() - started) * 1000),
            "sourceConfidence": 0.58,
            "radiusMeters": radius_m,
            "centerWgs84": {"lat": lat, "lon": lon},
            "counts": counts,
            "samples": samples,
            "signals": {
                "hasRoadsNearby": counts.get("road", 0) > 0,
                "hasSettlementsNearby": counts.get("settlement", 0) > 0,
                "hasPowerObjectsNearby": counts.get("power", 0) > 0,
                "hasRailwayNearby": counts.get("railway", 0) > 0,
                "hasPoiNearby": counts.get("poi", 0) > 0,
            },
            "diagnostics": diagnostics,
            "limitations": [
                "osm_data_can_be_incomplete",
                "does_not_confirm_legal_access_or_utility_connection",
                "overpass_endpoints_can_timeout_or_rate_limit",
            ],
        }

    async def _query_category(
        self,
        *,
        category: str,
        query_template: str,
        lat: float,
        lon: float,
        radius_m: int,
    ) -> tuple[str, dict[str, Any]]:
        query = _build_query(query_template.format(radius=radius_m, lat=lat, lon=lon))
        result = await self._query_first_available(query)
        return category, result

    async def _query_first_available(self, query: str) -> dict[str, Any]:
        last_error = ""
        for endpoint in self.endpoints:
            try:
                async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                    response = await client.post(endpoint, data={"data": query})
                    response.raise_for_status()
                    payload = response.json()
                    return {
                        "elements": payload.get("elements", []),
                        "diagnostics": {"ok": True, "url": endpoint, "status": response.status_code},
                    }
            except Exception as exc:
                last_error = str(exc)
        return {"elements": [], "diagnostics": {"ok": False, "error": last_error}}


def _build_query(body: str) -> str:
    return f"""
[out:json][timeout:10];
(
  {body}
);
out center tags 20;
"""


def _element_sample(element: dict[str, Any]) -> dict[str, Any]:
    tags = element.get("tags") or {}
    center = element.get("center") or {}
    return {
        "id": element.get("id"),
        "type": element.get("type"),
        "name": tags.get("name") or tags.get("ref") or "",
        "lat": element.get("lat") or center.get("lat"),
        "lon": element.get("lon") or center.get("lon"),
        "tags": {key: tags[key] for key in list(tags)[:8]},
    }
