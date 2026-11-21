"""Parcel state model; statuses mirror backend.graph.ontology."""
from dataclasses import dataclass


@dataclass
class Parcel:
    parcel_id: str
    target_bay: str
    status: str = "created"
    stage_elapsed: float = 0.0
    scan_ok: bool = True
    actual_bay: str | None = None
    bay_elapsed: float = 0.0
