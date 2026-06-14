from __future__ import annotations

from app.pipeline.base import Agent, AgentResult
from app.pipeline.context import AgentContext


class SearchPlanningAgent(Agent):
    """Чисто вычислительный агент — без LLM.

    Лёгкое планирование: определяет, какие данные/документы нужно запросить
    для проверки участка, исходя из того, чего не хватает в ctx.plot.
    """

    name = "SearchPlanningAgent"

    async def run(self, input: dict, ctx: AgentContext) -> AgentResult:
        plot = ctx.plot
        required: list[dict] = []

        def _need(key: str, label: str, present: bool, source: str) -> None:
            if not present:
                required.append({"data": key, "label": label, "source": source})

        _need("egrn", "Актуальная выписка ЕГРН", bool(plot.egrn_data), "rosreestr")
        _need("cadastral", "Кадастровый номер / идентификация участка", bool(plot.cadastral_number), "rosreestr")
        _need("geometry", "Геометрия границ участка", bool(ctx.get("parcel_geometry_geojson")), "data-collector")
        _need("category", "Категория земель", bool(plot.category), "nspd")
        _need("allowed_use", "Вид разрешённого использования (ВРИ)", bool(plot.allowed_use), "nspd")
        _need("coordinates", "Координаты участка", bool(plot.lat and plot.lng), "geo-service")
        _need("price", "Запрашиваемая цена", bool(plot.price), "user")
        _need("documents", "Загруженные документы", bool(plot.extracted_documents), "user")

        # Запросы к внешним сервисам, которые имеет смысл выполнить.
        data_requests: list[str] = []
        if plot.cadastral_number:
            data_requests.extend(["egrn", "spatial_layers", "plot_dataset"])
        if plot.lat and plot.lng:
            data_requests.append("location_analysis")

        plan = {
            "required_data": required,
            "missing_count": len(required),
            "data_requests": data_requests,
            "ready_for_analysis": bool(plot.cadastral_number or (plot.lat and plot.lng)),
        }
        ctx.set("search_plan", plan)
        return AgentResult(success=True, data=plan)
