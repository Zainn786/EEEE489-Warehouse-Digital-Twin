"""Auditable KPI calculations for the warehouse dashboard.

Definitions:
- Throughput: distinct parcels whose latest status is sorted or dispatched.
- Routing accuracy: correctly_routed=True / all parcels with a boolean routing outcome.
- Average sorting time: mean(sorted_at - created_at) for valid timestamp pairs.
- Rejection rate: rejected / terminal outcomes (sorted, rejected, dispatched).
- Bay occupancy: total occupied slots / total capacity across supplied bays.
"""
from datetime import datetime


def _parse_timestamp(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def calculate_kpis(parcels, bays):
    parcels = parcels if isinstance(parcels, list) else []
    bays = bays if isinstance(bays, list) else []

    valid_ids = {
        str(p.get("parcel_id"))
        for p in parcels
        if isinstance(p, dict) and p.get("parcel_id")
    }
    throughput = len({
        str(p.get("parcel_id"))
        for p in parcels
        if isinstance(p, dict)
        and p.get("parcel_id")
        and str(p.get("status", "")).lower() in {"sorted", "dispatched"}
    })

    routed = [
        p for p in parcels
        if isinstance(p, dict) and isinstance(p.get("correctly_routed"), bool)
    ]
    routing_accuracy = (
        round(100 * sum(p["correctly_routed"] for p in routed) / len(routed), 1)
        if routed else None
    )

    durations = []
    for parcel in parcels:
        if not isinstance(parcel, dict):
            continue
        created = _parse_timestamp(parcel.get("created_at"))
        sorted_at = _parse_timestamp(parcel.get("sorted_at"))
        if created and sorted_at:
            seconds = (sorted_at - created).total_seconds()
            if seconds >= 0:
                durations.append(seconds)
    average_sorting_time = round(sum(durations) / len(durations), 1) if durations else None

    terminal = [
        p for p in parcels
        if isinstance(p, dict)
        and str(p.get("status", "")).lower() in {"sorted", "rejected", "dispatched"}
    ]
    rejected = sum(str(p.get("status", "")).lower() == "rejected" for p in terminal)
    rejection_rate = round(100 * rejected / len(terminal), 1) if terminal else None

    total_capacity = sum(
        max(0, float(b.get("capacity", 0) or 0))
        for b in bays if isinstance(b, dict)
    )
    total_occupied = sum(
        max(0, float(b.get("occupancy", 0) or 0))
        for b in bays if isinstance(b, dict)
    )
    bay_occupancy = round(100 * total_occupied / total_capacity, 1) if total_capacity else None

    return {
        "throughput": throughput,
        "routing_accuracy": routing_accuracy,
        "average_sorting_time": average_sorting_time,
        "rejection_rate": rejection_rate,
        "bay_occupancy": bay_occupancy,
    }
