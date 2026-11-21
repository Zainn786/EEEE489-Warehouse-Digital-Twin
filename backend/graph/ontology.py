"""Ontology constants for the warehouse digital twin (Member 2).

Single source of truth for node identifiers, status values and the
mapping between parcel events, statuses and locations. Everything here
mirrors the team agreement (sections 6 and 7).
"""

# ---------------------------------------------------------------------------
# Stable identifiers of the fixed equipment nodes
# ---------------------------------------------------------------------------
WAREHOUSE_ID = "WH_01"
WAREHOUSE_NAME = "Group 6 Fulfillment Sorting Area"
CONVEYOR_ID = "CONVEYOR_01"
SCANNER_ID = "SCANNER_01"
SORTER_ID = "SORTER_01"
REJECT_LANE_ID = "REJECT_LANE_01"
BAY_IDS = ("A", "B", "C")
BAY_CAPACITY = 5

# (sensor_id, sensor_type, monitored label, monitored key property, monitored id)
SENSORS = (
    ("SENSOR_CONVEYOR_SPEED", "conveyor_speed", "Conveyor", "conveyor_id", CONVEYOR_ID),
    ("SENSOR_PARCEL_PRESENCE", "parcel_presence", "Conveyor", "conveyor_id", CONVEYOR_ID),
    ("SENSOR_BARCODE", "barcode", "Scanner", "scanner_id", SCANNER_ID),
    ("SENSOR_BAY_A", "bay_occupancy", "DispatchBay", "bay_id", "A"),
    ("SENSOR_BAY_B", "bay_occupancy", "DispatchBay", "bay_id", "B"),
    ("SENSOR_BAY_C", "bay_occupancy", "DispatchBay", "bay_id", "C"),
)

# The ten agreed entity types and the property that identifies each one.
ENTITY_KEYS = {
    "Warehouse": "warehouse_id",
    "Conveyor": "conveyor_id",
    "Scanner": "scanner_id",
    "Sensor": "sensor_id",
    "Sorter": "sorter_id",
    "Parcel": "parcel_id",  # unique together with run_id
    "DispatchBay": "bay_id",
    "RejectLane": "lane_id",
    "Alert": "alert_id",
    "TelemetryEvent": "event_id",
}

# ---------------------------------------------------------------------------
# Agreed status / location vocabularies
# ---------------------------------------------------------------------------
PARCEL_STATUSES = ("created", "scanned", "at_sorter", "sorted", "rejected", "dispatched")
ACTIVE_PARCEL_STATUSES = ("created", "scanned", "at_sorter")
SIMULATOR_STATUSES = ("stopped", "running", "paused")
LOCATIONS = ("ENTRY", "SCANNER", "SORTER", "BAY_A", "BAY_B", "BAY_C", "REJECT_LANE", "DISPATCHED")

# Parcels may only move forward through these ranks. "sorted" and
# "rejected" share a rank, so a parcel can never become both.
STATUS_RANK = {
    None: -1,
    "created": 0,
    "scanned": 1,
    "at_sorter": 2,
    "sorted": 3,
    "rejected": 3,
    "dispatched": 4,
}

PARCEL_EVENT_STATUS = {
    "parcel_created": "created",
    "parcel_scanned": "scanned",
    "parcel_at_sorter": "at_sorter",
    "parcel_sorted": "sorted",
    "parcel_rejected": "rejected",
    "parcel_dispatched": "dispatched",
}

EQUIPMENT_EVENT_TYPES = ("conveyor_status", "scanner_status", "sorter_status", "bay_occupancy_changed")
SIMULATOR_EVENT_TYPES = ("simulator_status",)
KNOWN_EVENT_TYPES = tuple(PARCEL_EVENT_STATUS) + EQUIPMENT_EVENT_TYPES + SIMULATOR_EVENT_TYPES

# Location value -> (node label, key property, key value). DISPATCHED has no node.
LOCATION_NODES = {
    "ENTRY": ("Conveyor", "conveyor_id", CONVEYOR_ID),
    "SCANNER": ("Scanner", "scanner_id", SCANNER_ID),
    "SORTER": ("Sorter", "sorter_id", SORTER_ID),
    "BAY_A": ("DispatchBay", "bay_id", "A"),
    "BAY_B": ("DispatchBay", "bay_id", "B"),
    "BAY_C": ("DispatchBay", "bay_id", "C"),
    "REJECT_LANE": ("RejectLane", "lane_id", REJECT_LANE_ID),
    "DISPATCHED": None,
}

# Bay operating status derived from occupancy.
BAY_STATUS_AVAILABLE = "available"
BAY_STATUS_HIGH = "high"  # 4/5 -> high-occupancy warning
BAY_STATUS_FULL = "full"  # 5/5 -> bay-full alert

# Alert target kinds accepted by upsert_alert().
ALERT_TARGETS = {
    "warehouse": ("Warehouse", "warehouse_id"),
    "conveyor": ("Conveyor", "conveyor_id"),
    "scanner": ("Scanner", "scanner_id"),
    "sorter": ("Sorter", "sorter_id"),
    "bay": ("DispatchBay", "bay_id"),
    "reject_lane": ("RejectLane", "lane_id"),
    "parcel": ("Parcel", "parcel_id"),
}


def bay_status(occupancy, capacity=BAY_CAPACITY):
    """Operating status of a dispatch bay for a given occupancy."""
    if occupancy is None:
        return BAY_STATUS_AVAILABLE
    if occupancy >= capacity:
        return BAY_STATUS_FULL
    if occupancy >= capacity - 1:
        return BAY_STATUS_HIGH
    return BAY_STATUS_AVAILABLE


def location_for_status(status, actual_bay=None):
    """Default location of a parcel in a given status."""
    if status == "created":
        return "ENTRY"
    if status == "scanned":
        return "SCANNER"
    if status == "at_sorter":
        return "SORTER"
    if status == "sorted":
        return f"BAY_{actual_bay}" if actual_bay in BAY_IDS else None
    if status == "rejected":
        return "REJECT_LANE"
    if status == "dispatched":
        return "DISPATCHED"
    return None
