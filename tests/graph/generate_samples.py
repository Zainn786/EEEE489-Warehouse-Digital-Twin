"""Replay a scripted scenario into a Neo4j instance and dump sample outputs.

Produces docs/graph/samples/*.json so Members 3 and 4 can develop against
real query output, and leaves a populated graph for Neo4j Browser
screenshots. WIPES the target database.

    NEO4J_URI=bolt://localhost:7688 NEO4J_PASSWORD=testpassword \
        python -m tests.graph.generate_samples
"""

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.graph import (
    apply_event, create_driver, get_alerts, get_graph, get_parcels, get_state,
    get_telemetry_events, init_graph, upsert_alert, write_metrics,
)

OUT = Path(__file__).resolve().parents[2] / "docs" / "graph" / "samples"
T0 = datetime(2026, 11, 20, 10, 0, 0, tzinfo=timezone.utc)
RUN_ID = "b6a54c23-e18d-45a1-a5bd-98e60c61f102"


def ev(event_type, t, parcel_id=None, source_id="SIMULATOR", **data):
    return {
        "event_id": str(uuid.uuid4()), "run_id": RUN_ID, "event_type": event_type,
        "timestamp": (T0 + timedelta(seconds=t)).isoformat().replace("+00:00", "Z"),
        "source_id": source_id, "parcel_id": parcel_id, "data": data,
    }


def scenario():
    yield ev("simulator_status", 0, status="running")
    yield ev("conveyor_status", 0, source_id="CONVEYOR_01", speed_mps=0.5, running=True, jammed=False)
    # P001 -> A (normal)
    yield ev("parcel_created", 0, "P001", "CONVEYOR_01", target_bay="A", status="created", location="ENTRY")
    yield ev("parcel_scanned", 2, "P001", "SCANNER_01", target_bay="A", scan_ok=True, status="scanned")
    yield ev("parcel_created", 3, "P002", "CONVEYOR_01", target_bay="C", status="created", location="ENTRY")
    yield ev("parcel_at_sorter", 4, "P001", "SORTER_01", target_bay="A", status="at_sorter")
    # P002 barcode failure -> reject lane
    yield ev("parcel_scanned", 5, "P002", "SCANNER_01", target_bay="C", scan_ok=False, status="scanned")
    yield ev("parcel_sorted", 6, "P001", "SORTER_01", target_bay="A", actual_bay="A", status="sorted")
    yield ev("bay_occupancy_changed", 6, source_id="BAY_A", bay_id="A", occupancy=1, capacity=5)
    yield ev("parcel_created", 6, "P003", "CONVEYOR_01", target_bay="B", status="created", location="ENTRY")
    yield ev("parcel_at_sorter", 7, "P002", "SORTER_01", target_bay="C", status="at_sorter")
    yield ev("parcel_rejected", 9, "P002", "SORTER_01", target_bay="C", actual_bay=None,
             reason="scan_failed", status="rejected")
    yield ev("parcel_scanned", 8, "P003", "SCANNER_01", target_bay="B", scan_ok=True, status="scanned")
    # Conveyor jam with P003 at the scanner
    yield ev("conveyor_status", 9, source_id="CONVEYOR_01", speed_mps=0.0, running=True, jammed=True)
    yield ev("parcel_created", 9, "P004", "CONVEYOR_01", target_bay="A", status="created", location="ENTRY")


def main():
    driver = create_driver(os.getenv("NEO4J_URI", "bolt://localhost:7688"),
                           os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "testpassword"))
    with driver.session() as s:
        s.run("MATCH (n) DETACH DELETE n").consume()
    init_graph(driver)
    events = list(scenario())
    for e in events:
        apply_event(e, driver=driver)
    upsert_alert("ALERT-0001", RUN_ID, "barcode_failure", "warning", "parcel", "P002",
                 triggered_at="2026-11-20T10:00:05Z", message="Scan failed for P002", driver=driver)
    upsert_alert("ALERT-0002", RUN_ID, "conveyor_jam", "critical", "conveyor", "CONVEYOR_01",
                 triggered_at="2026-11-20T10:00:12Z", message="Conveyor stopped for 3 s with parcels waiting",
                 driver=driver)
    write_metrics(RUN_ID, {"throughput": 1, "routing_accuracy": 100.0, "average_sorting_time": 6.0,
                           "rejection_rate": 50.0, "bay_occupancy": {"A": 20.0, "B": 0.0, "C": 0.0}},
                  calculated_at="2026-11-20T10:00:12Z", driver=driver)

    OUT.mkdir(parents=True, exist_ok=True)
    samples = {
        "mqtt_events.json": events,
        "get_state.json": get_state(driver=driver),
        "get_parcels.json": get_parcels(driver=driver),
        "get_telemetry_events.json": get_telemetry_events(limit=5, driver=driver),
        "get_alerts.json": get_alerts(driver=driver),
        "get_graph.json": get_graph(driver=driver),
    }
    for name, data in samples.items():
        (OUT / name).write_text(json.dumps(data, indent=2) + "\n")
        print("wrote", OUT / name)
    driver.close()


if __name__ == "__main__":
    main()
