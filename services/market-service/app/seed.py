"""Deterministic synthetic land-listing generator.

Produces a few thousand realistic comparable land listings across several
Russian regions and land categories so the market-service returns real
analytics fully offline. A fixed RNG seed makes the dataset reproducible.
"""

from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass

SEED = 20240601

# Reference point for the time spread of `listed_at`. Deterministic so the
# dataset does not drift between runs.
REFERENCE_DATE = dt.date(2026, 6, 1)
LISTING_WINDOW_DAYS = 540  # ~18 months of history

# Regions with an approximate geographic centre and a relative price level.
# price_level multiplies the per-category base price-per-sqm.
REGIONS: dict[str, dict] = {
    "Московская область": {"lat": 55.75, "lng": 37.62, "price_level": 1.0, "weight": 0.32},
    "Краснодарский край": {"lat": 45.04, "lng": 38.98, "price_level": 0.55, "weight": 0.22},
    "Республика Татарстан": {"lat": 55.80, "lng": 49.11, "price_level": 0.45, "weight": 0.16},
    "Ленинградская область": {"lat": 59.94, "lng": 30.31, "price_level": 0.70, "weight": 0.18},
    "Свердловская область": {"lat": 56.84, "lng": 60.65, "price_level": 0.38, "weight": 0.12},
}

# Category -> base price per sqm (RUB) and a typical area band (sqm).
# Areas: agricultural plots are large (hectares), settlement plots small,
# industrial plots mid-to-large.
CATEGORIES: dict[str, dict] = {
    "земли сельскохозяйственного назначения": {
        "base_ppsqm": 35.0,
        "area_min": 20_000.0,
        "area_max": 800_000.0,
        "weight": 0.40,
        "allowed_use": [
            "для сельскохозяйственного производства",
            "для ведения крестьянского (фермерского) хозяйства",
            "для садоводства",
        ],
    },
    "земли населённых пунктов": {
        "base_ppsqm": 2800.0,
        "area_min": 600.0,
        "area_max": 50_000.0,
        "weight": 0.42,
        "allowed_use": [
            "для индивидуального жилищного строительства",
            "для ведения личного подсобного хозяйства",
            "для размещения объектов торговли",
        ],
    },
    "земли промышленности": {
        "base_ppsqm": 950.0,
        "area_min": 5_000.0,
        "area_max": 300_000.0,
        "weight": 0.18,
        "allowed_use": [
            "для размещения промышленных объектов",
            "для размещения складских объектов",
            "для размещения объектов энергетики",
        ],
    },
}

SOURCES = ["avito", "domclick", "cian", "farpost", "realtymag"]


@dataclass
class Listing:
    id: str
    region: str
    category: str
    allowed_use: str
    area: float
    price: float
    price_per_sqm: float
    lat: float
    lng: float
    source: str
    listed_at: dt.datetime


def _weighted_choice(rng: random.Random, items: dict[str, dict]) -> str:
    keys = list(items.keys())
    weights = [items[k].get("weight", 1.0) for k in keys]
    return rng.choices(keys, weights=weights, k=1)[0]


def _log_uniform(rng: random.Random, low: float, high: float) -> float:
    import math

    return math.exp(rng.uniform(math.log(low), math.log(high)))


def generate_listings(count: int) -> list[Listing]:
    """Generate `count` deterministic synthetic listings."""

    rng = random.Random(SEED)
    listings: list[Listing] = []

    for i in range(count):
        region = _weighted_choice(rng, REGIONS)
        category = _weighted_choice(rng, CATEGORIES)
        region_cfg = REGIONS[region]
        cat_cfg = CATEGORIES[category]

        allowed_use = rng.choice(cat_cfg["allowed_use"])

        # Area: log-uniform within the category band so small plots dominate
        # but large ones still occur.
        area = round(_log_uniform(rng, cat_cfg["area_min"], cat_cfg["area_max"]), 1)

        # Price per sqm: base * region level * lognormal noise (~+-35%).
        base = cat_cfg["base_ppsqm"] * region_cfg["price_level"]
        noise = rng.lognormvariate(0.0, 0.30)
        # Mild discount for very large plots (economy of scale).
        scale_factor = 1.0
        if area > 100_000:
            scale_factor = 0.80
        elif area > 20_000:
            scale_factor = 0.90
        price_per_sqm = round(base * noise * scale_factor, 2)
        price = round(price_per_sqm * area, 2)

        # Location jitter around the regional centre (~+-0.6 degrees).
        lat = round(region_cfg["lat"] + rng.uniform(-0.6, 0.6), 6)
        lng = round(region_cfg["lng"] + rng.uniform(-0.6, 0.6), 6)

        source = rng.choice(SOURCES)

        # listed_at: more listings are recent (triangular toward 0 days ago).
        days_ago = int(rng.triangular(0, LISTING_WINDOW_DAYS, 0))
        seconds = rng.randint(0, 86_399)
        listed_at = dt.datetime.combine(
            REFERENCE_DATE - dt.timedelta(days=days_ago),
            dt.time(),
        ) + dt.timedelta(seconds=seconds)

        listings.append(
            Listing(
                id=f"L{i:06d}",
                region=region,
                category=category,
                allowed_use=allowed_use,
                area=area,
                price=price,
                price_per_sqm=price_per_sqm,
                lat=lat,
                lng=lng,
                source=source,
                listed_at=listed_at,
            )
        )

    return listings


def listings_as_rows(listings: list[Listing]) -> list[list]:
    """Convert listings to ClickHouse insert rows (column order matches schema)."""

    return [
        [
            l.id,
            l.region,
            l.category,
            l.allowed_use,
            l.area,
            l.price,
            l.price_per_sqm,
            l.lat,
            l.lng,
            l.source,
            l.listed_at,
        ]
        for l in listings
    ]


SEED_COLUMNS = [
    "id",
    "region",
    "category",
    "allowed_use",
    "area",
    "price",
    "price_per_sqm",
    "lat",
    "lng",
    "source",
    "listed_at",
]
