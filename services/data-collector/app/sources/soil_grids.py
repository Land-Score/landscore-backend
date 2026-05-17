from __future__ import annotations

import time
from typing import Any

import httpx


SOIL_PROPERTIES = ("clay", "sand", "silt", "soc", "phh2o", "nitrogen", "cec")
SOIL_DEPTHS = ("0-5cm", "5-15cm", "15-30cm")


class SoilGridsClient:
    def __init__(self, base_url: str = "https://rest.isric.org/soilgrids/v2.0", timeout: float = 20.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def collect(self, *, lat: float, lon: float) -> dict[str, Any]:
        started = time.monotonic()
        params: list[tuple[str, str | float]] = [("lat", lat), ("lon", lon)]
        params.extend(("property", prop) for prop in SOIL_PROPERTIES)
        params.extend(("depth", depth) for depth in SOIL_DEPTHS)
        params.append(("value", "mean"))

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.get(f"{self.base_url}/properties/query", params=params)
            response.raise_for_status()
            payload = response.json()

        return {
            "success": True,
            "source": "SoilGrids",
            "elapsedMs": int((time.monotonic() - started) * 1000),
            "centerWgs84": {"lat": lat, "lon": lon},
            "soil": _summarize_soilgrids(payload),
            "raw": payload,
            "limitations": [
                "modelled_global_soil_data_not_lab_analysis",
                "not_official_agrochemical_survey",
            ],
        }


def _summarize_soilgrids(payload: dict[str, Any]) -> dict[str, Any]:
    layers = payload.get("properties", {}).get("layers", [])
    result: dict[str, Any] = {"sourceConfidence": 0.55, "spatialResolution": "global_model_250m"}
    values_by_prop: dict[str, dict[str, float]] = {}

    for layer in layers:
        prop_name = layer.get("name")
        if not prop_name:
            continue
        depths = layer.get("depths") or []
        values_by_prop[prop_name] = {}
        for depth in depths:
            label = depth.get("label")
            mean = (depth.get("values") or {}).get("mean")
            if label and mean is not None:
                values_by_prop[prop_name][label] = float(mean)

    result["topsoil"] = {
        prop: _property_summary(prop, values.get("0-5cm"))
        for prop, values in values_by_prop.items()
    }
    result["subsoil"] = {
        prop: _property_summary(prop, values.get("15-30cm"))
        for prop, values in values_by_prop.items()
    }
    result["textureClass"] = _texture_class(
        result["topsoil"].get("clay", {}).get("percent"),
        result["topsoil"].get("sand", {}).get("percent"),
        result["topsoil"].get("silt", {}).get("percent"),
    )
    return result


def _property_summary(prop: str, mean: float | None) -> dict[str, Any]:
    unit_by_prop = {
        "clay": "g/kg",
        "sand": "g/kg",
        "silt": "g/kg",
        "soc": "dg/kg",
        "phh2o": "pH*10",
        "nitrogen": "cg/kg",
        "cec": "mmol(c)/kg",
    }
    percent_divisor = {"clay": 10, "sand": 10, "silt": 10, "soc": 100}.get(prop)
    return {
        "mean": mean,
        "unit": unit_by_prop.get(prop, ""),
        "percent": round(mean / percent_divisor, 3) if mean is not None and percent_divisor else None,
    }


def _texture_class(clay: float | None, sand: float | None, silt: float | None) -> str:
    if clay is None or sand is None or silt is None:
        return "unknown"
    if clay >= 40:
        return "clay"
    if clay >= 27 and sand <= 45:
        return "clay_loam"
    if silt >= 50 and clay < 27:
        return "silt_loam"
    if sand >= 70 and clay < 15:
        return "sandy_loam"
    return "loam"

