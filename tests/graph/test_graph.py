"""Graph functionality tests (Member 2).

Each test maps to an item of the team agreement (sections 7, 10, 12).
"""

import json

import pytest

from backend.graph import (
    apply_event,
    find_active_alert,
    get_alerts,
    get_graph,
    get_parcels,
    get_state,
    get_telemetry_events,
    init_graph,
    resolve_alert,
    start_new_run,
    upsert_alert,
    write_metrics,
)

from .conftest import scalar


def locations(driver, run_id, pid):
    with driver.session() as s:
        return [r["loc"] for r in s.run(
            "MATCH (p:Parcel {run_id: $r, parcel_id: $p})-[:CURRENTLY_AT]->(n) "
            "RETURN coalesce(n.bay_id, n.conveyor_id, n.scanner_id, n.sorter_id, n.lane_id) AS loc",
            r=run_id, p=pid)]


def parcel(driver, run_id, pid):
    return next(p for p in get_parcels(run_id, driver=driver)["parcels"] if p["parcel_id"] == pid)


def run_happy_path(driver, make_event, pid="P001", bay="A", t0=0, actual=None):
    events = [
        make_event("parcel_created", pid, {"target_bay": bay, "status": "created"}, t=t0),
        make_event("parcel_scanned", pid, {"target_bay": bay, "scan_ok": True, "status": "scanned"}, t=t0 + 2),
        make_event("parcel_at_sorter", pid, {"target_bay": bay, "status": "at_sorter"}, t=t0 + 4),
        make_event("parcel_sorted", pid, {"target_bay": bay, "actual_bay": actual or bay, "status": "sorted"},
                   t=t0 + 6),
    ]
    for e in events:
        assert apply_event(e, driver=driver) is True
    return events


# --- Initialisation --------------------------------------------------------
def test_init_is_idempotent_and_creates_ontology(graph):
    init_graph(graph)  # second call must not duplicate anything
    counts = {label: scalar(graph, f"MATCH (n:{label}) RETURN count(n)")
              for label in ("Warehouse", "Conveyor", "Scanner", "Sorter", "DispatchBay", "RejectLane", "Sensor")}
    assert counts == {"Warehouse": 1, "Conveyor": 1, "Scanner": 1, "Sorter": 1,
                      "DispatchBay": 3, "RejectLane": 1, "Sensor": 6}
    assert scalar(graph, "MATCH (:Warehouse)-[:HAS_BAY]->(b) RETURN count(b)") == 3
    assert scalar(graph, "MATCH (:Sorter)-[:ROUTES_TO]->(x) RETURN count(x)") == 4
    assert scalar(graph, "MATCH (:Conveyor)-[:MONITORED_BY]->(s:Sensor) RETURN count(s)") == 2
    assert scalar(graph, "MATCH (n) WHERE n.name IS NULL RETURN count(n)") == 0  # display captions
    names = {r["name"] for r in graph.session().run("SHOW CONSTRAINTS YIELD name RETURN name")}
    assert {"event_id", "parcel_run_id"} <= names


# --- 7.2 Required graph demonstration / Normal sorting --------------------
def test_parcel_lifecycle_updates_properties_and_relationships(graph, make_event, run_id):
    created = make_event("parcel_created", "P001", {"target_bay": "A", "status": "created"})
    apply_event(created, driver=graph)
    p = parcel(graph, run_id, "P001")
    assert p["status"] == "created" and p["current_stage"] == "ENTRY"
    assert locations(graph, run_id, "P001") == ["CONVEYOR_01"]
    assert scalar(graph, "MATCH (:Parcel {parcel_id:'P001'})-[:ASSIGNED_TO]->(b) RETURN b.bay_id") == "A"

    apply_event(make_event("parcel_scanned", "P001", {"target_bay": "A", "scan_ok": True}, t=2), driver=graph)
    assert locations(graph, run_id, "P001") == ["SCANNER_01"]
    apply_event(make_event("parcel_at_sorter", "P001", {"target_bay": "A"}, t=4), driver=graph)
    assert locations(graph, run_id, "P001") == ["SORTER_01"]
    apply_event(make_event("parcel_sorted", "P001", {"target_bay": "A", "actual_bay": "A"}, t=6), driver=graph)

    p = parcel(graph, run_id, "P001")
    assert p["status"] == "sorted" and p["actual_bay"] == "A" and p["current_stage"] == "BAY_A"
    assert p["correctly_routed"] is True
    assert p["created_at"] == "2026-11-20T10:00:00Z" and p["sorted_at"] == "2026-11-20T10:00:06Z"
    assert locations(graph, run_id, "P001") == ["A"]
    state = get_state(driver=graph)
    assert state["scanner"]["successful_scans"] == 1
    assert state["active_parcels"] == []
    assert scalar(graph, "MATCH (:Scanner)-[s:SCANS]->(:Parcel {parcel_id:'P001'}) RETURN s.scan_ok") is True


def test_dispatch_removes_location_keeps_sorted_history(graph, make_event, run_id):
    run_happy_path(graph, make_event)
    apply_event(make_event("parcel_dispatched", "P001", {"bay_id": "A", "status": "dispatched"}, t=21),
                driver=graph)
    p = parcel(graph, run_id, "P001")
    assert p["status"] == "dispatched" and p["current_stage"] == "DISPATCHED"
    assert p["sorted_at"] == "2026-11-20T10:00:06Z" and p["actual_bay"] == "A"
    assert locations(graph, run_id, "P001") == []


# --- Multiple active parcels ---------------------------------------------
def test_three_active_parcels_have_one_location_each(graph, make_event, run_id):
    apply_event(make_event("parcel_created", "P001", {"target_bay": "A"}, t=0), driver=graph)
    apply_event(make_event("parcel_scanned", "P001", {"target_bay": "A", "scan_ok": True}, t=2), driver=graph)
    apply_event(make_event("parcel_created", "P002", {"target_bay": "B"}, t=3), driver=graph)
    apply_event(make_event("parcel_at_sorter", "P001", {"target_bay": "A"}, t=4), driver=graph)
    apply_event(make_event("parcel_scanned", "P002", {"target_bay": "B", "scan_ok": True}, t=5), driver=graph)
    apply_event(make_event("parcel_created", "P003", {"target_bay": "C"}, t=6), driver=graph)

    active = get_state(driver=graph)["active_parcels"]
    assert {p["parcel_id"]: p["current_stage"] for p in active} == {
        "P001": "SORTER", "P002": "SCANNER", "P003": "ENTRY"}
    assert scalar(graph, "MATCH (p:Parcel) WHERE count{(p)-[:CURRENTLY_AT]->()} <> 1 RETURN count(p)") == 0


# --- Duplicate / out-of-order / invalid messages --------------------------
def test_duplicate_event_is_ignored(graph, make_event, run_id):
    events = run_happy_path(graph, make_event)
    for e in events:
        assert apply_event(e, driver=graph) is False
    assert scalar(graph, "MATCH (e:TelemetryEvent) RETURN count(e)") == 4
    assert get_state(driver=graph)["scanner"]["successful_scans"] == 1
    assert scalar(graph, "MATCH (p:Parcel) RETURN count(p)") == 1


def test_stale_event_does_not_move_parcel_backwards(graph, make_event, run_id):
    run_happy_path(graph, make_event)
    late = make_event("parcel_at_sorter", "P001", {"target_bay": "A"}, t=4.5)
    assert apply_event(late, driver=graph) is True  # stored as history
    assert parcel(graph, run_id, "P001")["status"] == "sorted"
    assert locations(graph, run_id, "P001") == ["A"]


def test_parcel_never_both_sorted_and_rejected(graph, make_event, run_id):
    run_happy_path(graph, make_event)
    apply_event(make_event("parcel_rejected", "P001", {"target_bay": "A", "reason": "x"}, t=7), driver=graph)
    assert parcel(graph, run_id, "P001")["status"] == "sorted"
    assert get_state(driver=graph)["reject_lane"]["rejected_count"] == 0


def test_invalid_event_raises_and_writes_nothing(graph, make_event):
    bad = [
        {"event_type": "parcel_created"},
        {**make_event("parcel_created", "P001"), "timestamp": "not-a-time"},
        {**make_event("parcel_created", "P001"), "data": "oops"},
        make_event("parcel_created", None),
    ]
    for e in bad:
        with pytest.raises(ValueError):
            apply_event(e, driver=graph)
    assert scalar(graph, "MATCH (e:TelemetryEvent) RETURN count(e)") == 0
    assert scalar(graph, "MATCH (p:Parcel) RETURN count(p)") == 0


# --- Fault scenarios -------------------------------------------------------
def test_barcode_failure_routes_to_reject_lane(graph, make_event, run_id):
    apply_event(make_event("parcel_created", "P001", {"target_bay": "B"}), driver=graph)
    apply_event(make_event("parcel_scanned", "P001", {"target_bay": "B", "scan_ok": False}, t=2), driver=graph)
    apply_event(make_event("parcel_at_sorter", "P001", {"target_bay": "B"}, t=4), driver=graph)
    apply_event(make_event("parcel_rejected", "P001", {"target_bay": "B", "actual_bay": None,
                                                      "reason": "scan_failed"}, t=6), driver=graph)
    p = parcel(graph, run_id, "P001")
    assert p["status"] == "rejected" and p["actual_bay"] is None and p["current_stage"] == "REJECT_LANE"
    assert p["scan_ok"] is False and p["reject_reason"] == "scan_failed"
    assert locations(graph, run_id, "P001") == ["REJECT_LANE_01"]
    state = get_state(driver=graph)
    assert state["scanner"]["failed_scans"] == 1
    assert state["reject_lane"]["rejected_count"] == 1


def test_conveyor_jam_and_recovery(graph, make_event):
    apply_event(make_event("conveyor_status", data={"speed_mps": 0.5, "running": True, "jammed": False}),
                driver=graph)
    apply_event(make_event("conveyor_status", data={"speed_mps": 0.0, "running": True, "jammed": True}, t=2),
                driver=graph)
    c = get_state(driver=graph)["conveyor"]
    assert c["jammed"] is True and c["speed_mps"] == 0.0 and c["status"] == "jammed"
    assert c["jammed_since"] == "2026-11-20T10:00:02Z" and c["stopped_since"] == "2026-11-20T10:00:02Z"
    assert scalar(graph, "MATCH (s:Sensor {sensor_type:'conveyor_speed'}) RETURN s.latest_value") == 0.0

    apply_event(make_event("conveyor_status", data={"speed_mps": 0.5, "running": True, "jammed": False}, t=9),
                driver=graph)
    c = get_state(driver=graph)["conveyor"]
    assert c["jammed"] is False and c["jammed_since"] is None and c["status"] == "running"


def test_bay_occupancy_and_full_status(graph, make_event):
    for occ, expected in ((3, "available"), (4, "high"), (5, "full"), (2, "available")):
        apply_event(make_event("bay_occupancy_changed", data={"bay_id": "A", "occupancy": occ, "capacity": 5},
                               t=occ), driver=graph)
        bay_a = next(b for b in get_state(driver=graph)["bays"] if b["bay_id"] == "A")
        assert (bay_a["occupancy"], bay_a["status"]) == (occ, expected)
    # bay id taken from source_id when data has none
    apply_event(make_event("bay_occupancy_changed", data={"occupancy": 1}, source_id="BAY_C", t=10), driver=graph)
    assert next(b for b in get_state(driver=graph)["bays"] if b["bay_id"] == "C")["occupancy"] == 1


def test_misrouted_parcel_is_flagged(graph, make_event, run_id):
    run_happy_path(graph, make_event, pid="P001", bay="A", actual="B")
    p = parcel(graph, run_id, "P001")
    assert p["target_bay"] == "A" and p["actual_bay"] == "B" and p["correctly_routed"] is False
    assert locations(graph, run_id, "P001") == ["B"]
    assert scalar(graph, "MATCH (:Parcel {parcel_id:'P001'})-[:ASSIGNED_TO]->(b) RETURN b.bay_id") == "A"


# --- Reset / runs ----------------------------------------------------------
def test_new_run_resets_live_state_and_keeps_history(graph, make_event, run_id):
    run_happy_path(graph, make_event)
    apply_event(make_event("bay_occupancy_changed", data={"bay_id": "A", "occupancy": 1}, t=7), driver=graph)
    write_metrics(run_id, {"throughput": 1}, driver=graph)

    new_run = "11111111-1111-1111-1111-111111111111"
    apply_event(make_event("parcel_created", "P001", {"target_bay": "C"}, t=30, run=new_run), driver=graph)

    state = get_state(driver=graph)
    assert state["run_id"] == new_run
    assert state["scanner"]["successful_scans"] == 0
    assert all(b["occupancy"] == 0 for b in state["bays"])
    assert state["kpis"] == {}
    assert scalar(graph, "MATCH (p:Parcel {parcel_id: 'P001'}) RETURN count(p)") == 2
    assert parcel(graph, run_id, "P001")["status"] == "sorted"  # old run untouched
    assert len(get_telemetry_events(run_id, driver=graph)["events"]) == 5

    # A late event from the old run is stored but does not touch live state.
    apply_event(make_event("bay_occupancy_changed", data={"bay_id": "B", "occupancy": 4}, t=31), driver=graph)
    assert all(b["occupancy"] == 0 for b in get_state(driver=graph)["bays"])
    assert get_state(driver=graph)["run_id"] == new_run


def test_explicit_start_new_run(graph, run_id):
    start_new_run(run_id, driver=graph)
    assert get_state(driver=graph)["run_id"] == run_id


def test_simulator_status(graph, make_event):
    apply_event(make_event("simulator_status", data={"status": "running"}), driver=graph)
    assert get_state(driver=graph)["simulator_status"] == "running"
    apply_event(make_event("simulator_status", data={"status": "paused"}, t=1), driver=graph)
    assert get_state(driver=graph)["simulator_status"] == "paused"


# --- Telemetry history -----------------------------------------------------
def test_telemetry_events_stored_with_data_json(graph, make_event, run_id):
    run_happy_path(graph, make_event)
    stored = scalar(graph, "MATCH (e:TelemetryEvent {event_type:'parcel_scanned'}) RETURN e.data_json")
    assert json.loads(stored)["scan_ok"] is True
    assert scalar(graph, "MATCH (e:TelemetryEvent {event_type:'parcel_sorted'})-[:ABOUT]->(n) "
                         "RETURN count(n)") == 3  # parcel, sorter, bay

    hist = get_telemetry_events(event_types=["parcel_scanned", "parcel_sorted"], driver=graph)
    assert [e["event_type"] for e in hist["events"]] == ["parcel_scanned", "parcel_sorted"]
    assert hist["events"][0]["data"]["scan_ok"] is True
    window = get_telemetry_events(since="2026-11-20T10:00:03Z", until="2026-11-20T10:00:05Z", driver=graph)
    assert [e["event_type"] for e in window["events"]] == ["parcel_at_sorter"]


# --- Alerts and KPIs in the twin ------------------------------------------
def test_alert_lifecycle(graph, make_event, run_id):
    start_new_run(run_id, driver=graph)
    upsert_alert("AL-1", run_id, "conveyor_jam", "critical", "conveyor", "CONVEYOR_01",
                 triggered_at="2026-11-20T10:00:05Z", message="Conveyor stopped", driver=graph)
    upsert_alert("AL-1", run_id, "conveyor_jam", "critical", "conveyor", "CONVEYOR_01", driver=graph)
    assert scalar(graph, "MATCH (a:Alert) RETURN count(a)") == 1
    assert scalar(graph, "MATCH (:Alert)-[:TRIGGERED_BY]->(c:Conveyor) RETURN c.conveyor_id") == "CONVEYOR_01"

    found = find_active_alert(run_id, "conveyor_jam", "conveyor", "CONVEYOR_01", driver=graph)
    assert found["alert_id"] == "AL-1" and found["triggered_at"] == "2026-11-20T10:00:05Z"

    assert resolve_alert("AL-1", resolved_at="2026-11-20T10:00:09Z", driver=graph) is True
    assert resolve_alert("AL-1", driver=graph) is False
    alert = get_alerts(driver=graph)["alerts"][0]
    assert alert["active"] is False and alert["resolved_at"] == "2026-11-20T10:00:09Z"
    assert find_active_alert(run_id, "conveyor_jam", driver=graph) is None


def test_alert_on_bay_and_parcel(graph, make_event, run_id):
    apply_event(make_event("parcel_scanned", "P009", {"target_bay": "A", "scan_ok": False}), driver=graph)
    upsert_alert("AL-2", run_id, "barcode_failure", "warning", "parcel", "P009", driver=graph)
    upsert_alert("AL-3", run_id, "bay_full", "critical", "bay", "A", driver=graph)
    targets = {a["alert_id"]: a["target"] for a in get_alerts(driver=graph)["alerts"]}
    assert targets == {"AL-2": {"type": "parcel", "id": "P009"}, "AL-3": {"type": "bay", "id": "A"}}


def test_write_metrics_reflected_in_twin(graph, run_id):
    start_new_run(run_id, driver=graph)
    ok = write_metrics(run_id, {
        "throughput": {"value": 4, "unit": "parcels/min"},
        "routing_accuracy": None,
        "bay_occupancy": {"A": {"value": 40.0, "unit": "%"}, "B": 0, "C": 20.0},
    }, driver=graph)
    assert ok
    kpis = get_state(driver=graph)["kpis"]
    assert kpis["throughput"] == 4 and kpis["bay_occupancy_A"] == 40.0 and "routing_accuracy" not in kpis
    assert write_metrics("some-old-run", {"throughput": 9}, driver=graph) is False


# --- Graph API shape -------------------------------------------------------
def test_get_graph_contract(graph, make_event, run_id):
    run_happy_path(graph, make_event)
    g = get_graph(driver=graph)
    ids = {n["id"] for n in g["nodes"]}
    assert all(set(n) == {"id", "type", "label", "properties"} for n in g["nodes"])
    assert all(set(e) == {"source", "target", "type"} for e in g["edges"])
    assert all(e["source"] in ids and e["target"] in ids for e in g["edges"])
    assert "Parcel:P001" in ids
    assert {"source": "Parcel:P001", "target": "DispatchBay:A", "type": "CURRENTLY_AT"} in g["edges"]
    assert {"source": "Parcel:P001", "target": "DispatchBay:A", "type": "ASSIGNED_TO"} in g["edges"]

    only = get_graph(node_types=["Parcel", "DispatchBay"], driver=graph)
    assert {n["type"] for n in only["nodes"]} == {"Parcel", "DispatchBay"}

    with_events = get_graph(include_events=True, event_limit=2, driver=graph)
    assert sum(n["type"] == "TelemetryEvent" for n in with_events["nodes"]) == 2
