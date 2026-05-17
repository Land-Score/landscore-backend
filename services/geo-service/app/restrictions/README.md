# Land Use Restrictions Geometry Core

`geo-service` does not collect NSPD/Rosreestr/Yandex map layers and does not own the
source-layer catalog. It receives normalized layers from `data-collector` and
calculates spatial impact.

## Service Boundary

`data-collector`:

- knows external source layer names and ids;
- collects ZOUIT, cultural heritage, natural territories, negative processes,
  planning layers, valuation layers, agricultural land-use contours, and child
  real estate objects;
- normalizes them into `restriction`, `land_use`, `real_estate_object`,
  `valuation`, or `info` payloads;
- attaches scenario effects such as `exclude_from_usable`, `warning_only`, or
  `ignore`.

`geo-service`:

- intersects normalized layers with the parcel geometry;
- calculates hectares inside the parcel;
- unions all scenario-excluding geometries before subtracting area, so
  overlapping zones are counted once;
- returns map-ready output layers for the .NET/general map overlay.

## Area Loss Rule

```text
area(parcel ∩ union(restrictive_layers_for_scenario))
```

Example: if a water protection zone lies inside a coastal protective strip, the
overlap is counted once in `restricted_area_ha`.

## Vision Usage

Vision/LLM can help classify rendered map symbols or a legend, but final hectare
values should come from geometry. If vision creates inferred contours, it must
pass them as low-confidence `land_use` layers and they should be clearly marked
in the output.
