from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter()


class CreateSearchBody(BaseModel):
    query: str


@router.post("/")
async def create_search(body: CreateSearchBody, request: Request):
    return {"search_id": "stub", "status": "pending"}


@router.get("/{search_id}/status")
async def get_status(search_id: str, request: Request):
    return {"search_id": search_id, "status": "processing", "progress_pct": 0}


@router.get("/{search_id}/criteria")
async def get_criteria(search_id: str, request: Request):
    return {"search_id": search_id, "criteria_json": "{}", "confirmed": False}


@router.put("/{search_id}/criteria")
async def confirm_criteria(search_id: str, request: Request):
    return {"ok": True}


@router.get("/{search_id}/results")
async def get_results(search_id: str, request: Request):
    return {"candidates": []}


@router.get("/{search_id}/recommendation")
async def get_recommendation(search_id: str, request: Request):
    return {"search_id": search_id, "recommendation_json": "{}"}
