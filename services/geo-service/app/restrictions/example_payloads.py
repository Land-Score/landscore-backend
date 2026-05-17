from __future__ import annotations

from app.restrictions.models import (
    AgriculturalLandUseLayer,
    RealEstateObject,
    RestrictionAnalysisRequest,
    RestrictionLayer,
)


def square(min_x: float, min_y: float, max_x: float, max_y: float) -> dict:
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [min_x, min_y],
                [max_x, min_y],
                [max_x, max_y],
                [min_x, max_y],
                [min_x, min_y],
            ]
        ],
    }


def demo_request() -> RestrictionAnalysisRequest:
    """Synthetic 100 ha parcel with overlapping restrictions.

    Coordinates are metric. Parcel is 1000 x 1000 m = 100 ha.
    Coastal strip covers 20 ha. OКН zone overlaps 10 ha of that strip and
    adds 10 ha outside it. Total hard loss should be 30 ha, not 40 ha.
    """

    return RestrictionAnalysisRequest(
        cadastral_number="demo",
        scenario="agriculture",
        parcel_geometry=square(0, 0, 1000, 1000),
        restriction_layers=[
            RestrictionLayer(
                id="coastal",
                layer_type="coastal_protective_strip",
                name="Прибрежная защитная полоса",
                source="demo",
                geometry=square(0, 0, 1000, 200),
            ),
            RestrictionLayer(
                id="heritage",
                layer_type="heritage_site_territory",
                name="Территория ОКН",
                source="demo",
                geometry=square(500, 100, 1000, 500),
            ),
            RestrictionLayer(
                id="gambling",
                layer_type="gambling_zone",
                name="Игорная зона",
                source="demo",
                geometry=square(0, 0, 1000, 1000),
            ),
        ],
        land_use_layers=[
            AgriculturalLandUseLayer(
                id="arable",
                land_use_type="arable",
                label="Пашня",
                geometry=square(0, 0, 700, 1000),
                source="demo_catalog",
                confidence=0.95,
            ),
            AgriculturalLandUseLayer(
                id="pasture",
                land_use_type="pasture",
                label="Пастбище",
                geometry=square(700, 0, 1000, 1000),
                source="demo_catalog",
                confidence=0.9,
            ),
        ],
        real_estate_objects=[
            RealEstateObject(
                cadastral_number="demo:building:1",
                object_type="building",
                name="Здание на участке",
                area_sqm=120.0,
                geometry=square(100, 100, 120, 120),
            )
        ],
    )
