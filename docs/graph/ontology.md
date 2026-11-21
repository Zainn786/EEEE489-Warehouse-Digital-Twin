# Warehouse Digital Twin – Neo4j Ontology (meta-schema)

Implemented in `backend/graph/` (constants in `ontology.py`, schema in `init_graph.py`).
Live check in Neo4j Browser: `CALL db.schema.visualization()`.

```mermaid
flowchart LR
    W["<b>Warehouse</b><br/>warehouse_id, name, status<br/>run_id, run_ids, simulator_status<br/><i>kpi_* (calculated)</i>"]
    C["<b>Conveyor</b><br/>conveyor_id<br/>speed_mps, running, jammed, status<br/>jammed_since, stopped_since"]
    SC["<b>Scanner</b><br/>scanner_id, status<br/>successful_scans, failed_scans"]
    SO["<b>Sorter</b><br/>sorter_id, status, active_route"]
    B["<b>DispatchBay</b> ×3 (A,B,C)<br/>bay_id, capacity=5<br/>occupancy, status"]
    RL["<b>RejectLane</b><br/>lane_id, rejected_count"]
    SE["<b>Sensor</b> ×6<br/>sensor_id, sensor_type<br/>latest_value, timestamp"]
    P["<b>Parcel</b><br/>(run_id, parcel_id) unique<br/>target_bay, actual_bay, status<br/>current_stage, scan_ok, correctly_routed<br/>reject_reason, created_at, scanned_at<br/>sorted_at / rejected_at, dispatched_at"]
    A["<b>Alert</b><br/>alert_id, run_id, type, severity<br/>message, active, triggered_at, resolved_at"]
    T["<b>TelemetryEvent</b><br/>event_id unique, run_id<br/>event_type, timestamp, data_json"]

    W -- HAS_CONVEYOR --> C
    W -- HAS_SCANNER --> SC
    W -- HAS_SORTER --> SO
    W -- HAS_BAY --> B
    W -- HAS_REJECT_LANE --> RL
    C -- FEEDS --> SC
    SC -- FEEDS --> SO
    SO -- ROUTES_TO --> B
    SO -- ROUTES_TO --> RL
    C -- MONITORED_BY --> SE
    SC -- MONITORED_BY --> SE
    B -- MONITORED_BY --> SE
    SC -- "SCANS {scan_ok, scanned_at}" --> P
    P -- ASSIGNED_TO --> B
    P -. "CURRENTLY_AT (exactly one, none once dispatched)" .-> C & SC & SO & B & RL
    A -- TRIGGERED_BY --> W & C & SC & SO & B & RL & P
    T -- ABOUT --> W & P & C & SC & SO & B & RL
```

## Entities

| Entity | Kind | Static properties | Dynamic / calculated properties |
|---|---|---|---|
| Warehouse | asset (root) | warehouse_id, name | status, run_id (active run), run_ids, simulator_status, kpi_* |
| Conveyor | equipment | conveyor_id | speed_mps, running, jammed, status, jammed_since, stopped_since |
| Scanner | equipment | scanner_id | status, successful_scans, failed_scans (per run) |
| Sensor | sensing | sensor_id, sensor_type | latest_value, timestamp |
| Sorter | equipment | sorter_id | status, active_route |
| Parcel | item | parcel_id, run_id, target_bay, created_at | status, current_stage, actual_bay, scan_ok, correctly_routed, reject_reason, scanned_at, at_sorter_at, sorted_at, rejected_at, completed_at, dispatched_at |
| DispatchBay | destination | bay_id, capacity | occupancy, status (available / high / full) |
| RejectLane | destination | lane_id | rejected_count (per run) |
| Alert | event / state | alert_id, run_id, type, severity, triggered_at, message | active, resolved_at |
| TelemetryEvent | history | event_id, run_id, event_type, timestamp, source_id, data_json | – |

Sensors: `SENSOR_CONVEYOR_SPEED`, `SENSOR_PARCEL_PRESENCE` (Conveyor), `SENSOR_BARCODE` (Scanner),
`SENSOR_BAY_A/B/C` (DispatchBay occupancy).

## Parcel state machine

`created (ENTRY) → scanned (SCANNER) → at_sorter (SORTER) → sorted (BAY_x) | rejected (REJECT_LANE) → dispatched (DISPATCHED)`

Parcels only move forward; `sorted` and `rejected` share a rank, so a parcel can never be both.
Each transition replaces the single `CURRENTLY_AT` relationship.

## Relationship notes

- `TRIGGERED_BY` can point at any asset or a parcel (`target_type`: warehouse, conveyor, scanner, sorter, bay, reject_lane, parcel).
- `ABOUT` links an event to every node it concerns: parcel events to the parcel and the stage node, equipment events to that equipment, `simulator_status` to the Warehouse.
- The Sorter has no sensor; the 6 sensors monitor the Conveyor (speed, parcel presence), Scanner (barcode) and each bay (occupancy).

## Constraints and indexes

- Unique: `TelemetryEvent.event_id`, `(Parcel.run_id, Parcel.parcel_id)`, and the id of every equipment node and Alert.
- Indexes: `TelemetryEvent(run_id, timestamp)`, `TelemetryEvent(event_type)`, `Parcel(run_id, status)`, `Alert(run_id, active)`.
