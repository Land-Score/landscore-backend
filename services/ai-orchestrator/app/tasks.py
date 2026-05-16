"""Celery tasks — entry points for the agent pipelines."""
import asyncio
from app.celery_app import celery_app
from app.pipeline.context import AgentContext, UserProfile, PlotPassport
from app.pipeline.runner import PipelineRunner
from app.pipeline.check_pipeline import build_check_pipeline
from app.pipeline.search_pipeline import build_search_pipeline
import json


def _run_async(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@celery_app.task(bind=True, name="run_check")
def run_check_task(self, payload: dict):
    profile = UserProfile.from_json(json.loads(payload.get("user_profile_json", "{}")))
    ctx = AgentContext(
        job_id=self.request.id,
        owner_id=payload["check_id"],
        owner_type="check",
        profile=profile,
        plot=PlotPassport(
            cadastral_number=payload.get("cadastral_number", ""),
            address=payload.get("address", ""),
            lat=payload.get("lat", 0.0),
            lng=payload.get("lng", 0.0),
        ),
    )
    for optional_key in (
        "parcel_geometry_geojson",
        "raw_features_by_layer_json",
        "vision_interpretation_json",
    ):
        if optional_key in payload:
            ctx.set(optional_key, payload[optional_key])

    async def _run():
        pipeline = build_check_pipeline()

        async def on_progress(agent_name: str, pct: int, result) -> None:
            # TODO: update check-service via gRPC
            print(f"[{payload['check_id']}] {agent_name} → {pct}%")

        runner = PipelineRunner(pipeline, on_progress=on_progress)
        results = await runner.run(ctx)
        return results

    return _run_async(_run())


@celery_app.task(bind=True, name="run_search")
def run_search_task(self, payload: dict):
    profile = UserProfile.from_json(json.loads(payload.get("user_profile_json", "{}")))
    ctx = AgentContext(
        job_id=self.request.id,
        owner_id=payload["search_id"],
        owner_type="search",
        profile=profile,
    )

    async def _run():
        pipeline = build_search_pipeline()
        runner = PipelineRunner(pipeline)
        return await runner.run(ctx)

    return _run_async(_run())
