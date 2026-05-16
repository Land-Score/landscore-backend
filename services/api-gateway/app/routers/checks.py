from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

router = APIRouter()


class CreateCheckBody(BaseModel):
    cadastral_number: str | None = None
    address: str | None = None
    lat: float | None = None
    lng: float | None = None
    purpose: str = ""


@router.post("/")
async def create_check(body: CreateCheckBody, request: Request):
    # TODO: call check-service via gRPC
    return {"check_id": "stub", "status": "pending"}


@router.get("/{check_id}/status")
async def get_status(check_id: str, request: Request):
    # TODO: call check-service via gRPC
    return {"check_id": check_id, "status": "processing", "progress_pct": 0, "current_step": ""}


@router.get("/{check_id}/report")
async def get_report(check_id: str, request: Request):
    # TODO: call check-service via gRPC
    return {"check_id": check_id, "status": "pending"}


@router.get("/")
async def list_checks(request: Request):
    return {"checks": [], "total": 0}
