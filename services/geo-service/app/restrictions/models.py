from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


GeoJsonGeometry = dict[str, Any]


class RestrictionLayer(BaseModel):
    id: str
    layer_type: str
    name: str = ""
    source: str = ""
    geometry: GeoJsonGeometry
    restrictions: list[str] = Field(default_factory=list)
    normative_basis: list[str] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)


class RealEstateObject(BaseModel):
    cadastral_number: str
    object_type: Literal["building", "structure", "unfinished_construction", "single_real_estate_complex", "enterprise_complex", "unknown"] = "unknown"
    name: str = ""
    area_sqm: float | None = None
    geometry: GeoJsonGeometry | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class AgriculturalLandUseLayer(BaseModel):
    id: str
    land_use_type: Literal["arable", "pasture", "hayfield", "fallow", "perennial_planting", "forest", "water", "built_up", "unknown"]
    label: str
    geometry: GeoJsonGeometry
    source: str = ""
    confidence: float = 0.0
    properties: dict[str, Any] = Field(default_factory=dict)


class VisionMapInterpretation(BaseModel):
    enabled: bool = False
    source_image_id: str | None = None
    legend_items: list[dict[str, Any]] = Field(default_factory=list)
    inferred_land_use: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RestrictionAnalysisRequest(BaseModel):
    cadastral_number: str = ""
    scenario: str = "agriculture"
    parcel_geometry: GeoJsonGeometry
    parcel_area_ha: float | None = None
    restriction_layers: list[RestrictionLayer] = Field(default_factory=list)
    land_use_layers: list[AgriculturalLandUseLayer] = Field(default_factory=list)
    real_estate_objects: list[RealEstateObject] = Field(default_factory=list)
    vision_interpretation: VisionMapInterpretation | None = None


class RestrictionLayerImpact(BaseModel):
    id: str
    layer_type: str
    label: str
    group: str
    name: str
    severity: str
    area_loss_mode: str
    show_in_report: bool
    intersection_ha: float
    counted_in_loss_ha: float
    overlap_not_double_counted_ha: float
    restrictions: list[str] = Field(default_factory=list)
    normative_basis: list[str] = Field(default_factory=list)
    source: str = ""
    properties: dict[str, Any] = Field(default_factory=dict)


class LandUseCompositionItem(BaseModel):
    land_use_type: str
    label: str
    area_ha: float
    share_percent: float
    source: str = ""
    confidence: float = 0.0


class ChildRealEstateObjectImpact(BaseModel):
    cadastral_number: str
    object_type: str
    name: str = ""
    area_sqm: float | None = None
    intersection_ha: float | None = None
    inside_parcel: bool
    properties: dict[str, Any] = Field(default_factory=dict)


class MapLayerOutput(BaseModel):
    id: str
    layer_type: str
    label: str
    geojson: GeoJsonGeometry
    style: dict[str, Any] = Field(default_factory=dict)
    properties: dict[str, Any] = Field(default_factory=dict)


class RestrictionAnalysisResult(BaseModel):
    cadastral_number: str
    scenario: str
    parcel_area_ha: float
    restricted_area_ha: float
    usable_area_ha: float
    loss_percent: float
    layers: list[RestrictionLayerImpact]
    land_use_composition: list[LandUseCompositionItem]
    child_real_estate_objects: list[ChildRealEstateObjectImpact]
    map_layers: list[MapLayerOutput]
    warnings: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
