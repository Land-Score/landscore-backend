from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_COLLECTOR_APP = ROOT / "services" / "data-collector"
sys.path.insert(0, str(DATA_COLLECTOR_APP))

from app.sources.nspd_map_layers import classify_layer_key, parcel_geometry_from_plot_raw, selected_layer_keys


def test_layer_classification() -> None:
    requested = {
        "cultural_heritage_zouit",
        "energy_transport_zouit",
        "water_protection_zone",
        "coastal_protective_strip",
        "natural_area_zouit",
        "water_erosion",
        "linear_erosion",
        "flooding",
        "disturbed_land",
        "heritage_object_territory",
    }

    assert classify_layer_key({"properties": {"name": "ЗОУИТ объекта культурного наследия"}}, 36940, requested) == "cultural_heritage_zouit"
    assert classify_layer_key({"properties": {"name": "Охранная зона линии электропередач"}}, 36940, requested) == "energy_transport_zouit"
    assert classify_layer_key({"properties": {"name_by_doc": "Водоохранная зона балки Свистунова"}}, 36940, requested) == "water_protection_zone"
    assert classify_layer_key({"properties": {"name_by_doc": "Прибрежная защитная полоса балки Свистунова", "content": "движение транспортных средств запрещается"}}, 36940, requested) == "coastal_protective_strip"
    assert classify_layer_key({"properties": {"name": "Линейная эрозия"}}, 38967, requested) == "linear_erosion"
    assert classify_layer_key({"properties": {"name": "Водная эрозия"}}, 38967, requested) == "water_erosion"
    assert classify_layer_key({"properties": {"name": "Зона затопления"}}, 38967, requested) == "flooding"
    assert classify_layer_key({"properties": {"name": "Территория объекта культурного наследия"}}, 472820, requested) == "heritage_object_territory"


def test_layer_selection_flags() -> None:
    selected = selected_layer_keys(
        [],
        include_restrictions=True,
        include_land_use=False,
        include_real_estate_objects=False,
        include_informational_layers=False,
    )
    assert "water_boundary_polygon" in selected
    assert "water_protection_zone" in selected
    assert "coastal_protective_strip" in selected
    assert "buildings" not in selected
    assert "special_economic_zone" not in selected


def test_plot_geometry_extraction() -> None:
    geometry = {"type": "Polygon", "coordinates": [[[1, 2], [3, 2], [1, 2]]]}
    payload = {"data": {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": geometry, "properties": {}}]}}
    assert parcel_geometry_from_plot_raw(payload) == geometry


if __name__ == "__main__":
    test_layer_classification()
    test_layer_selection_flags()
    test_plot_geometry_extraction()
    print("NSPD map layers smoke test passed")
