from __future__ import annotations

from dataclasses import dataclass, field


SCENARIO_AGRICULTURE = "agriculture"
SCENARIO_RENT = "rent"
SCENARIO_CONSTRUCTION = "construction"


@dataclass(frozen=True)
class RestrictionRule:
    """Fallback interpretation for normalized layer types.

    External NSPD/source layer names live in data-collector. Geo-service only
    keeps conservative defaults for normalized types in case the request arrives
    without explicit scenario effects.
    """

    layer_type: str
    label: str
    group: str
    severity: str
    area_loss_mode_by_scenario: dict[str, str]
    report_by_scenario: dict[str, bool]
    default_restrictions: list[str] = field(default_factory=list)
    normative_basis: list[str] = field(default_factory=list)
    priority: int = 100

    def loss_mode(self, scenario: str) -> str:
        return self.area_loss_mode_by_scenario.get(scenario, self.area_loss_mode_by_scenario.get("*", "warning_only"))

    def show_in_report(self, scenario: str) -> bool:
        return self.report_by_scenario.get(scenario, self.report_by_scenario.get("*", True))


def _rule(
    layer_type: str,
    label: str,
    group: str,
    severity: str = "restricted_use",
    *,
    agriculture: str = "warning_only",
    rent: str = "warning_only",
    construction: str = "warning_only",
    report: bool = True,
    restrictions: list[str] | None = None,
    basis: list[str] | None = None,
) -> RestrictionRule:
    return RestrictionRule(
        layer_type=layer_type,
        label=label,
        group=group,
        severity=severity,
        area_loss_mode_by_scenario={
            SCENARIO_AGRICULTURE: agriculture,
            SCENARIO_RENT: rent,
            SCENARIO_CONSTRUCTION: construction,
        },
        report_by_scenario={"*": report},
        default_restrictions=restrictions or [],
        normative_basis=basis or [],
    )


RULES: dict[str, RestrictionRule] = {
    "coastal_protective_strip": _rule(
        "coastal_protective_strip",
        "Прибрежная защитная полоса",
        "water",
        "hard_limit",
        agriculture="exclude_from_usable",
        rent="exclude_from_usable",
        construction="exclude_from_usable",
        restrictions=["В прибрежной защитной полосе запрещена распашка земель."],
        basis=["Водный кодекс РФ, статья 65"],
    ),
    "water_protection_zone": _rule(
        "water_protection_zone",
        "Водоохранная зона",
        "water",
        restrictions=["Специальный режим хозяйственной деятельности у водного объекта."],
        basis=["Водный кодекс РФ, статья 65"],
    ),
    "shoreline": _rule(
        "shoreline",
        "Береговая линия",
        "water",
        restrictions=["Линейный слой используется для уточнения водных ограничений."],
        basis=["Водный кодекс РФ, статья 65"],
    ),
    "cultural_heritage_protection_zone": _rule(
        "cultural_heritage_protection_zone",
        "ЗОУИТ объекта культурного наследия",
        "cultural_heritage",
        "hard_limit",
        construction="exclude_from_usable",
        restrictions=["Режим охраны может ограничивать строительство, земляные работы и хозяйственную деятельность."],
        basis=["ФЗ N 73-ФЗ об объектах культурного наследия"],
    ),
    "heritage_site_territory": _rule(
        "heritage_site_territory",
        "Территория объекта культурного наследия",
        "cultural_heritage",
        "hard_limit",
        agriculture="exclude_from_usable",
        rent="exclude_from_usable",
        construction="exclude_from_usable",
        restrictions=["Режим территории ОКН требует отдельной правовой проверки."],
        basis=["ФЗ N 73-ФЗ об объектах культурного наследия"],
    ),
    "power_transport_communication_zone": _rule(
        "power_transport_communication_zone",
        "Охранная зона инженерного или транспортного объекта",
        "engineering",
        construction="exclude_from_usable",
        restrictions=["Режим зависит от вида линейного, энергетического или транспортного объекта."],
        basis=["Сведения ЕГРН о ЗОУИТ"],
    ),
    "protected_natural_area": _rule(
        "protected_natural_area",
        "Особо охраняемая природная территория",
        "nature",
        "hard_limit",
        agriculture="exclude_from_usable",
        rent="exclude_from_usable",
        construction="exclude_from_usable",
        restrictions=["Режим ООПТ может запрещать или ограничивать обработку земли."],
        basis=["ФЗ N 33-ФЗ об особо охраняемых природных территориях"],
    ),
    "forest": _rule(
        "forest",
        "Лесной фонд / лесничество",
        "nature",
        agriculture="exclude_from_usable",
        rent="exclude_from_usable",
        construction="exclude_from_usable",
        restrictions=["Нужно проверить пересечение с лесным фондом и допустимость использования."],
        basis=["Лесной кодекс РФ"],
    ),
    "forest_park_boundary": _rule(
        "forest_park_boundary",
        "Граница лесопарка",
        "nature",
        construction="exclude_from_usable",
        restrictions=["Требуется проверка режима лесопарковой территории."],
        basis=["Лесной кодекс РФ"],
    ),
    "hunting_ground": _rule(
        "hunting_ground",
        "Охотничье угодье",
        "nature",
        "info",
        restrictions=["Обычно не исключает площадь, но важно как соседний режим использования."],
    ),
    "territorial_zone": _rule(
        "territorial_zone",
        "Территориальная зона",
        "planning",
        restrictions=["Проверяется соответствие ВРИ градостроительному регламенту."],
        basis=["ПЗЗ муниципального образования"],
    ),
    "red_line": _rule(
        "red_line",
        "Красная линия",
        "planning",
        "hard_limit",
        construction="exclude_from_usable",
        restrictions=["Ограничивает размещение объектов капитального строительства."],
        basis=["Градостроительный кодекс РФ"],
    ),
    "security_zone": _rule(
        "security_zone",
        "Охранная зона объекта безопасности",
        "security",
        "hard_limit",
        construction="exclude_from_usable",
        restrictions=["Режим зоны безопасности требует отдельной проверки по объекту."],
        basis=["Сведения ЕГРН о ЗОУИТ"],
    ),
    "other_zouit": _rule(
        "other_zouit",
        "Иная ЗОУИТ",
        "zouit",
        restrictions=["Нужно проверить вид зоны и ее режим по ЕГРН/акту установления."],
        basis=["Сведения ЕГРН о ЗОУИТ"],
    ),
    "flooding_zone": _rule(
        "flooding_zone",
        "Зона затопления",
        "negative_process",
        "hard_limit",
        construction="exclude_from_usable",
        restrictions=["Риск затопления; для строительства может быть стоп-фактором."],
    ),
    "underflooding_zone": _rule(
        "underflooding_zone",
        "Зона подтопления",
        "negative_process",
        construction="exclude_from_usable",
        restrictions=["Риск подтопления; требуется инженерная проверка."],
    ),
    "erosion": _rule(
        "erosion",
        "Эрозия",
        "negative_process",
        restrictions=["Не исключает площадь автоматически, но снижает агропригодность."],
    ),
    "waterlogging": _rule(
        "waterlogging",
        "Переувлажнение / заболачивание",
        "negative_process",
        restrictions=["Может ограничивать обработку и требовать мелиорации."],
    ),
    "desertification": _rule(
        "desertification",
        "Опустынивание",
        "negative_process",
        restrictions=["Снижает агропригодность; режим зависит от материалов мониторинга."],
    ),
    "littering": _rule(
        "littering",
        "Захламление",
        "negative_process",
        restrictions=["Требует проверки фактического состояния и затрат на очистку."],
    ),
    "landslide": _rule(
        "landslide",
        "Оползневые и обвально-осыпные процессы",
        "negative_process",
        "hard_limit",
        construction="exclude_from_usable",
        restrictions=["Требуется инженерно-геологическая проверка."],
    ),
    "abrasion": _rule(
        "abrasion",
        "Абразия",
        "negative_process",
        restrictions=["Береговой негативный процесс, требует проверки устойчивости."],
    ),
    "disturbed_land": _rule(
        "disturbed_land",
        "Нарушенные земли",
        "negative_process",
        restrictions=["Требует оценки рекультивации и фактической пригодности."],
    ),
    "burned_area": _rule(
        "burned_area",
        "Гари",
        "negative_process",
        restrictions=["Требует проверки восстановления и фактического состояния."],
    ),
    "salinization": _rule(
        "salinization",
        "Засоление",
        "negative_process",
        restrictions=["Снижает агропригодность и может требовать мелиорации."],
    ),
    "environmental_damage_cleanup": _rule(
        "environmental_damage_cleanup",
        "Территория ликвидации накопленного вреда",
        "environment",
        "hard_limit",
        agriculture="exclude_from_usable",
        rent="exclude_from_usable",
        construction="exclude_from_usable",
        restrictions=["Требуется экологическая и правовая проверка ограничений использования."],
    ),
    "gambling_zone": _rule(
        "gambling_zone",
        "Игорная зона",
        "economic_zone",
        "info",
        agriculture="ignore",
        rent="ignore",
        construction="warning_only",
        restrictions=["Для сельхозсценария не влияет на полезную площадь."],
    ),
}


def get_rule(layer_type: str) -> RestrictionRule:
    return RULES.get(
        layer_type,
        RestrictionRule(
            layer_type=layer_type,
            label=layer_type,
            group="other",
            severity="unknown",
            area_loss_mode_by_scenario={"*": "warning_only"},
            report_by_scenario={"*": True},
            default_restrictions=["Неизвестный нормализованный слой: нужна ручная классификация ограничения."],
            normative_basis=["Источник слоя / сведения ЕГРН"],
            priority=999,
        ),
    )
