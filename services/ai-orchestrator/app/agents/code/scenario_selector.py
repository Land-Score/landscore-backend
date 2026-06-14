from __future__ import annotations

from app.pipeline.base import Agent, AgentResult
from app.pipeline.context import AgentContext

# Канонические ключи сценариев, которые понимают агенты сценариев и калькулятор.
ALL_SCENARIOS = ["construction", "agriculture", "lease", "resale", "mixed_use"]

# Соответствие категории земель допустимым сценариям.
_AGRI_CATEGORY_MARKERS = ("сельхоз", "сельскохоз")
_SETTLEMENT_CATEGORY_MARKERS = ("населен", "поселен")

# Нормализация значений из профиля к каноническим ключам.
_PROFILE_ALIASES = {
    "construction": "construction",
    "development": "construction",
    "agriculture": "agriculture",
    "agro": "agriculture",
    "lease": "lease",
    "rent": "lease",
    "resale": "resale",
    "mixed": "mixed_use",
    "mixed_use": "mixed_use",
}


def _norm(text: str) -> str:
    return (text or "").strip().lower()


def _is_agricultural(category: str) -> bool:
    cat = _norm(category)
    return any(marker in cat for marker in _AGRI_CATEGORY_MARKERS)


def _is_settlement(category: str) -> bool:
    cat = _norm(category)
    return any(marker in cat for marker in _SETTLEMENT_CATEGORY_MARKERS)


def _construction_allowed(category: str, allowed_use: str) -> bool:
    use = _norm(allowed_use)
    if _is_settlement(category):
        return True
    if _is_agricultural(category):
        # Стройка на сельхозземле почти всегда недопустима, кроме явного ВРИ.
        return any(m in use for m in ("строит", "жилищ", "ижс", "дачн", "садов"))
    # Категория неизвестна — ориентируемся на ВРИ.
    return any(m in use for m in ("строит", "жилищ", "ижс", "произв", "склад", "коммер"))


def _agriculture_allowed(category: str, allowed_use: str) -> bool:
    use = _norm(allowed_use)
    if _is_agricultural(category):
        return True
    return any(m in use for m in ("сельскохоз", "сельхоз", "растен", "животнов", "садов", "огород"))


class ScenarioSelectorAgent(Agent):
    """Чисто вычислительный агент — без LLM.

    Выбирает релевантные сценарии использования для ДАННОГО пользователя на
    основе профиля (preferred_scenarios) и фактов участка (категория, ВРИ).
    """

    name = "ScenarioSelectorAgent"

    async def run(self, input: dict, ctx: AgentContext) -> AgentResult:
        # Изоляция контекста: агент видит только профиль и нормализованные факты.
        isolated = ctx.get_for_agent("normalized_facts")
        facts = isolated.get("normalized_facts") or {}
        category = facts.get("category") or ctx.plot.category or ""
        allowed_use = facts.get("allowed_use") or ctx.plot.allowed_use or ""

        preferred = []
        for raw in ctx.profile.preferred_scenarios or []:
            key = _PROFILE_ALIASES.get(_norm(raw))
            if key and key not in preferred:
                preferred.append(key)

        # Если в профиле ничего не указано — рассматриваем все сценарии.
        candidates = preferred or list(ALL_SCENARIOS)

        selected: list[str] = []
        rationale: dict[str, str] = {}
        for scenario in candidates:
            applicable, reason = self._evaluate(scenario, category, allowed_use)
            rationale[scenario] = reason
            if applicable:
                selected.append(scenario)

        # Подстраховка: если ничего не прошло, оставляем перепродажу как универсальный сценарий.
        if not selected:
            selected = ["resale"]
            rationale.setdefault("resale", "Универсальный сценарий перепродажи доступен по умолчанию.")

        result = {
            "selected_scenarios": selected,
            "rationale": rationale,
            "from_profile": bool(preferred),
        }
        ctx.set("selected_scenarios", selected)
        return AgentResult(success=True, data=result)

    def _evaluate(self, scenario: str, category: str, allowed_use: str) -> tuple[bool, str]:
        if scenario == "construction":
            if _construction_allowed(category, allowed_use):
                return True, "Категория и ВРИ допускают строительство."
            return False, "Строительство не разрешено категорией/ВРИ (например, земли сельхозназначения)."
        if scenario == "agriculture":
            if _agriculture_allowed(category, allowed_use):
                return True, "Категория и ВРИ допускают сельхозиспользование."
            return False, "Сельхозиспользование не соответствует категории/ВРИ участка."
        if scenario == "lease":
            return True, "Сдача в аренду применима для большинства участков."
        if scenario == "resale":
            return True, "Перепродажа применима независимо от категории."
        if scenario == "mixed_use":
            # Смешанный сценарий имеет смысл, если доступны и стройка, и сельхоз/аренда.
            if _construction_allowed(category, allowed_use) and _agriculture_allowed(category, allowed_use):
                return True, "Возможна комбинация застройки и сельхозиспользования."
            return False, "Недостаточно совместимых видов использования для смешанного сценария."
        return False, "Неизвестный сценарий."
