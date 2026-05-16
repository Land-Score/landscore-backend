import json
import re
from typing import Any

import grpc
from fastapi import APIRouter, HTTPException, Request

from app.config import settings
from app.errors import raise_for_grpc
from app.models import CadastralLookupRequest, CadastralLookupResponse

router = APIRouter()

_CADASTRAL_NUMBER_RE = re.compile(r"^\d{2}:\d{2}:\d{1,12}:\d{1,12}$")


def _loads(value: str) -> dict[str, Any]:
    if not value:
        return {}
    parsed = json.loads(value)
    return parsed if isinstance(parsed, dict) else {}


@router.post(
    "/lookup",
    response_model=CadastralLookupResponse,
    summary="Получить данные по кадастровому номеру",
    description=(
        "Публичная ручка без авторизации. Принимает кадастровый номер и возвращает "
        "собранный data-collector набор: кадастровые данные, почвы, инфраструктуру, "
        "рыночные сигналы и предупреждения."
    ),
)
async def lookup_cadastral_plot(body: CadastralLookupRequest, request: Request) -> CadastralLookupResponse:
    cadastral_number = body.cadastral_number.strip()
    if not _CADASTRAL_NUMBER_RE.match(cadastral_number):
        raise HTTPException(status_code=400, detail="Кадастровый номер должен быть в формате 26:11:101101:53")

    import data_collector_pb2

    try:
        response = await request.app.state.data_collector_stub.CollectPlotDataset(
            data_collector_pb2.CadastralRequest(cadastral_number=cadastral_number),
            timeout=settings.cadastral_lookup_timeout,
        )
    except grpc.RpcError as exc:
        raise_for_grpc(exc)

    try:
        return CadastralLookupResponse(
            success=response.success,
            cadastral_number=response.cadastral_number or cadastral_number,
            source=response.source,
            nspd=_loads(response.nspd_json),
            soil=_loads(response.soil_json),
            infrastructure=_loads(response.infrastructure_json),
            market_liquidity=_loads(response.market_json),
            warnings=list(response.warnings),
            raw=_loads(response.raw_json),
        )
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail=f"data-collector returned invalid JSON: {exc}") from exc
