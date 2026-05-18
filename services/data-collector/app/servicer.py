from __future__ import annotations

import json
import os
import sys
from typing import Any

import grpc
import httpx

from app.config import settings
from app.dataset_pipeline import DataCollectionPipeline, dataset_response_dict
from app.rosreestr_client import egrn_to_dict, get_client, plot_to_dict
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


class DataCollectorServicer:
    """Implements data_collector.proto DataCollectorService business logic."""

    def __init__(self) -> None:
        self.spatial_collector = SpatialLayerCollector()
        self.dataset_pipeline = DataCollectionPipeline()

    async def GetPlotByCadastral(self, request, context):
        plot = await get_client().get_plot(request.cadastral_number)
        return _message_or_dict("PlotDataResponse", _plot_response_dict(plot))

    async def GetPlotByAddress(self, request, context):
        _set_unimplemented(context, "Address lookup needs geocoder integration")
        return _message_or_dict("PlotDataResponse", {})

    async def GetEGRN(self, request, context):
        egrn = await get_client().get_egrn(request.cadastral_number)
        return _message_or_dict("EGRNResponse", _egrn_response_dict(egrn))

    async def SearchPlots(self, request, context):
        _set_unimplemented(context, "Search requires a persisted search index")
        return _message_or_dict("SearchPlotsResponse", {"plots": [], "total": 0})

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

        if not raw_features_by_layer:
            if not settings.nspd_map_layers_enabled:
                warnings.append("nspd_map_layers_disabled")
            elif settings.rosreestr_mode.lower() != "real":
                warnings.append("nspd_map_layers_live_collection_requires_rosreestr_mode_real")
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

        if settings.rosreestr_mode.lower() == "real" and getattr(request, "include_real_estate_objects", False):
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
            except Exception as exc:
                warnings.append(f"nspd_child_tabs_failed:{exc}")

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
