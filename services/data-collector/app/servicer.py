from __future__ import annotations

import json
import os
import sys
from typing import Any

import grpc
import httpx

from app.config import settings
from app.dataset_pipeline import DataCollectionPipeline, dataset_response_dict
from app.mock_data import (
    build_child_objects_and_composition as mock_build_children,
    build_raw_features_by_layer as mock_build_layers,
)
from app.mock_data import build_plot_payload as mock_build_plot_payload
from app.mock_data import stable_hash as mock_stable_hash
from app.rosreestr_client import PlotData, egrn_to_dict, get_client, plot_to_dict
from app.layers.normalizer import normalize_feature
from app.sources.egrn_official import EGRNOfficialClient
from app.sources.nspd_map_layers import NspdChildObjectClient, NspdMapLayerClient, parcel_geometry_from_plot_raw
from app.spatial_collector import SpatialLayerCollector, collected_spatial_data_to_dict

PROTO_GEN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "proto_gen"))
if PROTO_GEN_DIR not in sys.path:
    sys.path.insert(0, PROTO_GEN_DIR)

try:
    import data_collector_pb2
except ImportError:  # pragma: no cover - generated stubs may not exist in local smoke tests yet.
    data_collector_pb2 = None


def _message_or_dict(message_name: str, data: dict[str, Any]):
    if data_collector_pb2 is None:
        return data
    return getattr(data_collector_pb2, message_name)(**data)


def _plot_response_dict(plot) -> dict[str, Any]:
    data = plot_to_dict(plot)
    data["raw_json"] = json.dumps(data.get("raw_json") or {}, ensure_ascii=False)
    return data


def _egrn_response_dict(egrn) -> dict[str, Any]:
    data = egrn_to_dict(egrn)
    data["raw_json"] = json.dumps(data.get("raw_json") or {}, ensure_ascii=False)
    return data


def _set_unimplemented(context: Any, message: str = "Not implemented yet") -> None:
    context.set_code(grpc.StatusCode.UNIMPLEMENTED)
    context.set_details(message)


def _format_encumbrance(item: Any) -> str:
    """Render a structured encumbrance as a human-readable string.

    EGRNResponse.encumbrances is ``repeated string`` in the proto, so structured
    objects are flattened here; the full structured form is kept in raw_json.
    """

    if isinstance(item, str):
        return item
    if not isinstance(item, dict):
        return str(item)
    parts = [str(item.get("type") or "").strip()]
    number = str(item.get("number") or "").strip()
    if number:
        parts.append(f"№ {number}")
    date = str(item.get("date") or "").strip()
    if date:
        parts.append(f"от {date}")
    text = " ".join(part for part in parts if part)
    return text or "Обременение"


def _first_registration_date(rights: list[Any], encumbrances: list[Any]) -> str:
    for item in rights:
        if isinstance(item, dict) and str(item.get("date") or "").strip():
            return str(item["date"]).strip()
    for item in encumbrances:
        if isinstance(item, dict) and str(item.get("date") or "").strip():
            return str(item["date"]).strip()
    return ""


def _polygon_coordinates_from_geometry(geometry: dict[str, Any] | None) -> list[Any]:
    if not isinstance(geometry, dict):
        return []
    geometry_type = geometry.get("type")
    if geometry_type == "Feature":
        return _polygon_coordinates_from_geometry(geometry.get("geometry"))
    if geometry_type == "Polygon" and isinstance(geometry.get("coordinates"), list):
        return [geometry["coordinates"]]
    if geometry_type == "MultiPolygon" and isinstance(geometry.get("coordinates"), list):
        return list(geometry["coordinates"])
    if geometry_type == "GeometryCollection" and isinstance(geometry.get("geometries"), list):
        polygons: list[Any] = []
        for item in geometry["geometries"]:
            polygons.extend(_polygon_coordinates_from_geometry(item))
        return polygons
    return []


def _parcel_geometry_from_land_parts(land_parts: list[Any]) -> dict[str, Any] | None:
    polygons: list[Any] = []
    for layer in land_parts:
        polygons.extend(_polygon_coordinates_from_geometry(getattr(layer, "geometry", None)))
    if not polygons:
        return None
    return {"type": "MultiPolygon", "coordinates": polygons}


def _merge_spatial_data(target: Any, source: Any) -> None:
    target.restriction_layers.extend(source.restriction_layers)
    target.land_use_layers.extend(source.land_use_layers)
    target.real_estate_objects.extend(source.real_estate_objects)
    target.valuation_layers.extend(source.valuation_layers)
    target.informational_layers.extend(source.informational_layers)
    target.raw_layers.extend(source.raw_layers)
    target.warnings.extend(source.warnings)


def _attach_mock_children(data: Any, cadastral_number: str, source: str = "mock") -> None:
    """Populate child_real_estate_objects / land_parts / land_composition (mock mode).

    Mirrors what the real NspdChildObjectClient adds on the live path, so the
    SpatialLayersResponse shape is identical between mock and real.
    """

    child_features, land_part_features, land_composition = mock_build_children(cadastral_number)
    for feature in child_features:
        layer = normalize_feature(feature, "buildings", source=source)
        if layer is not None:
            data.child_real_estate_objects.append(layer)
    for feature in land_part_features:
        # Land parts carry parcel geometry; normalize as a land-use "unknown" so
        # the layer keeps geometry + properties for downstream area logic.
        layer = normalize_feature(feature, "land_use_unknown", source=source)
        if layer is not None:
            layer.label = str((feature.get("properties") or {}).get("name") or "Часть земельного участка")
            data.land_parts.append(layer)
    if land_composition:
        data.land_composition.extend(land_composition)


def _normalize_raw_features_by_layer(raw: Any) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(raw, dict):
        raise ValueError("raw_features_by_layer_json must be a JSON object")

    normalized: dict[str, list[dict[str, Any]]] = {}
    for layer_key, value in raw.items():
        if value in (None, ""):
            continue
        if isinstance(value, list):
            features = [item for item in value if isinstance(item, dict)]
        elif isinstance(value, dict):
            features = [value]
        else:
            raise ValueError("raw_features_by_layer_json values must be feature objects or arrays")
        if features:
            normalized[str(layer_key)] = features
    return normalized


def _spatial_include_flags(request: Any) -> dict[str, bool]:
    flags = {
        "include_restrictions": bool(getattr(request, "include_restrictions", False)),
        "include_land_use": bool(getattr(request, "include_land_use", False)),
        "include_real_estate_objects": bool(getattr(request, "include_real_estate_objects", False)),
        "include_informational_layers": bool(getattr(request, "include_informational_layers", False)),
    }
    if not any(flags.values()):
        return {key: True for key in flags}
    return flags


# Region name -> cadastral region block, for deterministic mock search/lookup.
_REGION_NAME_TO_BLOCK = {
    "московск": "50",
    "москва": "77",
    "ставропол": "26",
    "краснодар": "23",
    "ростов": "61",
    "ленинградск": "47",
}


def _region_block_from_text(text: str) -> str:
    lowered = (text or "").lower()
    for needle, block in _REGION_NAME_TO_BLOCK.items():
        if needle in lowered:
            return block
    return "50"


def _plot_from_cadastral(cadastral_number: str) -> PlotData:
    payload = mock_build_plot_payload(cadastral_number)
    return PlotData(
        cadastral_number=payload["cadastral_number"],
        address=payload["address"],
        area=payload["area"],
        category=payload["category"],
        allowed_use=payload["allowed_use"],
        owner_type=payload["owner_type"],
        lat=payload["lat"],
        lng=payload["lng"],
        price=payload["price"],
        status=payload["status"],
        raw_json=payload["raw_json"],
    )


def _synthesize_cadastral(block: str, seed: int) -> str:
    district = 1 + (seed // 17 % 60)
    quarter = 1000001 + (seed % 9_000_000)
    parcel = 1 + (seed % 900)
    return f"{block}:{district:02d}:{quarter:07d}:{parcel}"


def _mock_plot_for_address(address: str) -> PlotData:
    """Deterministically synthesize a plot from a free-text address."""

    block = _region_block_from_text(address)
    seed = mock_stable_hash("addr:" + (address or "").strip())
    cadastral_number = _synthesize_cadastral(block, seed)
    plot = _plot_from_cadastral(cadastral_number)
    # Keep the user-supplied address visible while retaining synthetic geo.
    if address and address.strip():
        plot.address = address.strip()
        plot.raw_json = {**plot.raw_json, "query_address": address.strip(), "source": "mock"}
    return plot


def _mock_search_plots(request: Any) -> list[dict[str, Any]]:
    """Generate a deterministic list of candidate plots matching the filters."""

    limit = int(getattr(request, "limit", 0) or 0)
    if limit <= 0:
        limit = 8
    limit = min(limit, 50)

    region = str(getattr(request, "region", "") or "")
    category = str(getattr(request, "category", "") or "")
    allowed_use = str(getattr(request, "allowed_use", "") or "")
    area_min = float(getattr(request, "area_min", 0) or 0)
    area_max = float(getattr(request, "area_max", 0) or 0)
    price_min = float(getattr(request, "price_min", 0) or 0)
    price_max = float(getattr(request, "price_max", 0) or 0)

    block = _region_block_from_text(region)
    filter_seed = mock_stable_hash(
        f"search:{region}|{category}|{allowed_use}|{area_min}|{area_max}|{price_min}|{price_max}"
    )

    plots: list[dict[str, Any]] = []
    attempts = 0
    index = 0
    # Walk a deterministic sequence of candidate cadastral numbers and keep the
    # ones matching the filters, biasing risk profiles to be varied via index.
    while len(plots) < limit and attempts < limit * 40:
        attempts += 1
        candidate_seed = mock_stable_hash(f"{filter_seed}:{index}")
        index += 1
        cadastral_number = _synthesize_cadastral(block, candidate_seed)
        plot = _plot_from_cadastral(cadastral_number)

        if category and category.strip() and category.strip().lower() not in plot.category.lower():
            continue
        if allowed_use and allowed_use.strip() and allowed_use.strip().lower() not in plot.allowed_use.lower():
            continue
        area_ha = plot.area / 10_000.0
        if area_min and area_ha < area_min:
            continue
        if area_max and area_ha > area_max:
            continue
        if price_min and plot.price < price_min:
            continue
        if price_max and plot.price > price_max:
            continue

        plots.append(_plot_response_dict(plot))

    # Guarantee a non-empty, useful shortlist even when filters are very tight:
    # fall back to filter-relaxed candidates so the pipeline always has inputs.
    if not plots:
        for fallback_index in range(limit):
            candidate_seed = mock_stable_hash(f"{filter_seed}:fallback:{fallback_index}")
            cadastral_number = _synthesize_cadastral(block, candidate_seed)
            plots.append(_plot_response_dict(_plot_from_cadastral(cadastral_number)))

    return plots


class DataCollectorServicer:
    """Implements data_collector.proto DataCollectorService business logic."""

    def __init__(self) -> None:
        self.spatial_collector = SpatialLayerCollector()
        self.dataset_pipeline = DataCollectionPipeline()

    async def GetPlotByCadastral(self, request, context):
        try:
            plot = await get_client().get_plot(request.cadastral_number)
        except LookupError as exc:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(str(exc))
            return _message_or_dict("PlotDataResponse", {})
        except httpx.HTTPError as exc:
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            context.set_details(f"NSPD public data request failed: {exc}")
            return _message_or_dict("PlotDataResponse", {})
        return _message_or_dict("PlotDataResponse", _plot_response_dict(plot))

    async def GetPlotByAddress(self, request, context):
        address = (request.address or "").strip()
        if settings.rosreestr_mode.lower() == "real":
            client = get_client()
            get_plot_by_address = getattr(client, "get_plot_by_address", None)
            if get_plot_by_address is None:
                # Fallback only if the active client cannot resolve addresses.
                _set_unimplemented(context, "Address lookup is not supported by the active client")
                return _message_or_dict("PlotDataResponse", {})
            try:
                plot = await get_plot_by_address(address)
            except LookupError as exc:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(str(exc))
                return _message_or_dict("PlotDataResponse", {})
            except httpx.HTTPError as exc:
                context.set_code(grpc.StatusCode.UNAVAILABLE)
                context.set_details(f"NSPD public data request failed: {exc}")
                return _message_or_dict("PlotDataResponse", {})
            return _message_or_dict("PlotDataResponse", _plot_response_dict(plot))

        plot = _mock_plot_for_address(address)
        return _message_or_dict("PlotDataResponse", _plot_response_dict(plot))

    async def GetEGRN(self, request, context):
        try:
            egrn = await get_client().get_egrn(request.cadastral_number)
        except LookupError as exc:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(str(exc))
            return _message_or_dict("EGRNResponse", {})
        except httpx.HTTPError as exc:
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            context.set_details(f"NSPD public data request failed: {exc}")
            return _message_or_dict("EGRNResponse", {})

        response = _egrn_response_dict(egrn)

        # In real mode, enrich the honest "no official extract" stub with a paid
        # ЕГРН Level-1 source when one is configured (EGRN_MODE != off).
        if settings.rosreestr_mode.lower() == "real" and settings.egrn_mode.lower() not in ("off", ""):
            response = await self._enrich_egrn_from_official(request.cadastral_number, response)

        return _message_or_dict("EGRNResponse", response)

    async def _enrich_egrn_from_official(self, cadastral_number: str, response: dict[str, Any]) -> dict[str, Any]:
        try:
            official = await EGRNOfficialClient().collect(cadastral_number=cadastral_number)
        except Exception as exc:  # graceful: never break the response on a source error
            raw = json.loads(response.get("raw_json") or "{}")
            raw["egrn_official_error"] = str(exc)
            response["raw_json"] = json.dumps(raw, ensure_ascii=False)
            return response

        encumbrances = official.get("encumbrances") or []
        owner_name = official.get("owner_name")
        owner_type = official.get("owner_type") or ""
        rights = official.get("rights") or []

        if encumbrances:
            response["encumbrances"] = [_format_encumbrance(item) for item in encumbrances]
        # Owner name is only present for ЮЛ; otherwise fall back to the type.
        if owner_name:
            response["owner"] = str(owner_name)
        elif owner_type and not response.get("owner"):
            response["owner"] = owner_type
        registration_date = _first_registration_date(rights, encumbrances)
        if registration_date and not response.get("registration_date"):
            response["registration_date"] = registration_date

        raw = json.loads(response.get("raw_json") or "{}")
        raw["egrn_official"] = {
            "source": official.get("source"),
            "success": official.get("success"),
            "rights": rights,
            "encumbrances": encumbrances,
            "owner_type": owner_type,
            "owner_name": owner_name,
            "warnings": official.get("warnings"),
            "limitations": official.get("limitations"),
            "diagnostics": official.get("diagnostics"),
        }
        response["raw_json"] = json.dumps(raw, ensure_ascii=False)
        return response

    async def SearchPlots(self, request, context):
        if settings.rosreestr_mode.lower() == "real":
            _set_unimplemented(context, "Search requires a persisted search index")
            return _message_or_dict("SearchPlotsResponse", {"plots": [], "total": 0})

        plots = _mock_search_plots(request)
        return _message_or_dict(
            "SearchPlotsResponse",
            {"plots": plots, "total": len(plots)},
        )

    async def CollectPlotSpatialLayers(self, request, context):
        """Collect and normalize NSPD map layers intersecting a cadastral parcel."""

        warnings: list[str] = []
        raw_json = getattr(request, "raw_features_by_layer_json", "") or "{}"
        try:
            raw_features_by_layer = _normalize_raw_features_by_layer(json.loads(raw_json))
        except (json.JSONDecodeError, ValueError) as exc:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details(str(exc))
            return _message_or_dict("SpatialLayersResponse", {})
        raw_source = "nspd"

        parcel_geometry = None
        plot = None
        parcel_raw = getattr(request, "parcel_geometry_geojson", "") or ""
        if parcel_raw:
            try:
                parcel_geometry = json.loads(parcel_raw)
            except json.JSONDecodeError:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("parcel_geometry_geojson must be valid GeoJSON geometry")
                return _message_or_dict("SpatialLayersResponse", {})

        mock_mode = settings.rosreestr_mode.lower() != "real"

        if not raw_features_by_layer and mock_mode:
            # Synthesize a deterministic, realistic set of layers intersecting
            # the parcel so the AI pipeline has rich spatial inputs offline.
            raw_features_by_layer = mock_build_layers(request.cadastral_number)
            raw_source = "mock"
            if parcel_geometry is None:
                parcel_geometry = mock_build_plot_payload(request.cadastral_number)["_geometry"]
            warnings.append("spatial_layers_synthesized_in_mock_mode")

        if not raw_features_by_layer and not mock_mode:
            if not settings.nspd_map_layers_enabled:
                warnings.append("nspd_map_layers_disabled")
            else:
                if parcel_geometry is None:
                    try:
                        plot = await get_client().get_plot(request.cadastral_number)
                        parcel_geometry = parcel_geometry_from_plot_raw(plot.raw_json)
                    except Exception as exc:
                        warnings.append(f"nspd_plot_geometry_lookup_failed:{exc}")

                if parcel_geometry is None:
                    warnings.append("nspd_map_layers_skipped_missing_parcel_geometry")
                else:
                    flags = _spatial_include_flags(request)
                    source_layer_keys = list(getattr(request, "source_layer_keys", []))
                    raw_features_by_layer, layer_warnings = await NspdMapLayerClient().collect_raw_layers(
                        parcel_geometry=parcel_geometry,
                        source_layer_keys=source_layer_keys,
                        **flags,
                    )
                    raw_source = "nspd_map"
                    warnings.extend(layer_warnings)

        data = self.spatial_collector.collect_from_features(
            cadastral_number=request.cadastral_number,
            raw_features_by_layer=raw_features_by_layer,
            parcel_geometry=parcel_geometry,
            source=raw_source,
        )

        if mock_mode:
            _attach_mock_children(data, request.cadastral_number, source=raw_source)
            if data.parcel_geometry is None:
                data.parcel_geometry = mock_build_plot_payload(request.cadastral_number)["_geometry"]

        if settings.rosreestr_mode.lower() == "real":
            try:
                if plot is None:
                    plot = await get_client().get_plot(request.cadastral_number)
                child_objects, land_parts, land_composition, child_warnings = await NspdChildObjectClient().collect_for_plot(
                    cadastral_number=request.cadastral_number,
                    plot_raw_json=plot.raw_json,
                )
                data.child_real_estate_objects.extend(child_objects)
                data.land_parts.extend(land_parts)
                data.land_composition.extend(land_composition)
                warnings.extend(child_warnings)
                if data.parcel_geometry is None:
                    parts_geometry = _parcel_geometry_from_land_parts(data.land_parts)
                    if parts_geometry is not None:
                        data.parcel_geometry = parts_geometry
                        parcel_geometry = parts_geometry
                        warnings.append("parcel_geometry_built_from_land_parts")
            except Exception as exc:
                warnings.append(f"nspd_child_tabs_failed:{exc}")

        if (
            not raw_features_by_layer
            and raw_source != "nspd_map"
            and data.parcel_geometry is not None
            and settings.nspd_map_layers_enabled
            and settings.rosreestr_mode.lower() == "real"
        ):
            try:
                flags = _spatial_include_flags(request)
                source_layer_keys = list(getattr(request, "source_layer_keys", []))
                raw_features_by_layer, layer_warnings = await NspdMapLayerClient().collect_raw_layers(
                    parcel_geometry=data.parcel_geometry,
                    source_layer_keys=source_layer_keys,
                    **flags,
                )
                extra_data = self.spatial_collector.collect_from_features(
                    cadastral_number=request.cadastral_number,
                    raw_features_by_layer=raw_features_by_layer,
                    parcel_geometry=data.parcel_geometry,
                    source="nspd_map",
                )
                _merge_spatial_data(data, extra_data)
                warnings.extend(layer_warnings)
            except Exception as exc:
                warnings.append(f"nspd_map_layers_from_land_parts_failed:{exc}")

        data.warnings.extend(warnings)
        return _message_or_dict("SpatialLayersResponse", collected_spatial_data_to_dict(data))

    async def CollectPlotDataset(self, request, context):
        cadastral_number = request.cadastral_number.strip()
        if not cadastral_number:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("cadastral_number is required")
            return _message_or_dict("PlotDatasetResponse", {"success": False, "warnings": ["cadastral_number is required"]})
        try:
            dataset = await self.dataset_pipeline.collect_full_dataset(cadastral_number)
        except LookupError as exc:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(str(exc))
            return _message_or_dict("PlotDatasetResponse", {"success": False, "warnings": [str(exc)]})
        except httpx.HTTPError as exc:
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            context.set_details(f"NSPD public data request failed: {exc}")
            return _message_or_dict("PlotDatasetResponse", {"success": False, "warnings": [str(exc)]})
        except Exception as exc:
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(exc))
            return _message_or_dict("PlotDatasetResponse", {"success": False, "warnings": [str(exc)]})
        return _message_or_dict("PlotDatasetResponse", dataset_response_dict(dataset))
