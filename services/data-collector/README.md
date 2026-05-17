# Data Collector

`data-collector` is responsible for collecting and normalizing external cadastral
and map-source data. It should not calculate final usable hectares itself; it
sends normalized geometry layers to `geo-service`.

## Responsibilities

- cadastral plot lookup by number, address, or coordinates;
- reference EGRN/NSPD data collection where public data is legally available;
- ZOUIT and restriction layer collection;
- natural territories, cultural heritage, planning layers, red lines, negative
  processes, valuation thematic maps;
- agricultural land-use composition layers: arable land, pasture, hayfield,
  fallow, perennial plantings, and unknown/vision-inferred contours;
- child real estate objects on a land plot: buildings, structures, unfinished
  construction, single real estate complexes, and enterprise complexes;
- normalization into the common `SpatialLayer` contract for `geo-service`.

## Architecture Boundary

`data-collector` owns source-specific knowledge:

- source layer key/name/category;
- raw feature attributes;
- how a layer maps to a normalized type;
- scenario hints such as `ignore`, `warning_only`, or `exclude_from_usable`.

`geo-service` owns geometry:

- intersection with the parcel;
- union of loss zones without double-counting overlaps;
- final restricted/usable area in hectares;
- map-ready output geometry.

## Spatial Layer Catalog

The initial catalog is in `app/layers/catalog.py`. It covers the groups from the
NSPD map tree:

- capital construction objects;
- object complexes;
- ZOUIT;
- zoning and planning;
- natural territories;
- socio-economic zones;
- cultural heritage territories;
- other territories;
- negative processes;
- thematic cadastral value maps.

## Manual Soil Data Entry

For manual laboratory or field soil data:

```bash
python scripts/collect_soil_profile.py --output tmp/soil-profile.json
```

The generated JSON can later be passed to agents as a separate factual source.

## Legal Limitation

NSPD/public cadastral maps provide reference information, not an official EGRN
extract. Owners, legally significant encumbrances, and final legal conclusions
must be verified through an official Rosreestr/Gosuslugi channel or a contracted
API provider.
