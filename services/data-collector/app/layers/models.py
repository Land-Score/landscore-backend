from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


GeoJsonGeometry = dict[str, Any]
LayerPayloadKind = Literal["restriction", "land_use", "real_estate_object", "valuation", "info"]


@dataclass(frozen=True)
class ScenarioEffect:
    """How the collected layer should be interpreted later by Geo Service."""

    area_loss_mode: str = "warning_only"
    show_in_report: bool = True


@dataclass(frozen=True)
class LayerCatalogEntry:
    """Known source layer and its normalized meaning.

    Data Collector owns this catalog because it knows external source names,
    category ids, layer groups, and raw attributes. Geo Service receives the
    normalized type plus optional scenario effects and only calculates geometry.
    """

    key: str
    source_group: str
    source_name: str
    payload_kind: LayerPayloadKind
    normalized_type: str
    label: str
    severity: str = "info"
    default_restrictions: tuple[str, ...] = ()
    normative_basis: tuple[str, ...] = ()
    scenario_effects: dict[str, ScenarioEffect] = field(default_factory=dict)
    source_layer_ids: tuple[str, ...] = ()


@dataclass
class CollectedSpatialLayer:
    id: str
    source_layer_key: str
    source_layer_name: str
    source_group: str
    payload_kind: LayerPayloadKind
    normalized_type: str
    label: str
    geometry: GeoJsonGeometry | None
    properties: dict[str, Any] = field(default_factory=dict)
    restrictions: list[str] = field(default_factory=list)
    normative_basis: list[str] = field(default_factory=list)
    scenario_effects: dict[str, dict[str, Any]] = field(default_factory=dict)
    source: str = "data-collector"
    confidence: float = 0.7


@dataclass
class CollectedPlotSpatialData:
    cadastral_number: str
    parcel_geometry: GeoJsonGeometry | None = None
    restriction_layers: list[CollectedSpatialLayer] = field(default_factory=list)
    land_use_layers: list[CollectedSpatialLayer] = field(default_factory=list)
    real_estate_objects: list[CollectedSpatialLayer] = field(default_factory=list)
    child_real_estate_objects: list[CollectedSpatialLayer] = field(default_factory=list)
    land_parts: list[CollectedSpatialLayer] = field(default_factory=list)
    land_composition: list[dict[str, Any]] = field(default_factory=list)
    valuation_layers: list[CollectedSpatialLayer] = field(default_factory=list)
    informational_layers: list[CollectedSpatialLayer] = field(default_factory=list)
    raw_layers: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

