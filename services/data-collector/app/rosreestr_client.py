"""
Rosreestr integration — dual-mode client.
Switch via env: ROSREESTR_MODE=mock | real
"""
from __future__ import annotations
import random
from dataclasses import dataclass


@dataclass
class PlotData:
    cadastral_number: str
    address: str
    area: float
    category: str
    allowed_use: str
    owner_type: str
    lat: float
    lng: float
    price: float
    raw_json: dict


@dataclass
class EGRNData:
    cadastral_number: str
    owner: str
    encumbrances: list[str]
    registration_date: str
    raw_json: dict


class MockRosreestrClient:
    """Returns realistic fake data for demo/hackathon purposes."""

    async def get_plot(self, cadastral_number: str) -> PlotData:
        return PlotData(
            cadastral_number=cadastral_number,
            address=f"Московская область, тест, уч. {cadastral_number[-4:]}",
            area=round(random.uniform(0.5, 50.0), 2),
            category="Земли сельскохозяйственного назначения",
            allowed_use="Для ведения крестьянского (фермерского) хозяйства",
            owner_type="Физическое лицо",
            lat=55.7 + random.uniform(-0.5, 0.5),
            lng=37.6 + random.uniform(-1.0, 1.0),
            price=round(random.uniform(500_000, 15_000_000), 0),
            raw_json={"source": "mock", "cadastral_number": cadastral_number},
        )

    async def get_egrn(self, cadastral_number: str) -> EGRNData:
        return EGRNData(
            cadastral_number=cadastral_number,
            owner="Иванов Иван Иванович",
            encumbrances=random.choice([[], ["Ипотека в пользу Сбербанк"], ["Аренда до 2027"]]),
            registration_date="2019-06-15",
            raw_json={"source": "mock"},
        )


class RealRosreestrClient:
    """Real integration with pkk.rosreestr.ru public API."""

    BASE_URL = "https://pkk.rosreestr.ru/api"

    async def get_plot(self, cadastral_number: str) -> PlotData:
        raise NotImplementedError("Real Rosreestr client not implemented yet")

    async def get_egrn(self, cadastral_number: str) -> EGRNData:
        raise NotImplementedError("Real Rosreestr client not implemented yet")


def get_client(mode: str = "mock") -> MockRosreestrClient | RealRosreestrClient:
    if mode == "real":
        return RealRosreestrClient()
    return MockRosreestrClient()
