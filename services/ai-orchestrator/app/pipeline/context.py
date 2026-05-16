from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class UserProfile:
    user_id: str
    client_type: str = "private"
    main_task: str = "land_check"
    region: str = ""
    priority: list[str] = field(default_factory=list)
    risk_tolerance: str = "medium"
    preferred_scenarios: list[str] = field(default_factory=list)

    @classmethod
    def from_json(cls, data: dict) -> "UserProfile":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class PlotPassport:
    """Unified plot data accumulated across agents."""
    cadastral_number: str = ""
    address: str = ""
    area: float = 0.0
    category: str = ""
    allowed_use: str = ""
    owner_type: str = ""
    lat: float = 0.0
    lng: float = 0.0
    price: float = 0.0
    egrn_data: dict = field(default_factory=dict)
    extracted_documents: list[dict] = field(default_factory=list)


@dataclass
class AgentContext:
    """Shared context passed through the pipeline. Each agent sees only what it needs."""
    job_id: str
    owner_id: str          # check_id or search_id
    owner_type: str        # "check" | "search"
    profile: UserProfile
    plot: PlotPassport = field(default_factory=PlotPassport)

    # Accumulated agent outputs — keyed by agent name
    _facts: dict[str, Any] = field(default_factory=dict)

    def set(self, key: str, value: Any) -> None:
        self._facts[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self._facts.get(key, default)

    def get_for_agent(self, *keys: str) -> dict[str, Any]:
        """Context isolation: agent receives only the keys it declares."""
        return {k: self._facts[k] for k in keys if k in self._facts}
