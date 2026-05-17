#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path
import json


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "data-collector"))
from app.spatial_collector import SpatialLayerCollector
from app.layers.normalizer import to_geo_land_use_layer, to_geo_real_estate_object, to_geo_restriction_layer

for module_name in list(sys.modules):
    if module_name == "app" or module_name.startswith("app."):
        del sys.modules[module_name]

sys.path.insert(0, str(ROOT / "services" / "geo-service"))
from app.restrictions.calculator import calculate_land_use_restrictions
from app.restrictions.models import AgriculturalLandUseLayer, RealEstateObject, RestrictionAnalysisRequest, RestrictionLayer


def _geojson_payload(payload: dict) -> dict:
    converted = dict(payload)
    if "geometry_geojson" in converted:
        converted["geometry"] = json.loads(converted.pop("geometry_geojson"))
    if "properties_json" in converted:
        converted["properties"] = json.loads(converted.pop("properties_json") or "{}")
    return converted


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
        ],
        "heritage_object_territory": [
            {
                "id": "heritage-a",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[750, 0], [1000, 0], [1000, 400], [750, 400], [750, 0]]],
                },
                "properties": {"label": "Территория ОКН"},
            }
        ],
        "gambling_zone": [
            {
                "id": "gambling-a",
                "geometry": parcel,
                "properties": {"label": "Игорная зона"},
            }
        ],
        "land_use_arable": [
            {
                "id": "arable-a",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [700, 0], [700, 1000], [0, 1000], [0, 0]]],
                },
                "properties": {"label": "Пашня"},
            }
        ],
        "land_use_pasture": [
            {
                "id": "pasture-a",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[700, 0], [1000, 0], [1000, 1000], [700, 1000], [700, 0]]],
                },
                "properties": {"label": "Пастбище"},
            }
        ],
        "buildings": [
            {
                "id": "building-a",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[100, 100], [120, 100], [120, 120], [100, 120], [100, 100]]],
                },
                "properties": {
                    "label": "Здание на участке",
                    "options": {"cad_num": "26:11:101101:5301", "specified_area": 400},
                },
            }
        ],
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
            RestrictionLayer(**_geojson_payload(to_geo_restriction_layer(layer)))
            for layer in collected.restriction_layers
        ],
        land_use_layers=[
            AgriculturalLandUseLayer(**_geojson_payload(to_geo_land_use_layer(layer)))
            for layer in collected.land_use_layers
        ],
        real_estate_objects=[
            RealEstateObject(**_geojson_payload(to_geo_real_estate_object(layer)))
            for layer in collected.real_estate_objects
        ],
    )
    result = calculate_land_use_restrictions(request)

    print(f"restriction_layers={len(collected.restriction_layers)}")
    print(f"land_use_layers={len(collected.land_use_layers)}")
    print(f"real_estate_objects={len(collected.real_estate_objects)}")
    print(f"parcel_area_ha={result.parcel_area_ha}")
    print(f"restricted_area_ha={result.restricted_area_ha}")
    print(f"usable_area_ha={result.usable_area_ha}")

    layer_by_id = {layer.id: layer for layer in result.layers}
    composition_by_type = {item.land_use_type: item for item in result.land_use_composition}
    map_layer_ids = {layer.id for layer in result.map_layers}

    assert len(collected.restriction_layers) == 4
    assert len(collected.land_use_layers) == 2
    assert len(collected.real_estate_objects) == 1
    assert result.parcel_area_ha == 100.0
    assert result.restricted_area_ha == 85.0
    assert result.usable_area_ha == 15.0

    assert layer_by_id["water_boundary_polygon:coastal-a"].counted_in_loss_ha == 50.0
    assert layer_by_id["water_boundary_polygon:coastal-b"].counted_in_loss_ha == 25.0
    assert layer_by_id["water_boundary_polygon:coastal-b"].overlap_not_double_counted_ha == 25.0
    assert layer_by_id["heritage_object_territory:heritage-a"].counted_in_loss_ha == 10.0
    assert layer_by_id["gambling_zone:gambling-a"].area_loss_mode == "ignore"
    assert layer_by_id["gambling_zone:gambling-a"].counted_in_loss_ha == 0.0
    assert layer_by_id["gambling_zone:gambling-a"].show_in_report is False
    assert "restriction_gambling_zone:gambling-a" not in map_layer_ids

    assert composition_by_type["arable"].area_ha == 70.0
    assert composition_by_type["pasture"].area_ha == 30.0
    assert result.child_real_estate_objects[0].cadastral_number == "26:11:101101:5301"
    assert result.child_real_estate_objects[0].inside_parcel is True


if __name__ == "__main__":
    main()
