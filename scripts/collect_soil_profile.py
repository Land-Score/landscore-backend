from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

TEXTURE_OPTIONS = {
    "1": "sand",
    "2": "sandy_loam",
    "3": "loam",
    "4": "clay_loam",
    "5": "clay",
    "6": "peat",
    "7": "unknown",
}

DRAINAGE_OPTIONS = {
    "1": "good",
    "2": "moderate",
    "3": "poor",
    "4": "waterlogged",
    "5": "unknown",
}

CONTAMINATION_KEYS = {
    "lead_mg_kg": "Свинец Pb, мг/кг",
    "cadmium_mg_kg": "Кадмий Cd, мг/кг",
    "arsenic_mg_kg": "Мышьяк As, мг/кг",
    "mercury_mg_kg": "Ртуть Hg, мг/кг",
    "petroleum_mg_kg": "Нефтепродукты, мг/кг",
}


def ask_text(prompt: str, *, default: str = "", required: bool = False) -> str:
    while True:
        suffix = f" [{default}]" if default else ""
        value = input(f"{prompt}{suffix}: ").strip()
        if not value:
            value = default
        if value or not required:
            return value
        print("Значение обязательно.")


def ask_float(
    prompt: str,
    *,
    default: float | None = None,
    min_value: float | None = None,
    max_value: float | None = None,
    required: bool = False,
) -> float | None:
    while True:
        suffix = f" [{default}]" if default is not None else ""
        raw_value = input(f"{prompt}{suffix}: ").strip().replace(",", ".")
        if not raw_value and default is not None:
            return default
        if not raw_value and not required:
            return None
        try:
            value = float(raw_value)
        except ValueError:
            print("Введите число, например 6.5")
            continue
        if min_value is not None and value < min_value:
            print(f"Минимальное значение: {min_value}")
            continue
        if max_value is not None and value > max_value:
            print(f"Максимальное значение: {max_value}")
            continue
        return value


def ask_int(
    prompt: str,
    *,
    default: int | None = None,
    min_value: int | None = None,
    max_value: int | None = None,
    required: bool = False,
) -> int | None:
    while True:
        suffix = f" [{default}]" if default is not None else ""
        raw_value = input(f"{prompt}{suffix}: ").strip()
        if not raw_value and default is not None:
            return default
        if not raw_value and not required:
            return None
        try:
            value = int(raw_value)
        except ValueError:
            print("Введите целое число, например 20")
            continue
        if min_value is not None and value < min_value:
            print(f"Минимальное значение: {min_value}")
            continue
        if max_value is not None and value > max_value:
            print(f"Максимальное значение: {max_value}")
            continue
        return value


def ask_choice(prompt: str, options: dict[str, str], *, default_key: str) -> str:
    print(prompt)
    for key, label in options.items():
        marker = " default" if key == default_key else ""
        print(f"  {key}) {label}{marker}")
    while True:
        value = input(f"Выбор [{default_key}]: ").strip() or default_key
        if value in options:
            return options[value]
        print("Выберите один из предложенных вариантов.")


def ask_yes_no(prompt: str, *, default: bool = False) -> bool:
    default_text = "y" if default else "n"
    while True:
        value = input(f"{prompt} [y/n, default {default_text}]: ").strip().lower()
        if not value:
            return default
        if value in {"y", "yes", "д", "да"}:
            return True
        if value in {"n", "no", "н", "нет"}:
            return False
        print("Введите y или n.")


def acidity_class(ph: float | None) -> str:
    if ph is None:
        return "unknown"
    if ph < 4.5:
        return "strongly_acidic"
    if ph < 5.5:
        return "acidic"
    if ph < 6.5:
        return "slightly_acidic"
    if ph <= 7.5:
        return "neutral"
    if ph <= 8.5:
        return "alkaline"
    return "strongly_alkaline"


def humus_class(humus_pct: float | None) -> str:
    if humus_pct is None:
        return "unknown"
    if humus_pct < 2:
        return "low"
    if humus_pct < 4:
        return "medium"
    if humus_pct < 6:
        return "high"
    return "very_high"


def nutrient_class(value: float | None, *, low: float, high: float) -> str:
    if value is None:
        return "unknown"
    if value < low:
        return "low"
    if value <= high:
        return "medium"
    return "high"


def build_risk_flags(profile: dict[str, Any]) -> list[dict[str, str]]:
    lab = profile["lab_results"]
    site = profile["site_conditions"]
    flags: list[dict[str, str]] = []

    ph = lab.get("ph")
    if ph is not None and (ph < 5.0 or ph > 8.5):
        flags.append(
            {
                "severity": "warning",
                "code": "ph_out_of_range",
                "message": "pH вне комфортного диапазона для большинства культур.",
            }
        )

    if lab.get("humus_pct") is not None and lab["humus_pct"] < 2:
        flags.append(
            {
                "severity": "warning",
                "code": "low_humus",
                "message": "Низкое содержание гумуса, потребуется органическое улучшение почвы.",
            }
        )

    if lab.get("salinity_ds_m") is not None and lab["salinity_ds_m"] > 2:
        flags.append(
            {
                "severity": "critical",
                "code": "salinity_risk",
                "message": "Повышенная засоленность может ограничить сельхозиспользование.",
            }
        )

    if site.get("groundwater_depth_m") is not None and site["groundwater_depth_m"] < 1.0:
        flags.append(
            {
                "severity": "warning",
                "code": "high_groundwater",
                "message": "Близкие грунтовые воды повышают риск подтопления и ограничивают строительство.",
            }
        )

    if site.get("drainage") in {"poor", "waterlogged"}:
        flags.append(
            {
                "severity": "warning",
                "code": "poor_drainage",
                "message": "Плохой дренаж требует отдельной инженерной проверки.",
            }
        )

    contaminants = profile.get("contaminants", {})
    if any(value is not None and value > 0 for value in contaminants.values()):
        flags.append(
            {
                "severity": "info",
                "code": "contaminants_present",
                "message": "Указаны загрязнители; нужны сравнение с ПДК и лабораторный протокол.",
            }
        )

    return flags


def build_profile() -> dict[str, Any]:
    print("Сбор данных о составе почвы для LandScore AI")
    print("Оставляйте поле пустым, если данных нет.\n")

    plot = {
        "cadastral_number": ask_text("Кадастровый номер"),
        "address": ask_text("Адрес/описание локации"),
        "lat": ask_float("Широта WGS84", min_value=-90, max_value=90),
        "lng": ask_float("Долгота WGS84", min_value=-180, max_value=180),
    }

    sampling = {
        "sample_date": ask_text("Дата отбора пробы YYYY-MM-DD", default=str(date.today())),
        "sample_depth_cm": ask_int("Глубина отбора, см", default=20, min_value=0, max_value=300),
        "lab_name": ask_text("Лаборатория / источник данных"),
        "protocol_number": ask_text("Номер протокола анализа"),
    }

    soil = {
        "texture": ask_choice("Механический состав почвы", TEXTURE_OPTIONS, default_key="3"),
        "soil_type": ask_text("Тип почвы, если известен", default="unknown"),
    }

    lab_results = {
        "ph": ask_float("pH водной вытяжки", min_value=0, max_value=14),
        "humus_pct": ask_float("Гумус, %", min_value=0, max_value=100),
        "nitrogen_mg_kg": ask_float("Азот N, мг/кг", min_value=0),
        "phosphorus_mg_kg": ask_float("Фосфор P2O5, мг/кг", min_value=0),
        "potassium_mg_kg": ask_float("Калий K2O, мг/кг", min_value=0),
        "salinity_ds_m": ask_float("Засоленность EC, dS/m", min_value=0),
        "carbonate_pct": ask_float("Карбонаты, %", min_value=0, max_value=100),
    }

    site_conditions = {
        "groundwater_depth_m": ask_float("Глубина грунтовых вод, м", min_value=0),
        "drainage": ask_choice("Дренаж участка", DRAINAGE_OPTIONS, default_key="5"),
        "slope_pct": ask_float("Уклон, %", min_value=0),
        "visible_erosion": ask_yes_no("Есть признаки эрозии?", default=False),
        "flooding_history": ask_yes_no("Были подтопления?", default=False),
    }

    contaminants: dict[str, float | None] = {}
    if ask_yes_no("Есть данные по загрязнителям?", default=False):
        for key, label in CONTAMINATION_KEYS.items():
            contaminants[key] = ask_float(label, min_value=0)

    profile = {
        "schema": "landscore.soil_profile.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "plot": plot,
        "sampling": sampling,
        "soil": soil,
        "lab_results": lab_results,
        "site_conditions": site_conditions,
        "contaminants": contaminants,
    }
    profile["derived"] = {
        "acidity_class": acidity_class(lab_results["ph"]),
        "humus_class": humus_class(lab_results["humus_pct"]),
        "nitrogen_class": nutrient_class(lab_results["nitrogen_mg_kg"], low=10, high=20),
        "phosphorus_class": nutrient_class(lab_results["phosphorus_mg_kg"], low=50, high=100),
        "potassium_class": nutrient_class(lab_results["potassium_mg_kg"], low=80, high=150),
    }
    profile["risk_flags"] = build_risk_flags(profile)
    return profile


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect soil composition data for later LandScore analysis.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("tmp/soil-profile.json"),
        help="JSON output path",
    )
    parser.add_argument("--compact", action="store_true", help="Print compact JSON")
    args = parser.parse_args()

    profile = build_profile()
    json_text = json.dumps(
        profile,
        ensure_ascii=False,
        indent=None if args.compact else 2,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json_text + "\n", encoding="utf-8")

    print("\nГотово.")
    print(f"JSON сохранён: {args.output}")
    print(json_text)


if __name__ == "__main__":
    main()
