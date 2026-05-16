from __future__ import annotations

import json
import os
import sys
from typing import Any

import grpc
import httpx

from app.dataset_pipeline import DataCollectionPipeline, dataset_response_dict
from app.rosreestr_client import egrn_to_dict, get_client, plot_to_dict
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
        """Normalize already collected raw features.

        gRPC request support for live external collection will be added after
        NSPD layer ids/endpoints are finalized. For now this method accepts
        optional `raw_features_by_layer_json` if present on a generated/request
        test double, which makes local tests deterministic and network-free.
        """

        raw_json = getattr(request, "raw_features_by_layer_json", "") or "{}"
        try:
            raw_features_by_layer = json.loads(raw_json)
        except json.JSONDecodeError:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("raw_features_by_layer_json must be valid JSON")
            return _message_or_dict("SpatialLayersResponse", {})

        parcel_geometry = None
        parcel_raw = getattr(request, "parcel_geometry_geojson", "") or ""
        if parcel_raw:
            try:
                parcel_geometry = json.loads(parcel_raw)
            except json.JSONDecodeError:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("parcel_geometry_geojson must be valid GeoJSON geometry")
                return _message_or_dict("SpatialLayersResponse", {})

        data = self.spatial_collector.collect_from_features(
            cadastral_number=request.cadastral_number,
            raw_features_by_layer=raw_features_by_layer,
            parcel_geometry=parcel_geometry,
        )
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
