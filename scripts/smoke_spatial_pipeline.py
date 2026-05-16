#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "data-collector"))
from app.spatial_collector import SpatialLayerCollector

for module_name in list(sys.modules):
    if module_name == "app" or module_name.startswith("app."):
        del sys.modules[module_name]

sys.path.insert(0, str(ROOT / "services" / "geo-service"))
from app.restrictions.calculator import calculate_land_use_restrictions
from app.restrictions.models import RestrictionAnalysisRequest, RestrictionLayer


def main() -> None:
    parcel = {
        "type": "Polygon",
        "coordinates": [[[0, 0], [1000, 0], [1000, 1000], [0, 1000], [0, 0]]],
    }
    raw_features_by_layer = {
        "water_boundary_polygon": [
            {
                "id": "coastal-a",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [500, 0], [500, 1000], [0, 1000], [0, 0]]],
                },
                "properties": {"label": "Прибрежная защитная полоса A"},
            },
            {
                "id": "coastal-b",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[250, 0], [750, 0], [750, 1000], [250, 1000], [250, 0]]],
                },
                "properties": {"label": "Прибрежная защитная полоса B"},
            },
        ]
    }

    collected = SpatialLayerCollector().collect_from_features(
        cadastral_number="test",
        raw_features_by_layer=raw_features_by_layer,
        parcel_geometry=parcel,
    )
    request = RestrictionAnalysisRequest(
        cadastral_number="test",
        scenario="agriculture",
        parcel_geometry=parcel,
        restriction_layers=[
            RestrictionLayer(
                id=layer.id,
                layer_type=layer.normalized_type,
                name=layer.label,
                source=layer.source,
                geometry=layer.geometry,
                restrictions=layer.restrictions,
                normative_basis=layer.normative_basis,
                properties=layer.properties,
            )
            for layer in collected.restriction_layers
        ],
    )
    result = calculate_land_use_restrictions(request)

    print(f"restriction_layers={len(collected.restriction_layers)}")
    print(f"parcel_area_ha={result.parcel_area_ha}")
    print(f"restricted_area_ha={result.restricted_area_ha}")
    print(f"usable_area_ha={result.usable_area_ha}")

    assert len(collected.restriction_layers) == 2
    assert result.parcel_area_ha == 100.0
    assert result.restricted_area_ha == 75.0
    assert result.usable_area_ha == 25.0


if __name__ == "__main__":
    main()
