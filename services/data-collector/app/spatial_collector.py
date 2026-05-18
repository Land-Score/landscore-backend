from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from app.layers.models import CollectedPlotSpatialData, CollectedSpatialLayer
from app.layers.normalizer import normalize_feature, split_collected_layers


class SpatialLayerCollector:
    """Collect and normalize spatial layers for a parcel.

    API-specific clients should fetch raw source features, grouped by our
    internal source layer key, and pass them here. This keeps all source-layer
    taxonomy inside data-collector while geo-service receives a stable contract.
    """

    def collect_from_features(
        self,
        *,
        cadastral_number: str,
        raw_features_by_layer: dict[str, list[dict[str, Any]]],
        parcel_geometry: dict[str, Any] | None = None,
        source: str = "nspd",
    ) -> CollectedPlotSpatialData:
        layers: list[CollectedSpatialLayer] = []
        warnings: list[str] = []
        raw_layers: list[dict[str, Any]] = []

        for layer_key, features in raw_features_by_layer.items():
            for feature in features:
                raw_layers.append({"layer_key": layer_key, "feature": feature})
                layer = normalize_feature(feature, layer_key, source=source)
                if layer is None:
                    warnings.append(f"Unknown source layer key: {layer_key}")
                    continue
                layers.append(layer)

        result = split_collected_layers(cadastral_number, layers)
        result.parcel_geometry = parcel_geometry
        result.raw_layers = raw_layers
        result.warnings = warnings
        return result


def spatial_layer_to_dict(layer: CollectedSpatialLayer) -> dict[str, Any]:
    return {
        "id": layer.id,
        "source_layer_key": layer.source_layer_key,
        "source_layer_name": layer.source_layer_name,
        "source_group": layer.source_group,
        "payload_kind": layer.payload_kind,
        "normalized_type": layer.normalized_type,
        "label": layer.label,
        "geometry_geojson": json.dumps(layer.geometry or {}, ensure_ascii=False),
        "source": layer.source,
        "confidence": layer.confidence,
        "restrictions": layer.restrictions,
        "normative_basis": layer.normative_basis,
        "properties_json": json.dumps(layer.properties, ensure_ascii=False),
    }


def collected_spatial_data_to_dict(data: CollectedPlotSpatialData) -> dict[str, Any]:
    return {
        "cadastral_number": data.cadastral_number,
        "parcel_geometry_geojson": json.dumps(data.parcel_geometry or {}, ensure_ascii=False),
        "restriction_layers": [spatial_layer_to_dict(layer) for layer in data.restriction_layers],
        "land_use_layers": [spatial_layer_to_dict(layer) for layer in data.land_use_layers],
        "real_estate_objects": [spatial_layer_to_dict(layer) for layer in data.real_estate_objects],
        "child_real_estate_objects": [spatial_layer_to_dict(layer) for layer in data.child_real_estate_objects],
        "land_parts": [spatial_layer_to_dict(layer) for layer in data.land_parts],
        "land_composition_json": json.dumps(data.land_composition, ensure_ascii=False),
        "valuation_layers": [spatial_layer_to_dict(layer) for layer in data.valuation_layers],
        "informational_layers": [spatial_layer_to_dict(layer) for layer in data.informational_layers],
        "warnings": data.warnings,
        "raw_json": json.dumps(asdict(data), ensure_ascii=False),
        "from_cache": False,
    }

