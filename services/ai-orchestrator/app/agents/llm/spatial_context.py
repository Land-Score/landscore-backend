from __future__ import annotations

import json
from typing import Any


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _short_text(value: Any, limit: int = 700) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit].rstrip() + "..."


def _properties(layer: dict[str, Any]) -> dict[str, Any]:
    props = _json_dict(layer.get("properties_json"))
    raw_props = layer.get("properties")
    if isinstance(raw_props, dict):
        props.update(raw_props)
    options = props.get("options")
    if isinstance(options, dict):
        props.update(options)
    return props


def _compact_layer(layer: dict[str, Any]) -> dict[str, Any]:
    props = _properties(layer)
    compact = {
        "id": layer.get("id"),
        "label": layer.get("label") or layer.get("name") or layer.get("source_layer_name"),
        "name": layer.get("name") or props.get("name_by_doc") or props.get("reg_numb_border"),
        "normalized_type": layer.get("normalized_type") or layer.get("layer_type"),
        "source_layer_name": layer.get("source_layer_name") or props.get("sourceLayerName"),
        "source_group": layer.get("source_group") or props.get("sourceGroup"),
        "severity": layer.get("severity"),
        "area_loss_mode": layer.get("area_loss_mode"),
        "restrictions": layer.get("restrictions") or [],
        "normative_basis": layer.get("normative_basis") or [],
        "type_zone": props.get("type_zone"),
        "category_name": props.get("categoryName"),
        "reg_number": props.get("reg_numb_border") or props.get("externalKey"),
        "document_name": props.get("legal_act_document_name"),
        "document_date": props.get("legal_act_document_date"),
    }
    if props.get("content_restrict_encumbrances"):
        compact["content_restrict_encumbrances"] = _short_text(
            props.get("content_restrict_encumbrances")
        )
    return {key: value for key, value in compact.items() if value not in (None, "", [], {})}


def compact_spatial_context(spatial_layers: dict[str, Any] | None, limit: int = 12) -> dict[str, Any]:
    spatial_layers = spatial_layers or {}
    restriction_layers = spatial_layers.get("restriction_layers") or []
    land_use_layers = spatial_layers.get("land_use_layers") or []
    real_estate_objects = spatial_layers.get("real_estate_objects") or []
    valuation_layers = spatial_layers.get("valuation_layers") or []
    informational_layers = spatial_layers.get("informational_layers") or []

    return {
        "cadastral_number": spatial_layers.get("cadastral_number"),
        "warnings": spatial_layers.get("warnings") or [],
        "parcel_geometry_present": bool(spatial_layers.get("parcel_geometry")),
        "counts": {
            "restriction_layers": len(restriction_layers),
            "land_use_layers": len(land_use_layers),
            "real_estate_objects": len(real_estate_objects),
            "valuation_layers": len(valuation_layers),
            "informational_layers": len(informational_layers),
        },
        "restriction_layers": [_compact_layer(layer) for layer in restriction_layers[:limit]],
        "land_use_layers": [_compact_layer(layer) for layer in land_use_layers[:limit]],
        "real_estate_objects": [_compact_layer(layer) for layer in real_estate_objects[:limit]],
        "informational_layers": [_compact_layer(layer) for layer in informational_layers[:limit]],
    }
