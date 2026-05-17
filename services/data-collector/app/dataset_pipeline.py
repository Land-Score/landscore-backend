from __future__ import annotations

import asyncio
import json
from typing import Any

from app.config import settings
from app.rosreestr_client import get_client, plot_to_dict
from app.sources.market_search import MarketSearchClient
from app.sources.overpass import OverpassInfrastructureClient
from app.sources.soil_grids import SoilGridsClient


class DataCollectionPipeline:
    """Backend home for the old standalone parser pipeline."""

    async def collect_full_dataset(self, cadastral_number: str) -> dict[str, Any]:
        plot = await get_client().get_plot(cadastral_number)
        plot_data = plot_to_dict(plot)

        if settings.rosreestr_mode.lower() != "real":
            return _mock_dataset(plot_data)

        soil_task = SoilGridsClient(timeout=settings.source_timeout).collect(lat=plot.lat, lon=plot.lng)
        infrastructure_task = OverpassInfrastructureClient(timeout=settings.source_timeout).collect(lat=plot.lat, lon=plot.lng)
        tasks = [soil_task, infrastructure_task]

        if settings.market_search_enabled:
            tasks.append(
                MarketSearchClient(timeout=settings.source_timeout).collect(
                    cadastral_number=plot.cadastral_number,
                    region=_region_from_plot(plot_data),
                    district=_district_from_plot(plot_data),
                )
            )

        results = await asyncio.gather(*tasks, return_exceptions=True)
        soil = results[0]
        infrastructure = results[1]
        market = results[2] if settings.market_search_enabled and len(results) > 2 else _market_disabled()

        return {
            "success": True,
            "source": "data_collector_dataset_pipeline",
            "cadastralNumber": cadastral_number,
            "nspd": plot_data,
            "soil": _result_or_error(soil),
            "infrastructure": _result_or_error(infrastructure),
            "marketLiquidity": _result_or_error(market),
            "warnings": _warnings(soil, infrastructure, market),
        }


def dataset_response_dict(dataset: dict[str, Any]) -> dict[str, Any]:
    return {
        "success": bool(dataset.get("success")),
        "cadastral_number": dataset.get("cadastralNumber") or dataset.get("nspd", {}).get("cadastral_number", ""),
        "nspd_json": json.dumps(dataset.get("nspd") or {}, ensure_ascii=False),
        "soil_json": json.dumps(dataset.get("soil") or {}, ensure_ascii=False),
        "infrastructure_json": json.dumps(dataset.get("infrastructure") or {}, ensure_ascii=False),
        "market_json": json.dumps(dataset.get("marketLiquidity") or {}, ensure_ascii=False),
        "warnings": dataset.get("warnings") or [],
        "raw_json": json.dumps(dataset, ensure_ascii=False),
        "source": dataset.get("source", ""),
    }


def _mock_dataset(plot_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "success": True,
        "source": "data_collector_dataset_pipeline_mock",
        "cadastralNumber": plot_data.get("cadastral_number", ""),
        "nspd": plot_data,
        "soil": {"success": True, "source": "mock", "soil": {}, "limitations": ["mock_mode_no_external_soil_request"]},
        "infrastructure": {"success": True, "source": "mock", "counts": {}, "limitations": ["mock_mode_no_overpass_request"]},
        "marketLiquidity": _market_disabled(),
        "warnings": ["ROSREESTR_MODE is not real; external parsers were not called."],
    }


def _market_disabled() -> dict[str, Any]:
    return {
        "success": False,
        "source": "disabled",
        "itemsCount": 0,
        "items": [],
        "limitations": ["market_search_disabled_until_price_comparison_is_reworked"],
    }


def _result_or_error(value: Any) -> dict[str, Any]:
    if isinstance(value, Exception):
        return {"success": False, "error": str(value)}
    return value


def _warnings(*values: Any) -> list[str]:
    result = []
    for value in values:
        if isinstance(value, Exception):
            result.append(str(value))
    return result


def _region_from_plot(plot_data: dict[str, Any]) -> str:
    text = json.dumps(plot_data, ensure_ascii=False)
    if "Ставрополь" in text:
        return "Ставропольский край"
    return ""


def _district_from_plot(plot_data: dict[str, Any]) -> str:
    text = json.dumps(plot_data, ensure_ascii=False)
    if "Шпаков" in text:
        return "Шпаковский район"
    return ""
