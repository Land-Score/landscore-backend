from __future__ import annotations

import json
import re
from typing import Any

from app.layers.catalog import get_catalog_entry
from app.layers.models import CollectedPlotSpatialData, CollectedSpatialLayer, LayerCatalogEntry


CADASTRAL_NUMBER_RE = re.compile(r"\b\d{1,2}:\d{1,2}:\d{1,10}:\d{1,10}(?:/\d+)?\b")


def _attrs_from_feature(feature: dict[str, Any]) -> dict[str, Any]:
    properties = feature.get("properties") or {}
    options = properties.get("options") or {}
    attrs: dict[str, Any] = {}
    if isinstance(options, dict):
        attrs.update(options)
    if isinstance(properties, dict):
        attrs.update(properties)
    return attrs


def _find_cadastral_number(attrs: dict[str, Any]) -> str:
    for key in ("cad_num", "cadastral_number", "cadastralNumber", "objectCn", "label", "descr", "externalKey"):
        value = attrs.get(key)
        if value:
            match = CADASTRAL_NUMBER_RE.search(str(value))
            if match:
                return match.group(0)
    match = CADASTRAL_NUMBER_RE.search(json.dumps(attrs, ensure_ascii=False))
    return match.group(0) if match else ""


def _layer_id(feature: dict[str, Any], entry: LayerCatalogEntry) -> str:
    attrs = _attrs_from_feature(feature)
    for key in ("id", "object_id", "objectId", "externalKey", "interactionId", "label", "descr"):
        value = feature.get(key) or attrs.get(key)
        if value not in (None, ""):
            return f"{entry.key}:{value}"
    cadastral_number = _find_cadastral_number(attrs)
    if cadastral_number:
        return f"{entry.key}:{cadastral_number}"
    return f"{entry.key}:unknown"


def normalize_feature(feature: dict[str, Any], source_layer_key: str, *, source: str = "nspd") -> CollectedSpatialLayer | None:
    """Normalize a raw external feature into the common Data Collector layer shape.

    This function is intentionally source-agnostic and does not fetch anything.
    Network/API clients should collect raw features and then pass them here.
    """

    entry = get_catalog_entry(source_layer_key)
    if entry is None:
        return None

    attrs = _attrs_from_feature(feature)
    geometry = feature.get("geometry")
    scenario_effects = {
        scenario: {
            "area_loss_mode": effect.area_loss_mode,
            "show_in_report": effect.show_in_report,
        }
        for scenario, effect in entry.scenario_effects.items()
    }
    properties = dict(attrs)
    properties.update(
        {
            "sourceLayerKey": entry.key,
            "sourceLayerName": entry.source_name,
            "sourceGroup": entry.source_group,
            "scenarioEffects": scenario_effects,
        }
    )
    cadastral_number = _find_cadastral_number(attrs)
    if cadastral_number:
        properties["cadastralNumber"] = cadastral_number

    return CollectedSpatialLayer(
        id=_layer_id(feature, entry),
        source_layer_key=entry.key,
        source_layer_name=entry.source_name,
        source_group=entry.source_group,
        payload_kind=entry.payload_kind,
        normalized_type=entry.normalized_type,
        label=str(attrs.get("name") or attrs.get("label") or entry.label),
        geometry=geometry if isinstance(geometry, dict) else None,
        properties=properties,
        restrictions=list(entry.default_restrictions),
        normative_basis=list(entry.normative_basis),
        scenario_effects=scenario_effects,
        source=source,
    )


def split_collected_layers(cadastral_number: str, layers: list[CollectedSpatialLayer]) -> CollectedPlotSpatialData:
    result = CollectedPlotSpatialData(cadastral_number=cadastral_number)
    for layer in layers:
        if layer.payload_kind == "restriction":
            result.restriction_layers.append(layer)
        elif layer.payload_kind == "land_use":
            result.land_use_layers.append(layer)
        elif layer.payload_kind == "real_estate_object":
            result.real_estate_objects.append(layer)
        elif layer.payload_kind == "valuation":
            result.valuation_layers.append(layer)
        else:
            result.informational_layers.append(layer)
    return result


def to_geo_restriction_layer(layer: CollectedSpatialLayer) -> dict[str, Any]:
    return {
        "id": layer.id,
        "layer_type": layer.normalized_type,
        "name": layer.label,
        "source": layer.source,
        "geometry_geojson": json.dumps(layer.geometry or {}, ensure_ascii=False),
        "restrictions": layer.restrictions,
        "normative_basis": layer.normative_basis,
        "properties_json": json.dumps(layer.properties, ensure_ascii=False),
    }


def to_geo_real_estate_object(layer: CollectedSpatialLayer) -> dict[str, Any]:
    return {
        "cadastral_number": str(layer.properties.get("cadastralNumber") or ""),
        "object_type": layer.normalized_type,
        "name": layer.label,
        "area_sqm": float(layer.properties.get("area") or layer.properties.get("specified_area") or 0),
        "geometry_geojson": json.dumps(layer.geometry or {}, ensure_ascii=False),
        "properties_json": json.dumps(layer.properties, ensure_ascii=False),
    }

