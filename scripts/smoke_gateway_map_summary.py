#!/usr/bin/env python3
from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api-gateway"))

fastapi_stub = types.ModuleType("fastapi")


class _APIRouter:
    def post(self, *args, **kwargs):
        def _decorator(func):
            return func

        return _decorator


class _HTTPException(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


fastapi_stub.APIRouter = _APIRouter
fastapi_stub.HTTPException = _HTTPException
fastapi_stub.Request = object
sys.modules.setdefault("fastapi", fastapi_stub)

config_stub = types.ModuleType("app.config")
config_stub.settings = types.SimpleNamespace(cadastral_lookup_timeout=1.0)
sys.modules.setdefault("app.config", config_stub)

errors_stub = types.ModuleType("app.errors")
errors_stub.raise_for_grpc = lambda exc: (_ for _ in ()).throw(exc)
sys.modules.setdefault("app.errors", errors_stub)

models_stub = types.ModuleType("app.models")
for name in (
    "CadastralLookupRequest",
    "CadastralLookupResponse",
    "CadastralMapAnalysisRequest",
    "CadastralMapAnalysisResponse",
    "CadastralSpatialLayersRequest",
    "CadastralSpatialLayersResponse",
):
    setattr(models_stub, name, type(name, (), {}))
sys.modules.setdefault("app.models", models_stub)

from app.routers.cadastral import _analysis_summary, _geo_response_to_dict, _geometry_to_wgs84


@dataclass
class Layer:
    id: str = "layer:1"
    layer_type: str = "water_protection_zone"
    label: str = "Водоохранная зона"
    group: str = "zouit"
    name: str = "26:11-6.81"
    severity: str = "hard_limit"
    area_loss_mode: str = "exclude_from_usable"
    show_in_report: bool = True
    intersection_ha: float = 29.2
    counted_in_loss_ha: float = 29.2
    overlap_not_double_counted_ha: float = 0.0
    restrictions: list[str] = field(default_factory=lambda: ["restriction"])
    normative_basis: list[str] = field(default_factory=lambda: ["basis"])
    source: str = "nspd_map"
    properties_json: str = '{"heavy": "raw"}'


@dataclass
class Response:
    cadastral_number: str = "26:11:101101:53"
    scenario: str = "agriculture"
    parcel_area_ha: float = 243.9909
    restricted_area_ha: float = 29.2339
    usable_area_ha: float = 214.757
    loss_percent: float = 11.98
    layers: list[Layer] = field(default_factory=lambda: [Layer()])
    land_use_composition: list = field(default_factory=list)
    child_real_estate_objects: list = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    result_json: str = '{"raw": true}'


def main() -> None:
    analysis = _geo_response_to_dict(Response(), include_raw=False)
    assert "raw" not in analysis
    assert "properties" not in analysis["layers"][0]
    assert analysis["top_restrictions"][0]["id"] == "layer:1"

    verbose = _geo_response_to_dict(Response(), include_raw=True)
    assert verbose["raw"] == {"raw": True}
    assert verbose["layers"][0]["properties"] == {"heavy": "raw"}

    missing = _analysis_summary({}, geometry_status="missing")
    assert missing["usable_area_ha"] is None
    assert missing["top_restrictions"] == []

    converted = _geometry_to_wgs84({"type": "Point", "coordinates": [4700190.84097855, 5592331.816929129]})
    lon, lat = converted["coordinates"]
    assert 42.0 < lon < 42.5
    assert 44.5 < lat < 45.0

    print("API gateway map summary smoke test passed")


if __name__ == "__main__":
    main()
