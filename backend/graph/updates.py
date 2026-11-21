"""Graph update functions called by the backend (Member 4).

Main entry point: ``apply_event(event)`` takes one validated MQTT event
(agreement section 6.5) and, in a single transaction:

1. stores it as a TelemetryEvent node (skipped entirely if the event_id
   was already stored -> duplicate MQTT deliveries change nothing),
2. switches the Warehouse to a new run when an unseen run_id appears,
3. updates parcel / equipment / bay state and relationships,
4. links the TelemetryEvent to what it is ABOUT.

Other writers: ``start_new_run``, ``upsert_alert``, ``resolve_alert``
and ``write_metrics`` (so alerts and KPIs live in the twin).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from neo4j import Driver
from neo4j.exceptions import ConstraintError

from .connection import get_database, get_driver
from .ontology import (
    ALERT_TARGETS,
    BAY_IDS,
    CONVEYOR_ID,
    KNOWN_EVENT_TYPES,
    LOCATION_NODES,
    LOCATIONS,
    PARCEL_EVENT_STATUS,
    REJECT_LANE_ID,
    SCANNER_ID,
    SIMULATOR_STATUSES,
    SORTER_ID,
    STATUS_RANK,
    WAREHOUSE_ID,
    bay_status,
    location_for_status,
)

REQUIRED_FIELDS = ("event_id", "run_id", "event_type", "timestamp", "source_id", "parcel_id", "data")

# Scalar data fields copied onto the TelemetryEvent node for easy querying.
DENORMALISED_FIELDS = (
    "target_bay", "actual_bay", "scan_ok", "speed_mps", "running", "jammed",
    "occupancy", "capacity", "status", "bay_id", "location", "reason",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def normalize_event(event: dict) -> dict:
    """Check the common fields and return a clean copy. Raises ValueError."""
    if not isinstance(event, dict):
        raise ValueError("event must be a JSON object")
    missing = [f for f in REQUIRED_FIELDS if f not in event]
    if missing:
        raise ValueError(f"event is missing fields: {missing}")
    for f in ("event_id", "run_id", "event_type", "timestamp", "source_id"):
        if not isinstance(event[f], str) or not event[f]:
            raise ValueError(f"{f} must be a non-empty string")
    if event["parcel_id"] is not None and not isinstance(event["parcel_id"], str):
        raise ValueError("parcel_id must be a string or null")
    if not isinstance(event["data"], dict):
        raise ValueError("data must be an object")
    try:
        ts = _parse_ts(event["timestamp"])
    except ValueError as exc:
        raise ValueError(f"timestamp is not ISO 8601: {event['timestamp']}") from exc
    if ts.tzinfo is None:
        raise ValueError("timestamp must include a UTC offset (e.g. ...Z)")
    if event["event_type"] in PARCEL_EVENT_STATUS and not event["parcel_id"]:
        raise ValueError(f"{event['event_type']} requires parcel_id")

    clean = {f: event[f] for f in REQUIRED_FIELDS}
    clean["data"] = dict(event["data"])
    clean["data_json"] = json.dumps(event["data"], sort_keys=True, default=str)
    return clean


def _bay_from(data: dict, source_id: str | None = None) -> str | None:
    """Bay letter from data (bay_id / actual_bay) or a source_id like 'BAY_A'."""
    for key in ("bay_id", "actual_bay"):
        val = data.get(key)
        if val in BAY_IDS:
            return val
        if isinstance(val, str) and val.upper().startswith("BAY_") and val[4:].upper() in BAY_IDS:
            return val[4:].upper()
    if source_id and source_id.upper().startswith("BAY_") and source_id[4:].upper() in BAY_IDS:
        return source_id[4:].upper()
    return None


def _link_about(tx, event_id: str, label: str, key: str, value: Any, run_id: str | None = None) -> None:
    if value is None:
        return
    if label == "Parcel":
        tx.run(
            "MATCH (e:TelemetryEvent {event_id: $eid}) "
            "MATCH (p:Parcel {run_id: $run_id, parcel_id: $val}) "
            "MERGE (e)-[:ABOUT]->(p)",
            eid=event_id, run_id=run_id, val=value,
        ).consume()
    else:
        tx.run(
            f"MATCH (e:TelemetryEvent {{event_id: $eid}}) "
            f"MATCH (n:{label} {{{key}: $val}}) "
            f"MERGE (e)-[:ABOUT]->(n)",
            eid=event_id, val=value,
        ).consume()


# ---------------------------------------------------------------------------
# Run management
# ---------------------------------------------------------------------------
def _start_run_tx(tx, run_id: str, started_at: str) -> None:
    rec = tx.run(
        "MATCH (w:Warehouse {warehouse_id: $wid}) RETURN [k IN keys(w) WHERE k STARTS WITH 'kpi_'] AS ks",
        wid=WAREHOUSE_ID,
    ).single()
    if rec is None:
        raise RuntimeError("Warehouse node missing - call init_graph() first")
    cleared = {k: None for k in rec["ks"]}
    tx.run(
        """
        MATCH (w:Warehouse {warehouse_id: $wid})
        SET w += $cleared,
            w.run_id = $run_id,
            w.run_ids = CASE WHEN $run_id IN coalesce(w.run_ids, []) THEN w.run_ids
                             ELSE coalesce(w.run_ids, []) + $run_id END,
            w.run_started_at = datetime($ts)
        """,
        wid=WAREHOUSE_ID, run_id=run_id, ts=started_at, cleared=cleared,
    ).consume()
    # Reset live counters / occupancy; history (parcels, events, alerts) stays.
    tx.run("MATCH (n:Scanner) SET n.successful_scans = 0, n.failed_scans = 0").consume()
    tx.run("MATCH (n:RejectLane) SET n.rejected_count = 0").consume()
    tx.run("MATCH (n:DispatchBay) SET n.occupancy = 0, n.status = $st", st=bay_status(0)).consume()
    tx.run("MATCH (n:Sorter) SET n.active_route = null").consume()
    tx.run(
        "MATCH (n:Conveyor) SET n.jammed = false, n.jammed_since = null, n.stopped_since = null"
    ).consume()
    tx.run(
        "MATCH (n:Sensor) WHERE n.sensor_type IN ['bay_occupancy', 'parcel_presence'] "
        "SET n.latest_value = null, n.timestamp = null"
    ).consume()


def _resolve_run_tx(tx, run_id: str, ts: str) -> bool:
    """Return True if the event belongs to the active run (starting it if new)."""
    rec = tx.run(
        "MATCH (w:Warehouse {warehouse_id: $wid}) RETURN w.run_id AS run_id, coalesce(w.run_ids, []) AS run_ids",
        wid=WAREHOUSE_ID,
    ).single()
    if rec is None:
        raise RuntimeError("Warehouse node missing - call init_graph() first")
    if rec["run_id"] == run_id:
        return True
    if run_id not in rec["run_ids"]:
        _start_run_tx(tx, run_id, ts)
        return True
    return False  # late event from an older run: stored as history only


def start_new_run(run_id: str, started_at: str | None = None, driver: Driver | None = None) -> None:
    """Make ``run_id`` the active run and reset live counters (reset command)."""
    driver = driver or get_driver()
    with driver.session(database=get_database()) as session:
        session.execute_write(_start_run_tx, run_id, started_at or _now_iso())


# ---------------------------------------------------------------------------
# Parcel events
# ---------------------------------------------------------------------------
def _handle_parcel(tx, ev: dict, active: bool) -> None:
    new_status = PARCEL_EVENT_STATUS[ev["event_type"]]
    data, run_id, pid, ts = ev["data"], ev["run_id"], ev["parcel_id"], ev["timestamp"]
    target = data.get("target_bay") if data.get("target_bay") in BAY_IDS else None

    rec = tx.run(
        """
        MERGE (p:Parcel {run_id: $run_id, parcel_id: $pid})
        SET p.target_bay = coalesce(p.target_bay, $target), p.name = $pid
        RETURN p.status AS status, p.target_bay AS target_bay
        """,
        run_id=run_id, pid=pid, target=target,
    ).single()
    current, target = rec["status"], rec["target_bay"]

    if target is not None:
        tx.run(
            """
            MATCH (p:Parcel {run_id: $run_id, parcel_id: $pid})
            WHERE NOT (p)-[:ASSIGNED_TO]->(:DispatchBay)
            MATCH (b:DispatchBay {bay_id: $target})
            MERGE (p)-[:ASSIGNED_TO]->(b)
            """,
            run_id=run_id, pid=pid, target=target,
        ).consume()

    if new_status == "created":
        # Also covers a created event arriving after later stages.
        tx.run(
            "MATCH (p:Parcel {run_id: $run_id, parcel_id: $pid}) "
            "SET p.created_at = coalesce(p.created_at, datetime($ts))",
            run_id=run_id, pid=pid, ts=ts,
        ).consume()

    # Forward-only state machine: stale, repeated or contradictory events
    # (e.g. 'rejected' after 'sorted') do not change the parcel.
    if STATUS_RANK[new_status] <= STATUS_RANK.get(current, -1):
        return
    if new_status == "dispatched" and current == "rejected":
        return

    props: dict[str, Any] = {"status": new_status, "updated_at": ts}
    actual_bay = None
    if new_status == "scanned":
        props["scan_ok"] = bool(data.get("scan_ok", True))
        props["scanned_at"] = ts
    elif new_status == "at_sorter":
        props["at_sorter_at"] = ts
    elif new_status == "sorted":
        actual_bay = _bay_from(data) or target
        props.update(actual_bay=actual_bay, sorted_at=ts, completed_at=ts,
                     correctly_routed=(actual_bay == target))
    elif new_status == "rejected":
        props.update(actual_bay=None, rejected_at=ts, completed_at=ts,
                     reject_reason=data.get("reason"))
    elif new_status == "dispatched":
        props["dispatched_at"] = ts

    location = data.get("location") if data.get("location") in LOCATIONS else None
    if location is None or new_status == "dispatched":
        location = location_for_status(new_status, actual_bay)
    props["current_stage"] = location

    # *_at keys are fixed names from this function; stored as Neo4j datetimes.
    datetime_keys = [k for k in props if k.endswith("_at")]
    dt_set = ", ".join(f"p.{k} = datetime($ts)" for k in datetime_keys)
    tx.run(
        f"MATCH (p:Parcel {{run_id: $run_id, parcel_id: $pid}}) SET p += $plain, {dt_set}",
        run_id=run_id, pid=pid, ts=ts,
        plain={k: v for k, v in props.items() if k not in datetime_keys},
    ).consume()

    # Exactly one CURRENTLY_AT relationship (none once dispatched).
    tx.run(
        "MATCH (p:Parcel {run_id: $run_id, parcel_id: $pid})-[r:CURRENTLY_AT]->() DELETE r",
        run_id=run_id, pid=pid,
    ).consume()
    node = LOCATION_NODES.get(location) if location else None
    if node is not None:
        label, key, value = node
        tx.run(
            f"MATCH (p:Parcel {{run_id: $run_id, parcel_id: $pid}}) "
            f"MATCH (loc:{label} {{{key}: $val}}) "
            f"CREATE (p)-[:CURRENTLY_AT {{since: datetime($ts)}}]->(loc)",
            run_id=run_id, pid=pid, val=value, ts=ts,
        ).consume()

    if new_status == "scanned":
        tx.run(
            """
            MATCH (sc:Scanner {scanner_id: $sid}), (p:Parcel {run_id: $run_id, parcel_id: $pid})
            MERGE (sc)-[s:SCANS]->(p)
            SET s.scan_ok = $ok, s.scanned_at = datetime($ts)
            """,
            sid=SCANNER_ID, run_id=run_id, pid=pid, ok=props["scan_ok"], ts=ts,
        ).consume()

    if not active:
        return  # older run: keep live equipment counters untouched

    if new_status == "scanned":
        field = "successful_scans" if props["scan_ok"] else "failed_scans"
        tx.run(
            f"MATCH (sc:Scanner {{scanner_id: $sid}}) SET sc.{field} = coalesce(sc.{field}, 0) + 1",
            sid=SCANNER_ID,
        ).consume()
    elif new_status == "sorted":
        tx.run("MATCH (so:Sorter {sorter_id: $soid}) SET so.active_route = $route",
               soid=SORTER_ID, route=f"BAY_{actual_bay}" if actual_bay else None).consume()
    elif new_status == "rejected":
        tx.run("MATCH (rl:RejectLane {lane_id: $lid}) SET rl.rejected_count = coalesce(rl.rejected_count, 0) + 1",
               lid=REJECT_LANE_ID).consume()
        tx.run("MATCH (so:Sorter {sorter_id: $soid}) SET so.active_route = 'REJECT_LANE'",
               soid=SORTER_ID).consume()

    tx.run(
        "MATCH (s:Sensor {sensor_id: 'SENSOR_PARCEL_PRESENCE'}) "
        "SET s.latest_value = $val, s.timestamp = datetime($ts)",
        val=f"{pid}@{location}", ts=ts,
    ).consume()


def _parcel_about(tx, ev: dict) -> None:
    eid, et, data = ev["event_id"], ev["event_type"], ev["data"]
    _link_about(tx, eid, "Parcel", "parcel_id", ev["parcel_id"], ev["run_id"])
    if et == "parcel_created":
        _link_about(tx, eid, "Conveyor", "conveyor_id", CONVEYOR_ID)
    elif et == "parcel_scanned":
        _link_about(tx, eid, "Scanner", "scanner_id", SCANNER_ID)
    elif et in ("parcel_at_sorter", "parcel_sorted", "parcel_rejected"):
        _link_about(tx, eid, "Sorter", "sorter_id", SORTER_ID)
    if et in ("parcel_sorted", "parcel_dispatched"):
        _link_about(tx, eid, "DispatchBay", "bay_id", _bay_from(data))
    if et == "parcel_rejected":
        _link_about(tx, eid, "RejectLane", "lane_id", REJECT_LANE_ID)


# ---------------------------------------------------------------------------
# Equipment, bay and simulator events
# ---------------------------------------------------------------------------
def _handle_conveyor(tx, ev: dict, active: bool) -> None:
    _link_about(tx, ev["event_id"], "Conveyor", "conveyor_id", CONVEYOR_ID)
    if not active:
        return
    d = ev["data"]
    tx.run(
        """
        MATCH (c:Conveyor {conveyor_id: $cid})
        SET c.speed_mps = coalesce($speed, c.speed_mps),
            c.running = coalesce($running, c.running),
            c.jammed = coalesce($jammed, c.jammed),
            c.last_updated = datetime($ts)
        SET c.status = coalesce($status,
                CASE WHEN c.jammed THEN 'jammed' WHEN c.running THEN 'running' ELSE 'stopped' END),
            c.jammed_since = CASE WHEN c.jammed THEN coalesce(c.jammed_since, datetime($ts)) ELSE null END,
            c.stopped_since = CASE WHEN c.speed_mps = 0 OR NOT c.running
                                   THEN coalesce(c.stopped_since, datetime($ts)) ELSE null END
        WITH c
        MATCH (c)-[:MONITORED_BY]->(s:Sensor {sensor_type: 'conveyor_speed'})
        SET s.latest_value = c.speed_mps, s.timestamp = datetime($ts)
        """,
        cid=CONVEYOR_ID, ts=ev["timestamp"], status=d.get("status"),
        speed=d.get("speed_mps"), running=d.get("running"), jammed=d.get("jammed"),
    ).consume()


def _handle_scanner(tx, ev: dict, active: bool) -> None:
    _link_about(tx, ev["event_id"], "Scanner", "scanner_id", SCANNER_ID)
    if not active:
        return
    d = ev["data"]
    tx.run(
        """
        MATCH (sc:Scanner {scanner_id: $sid})
        SET sc.status = coalesce($status, sc.status), sc.last_updated = datetime($ts)
        WITH sc
        MATCH (sc)-[:MONITORED_BY]->(s:Sensor)
        SET s.latest_value = coalesce($status, s.latest_value), s.timestamp = datetime($ts)
        """,
        sid=SCANNER_ID, status=d.get("status"), ts=ev["timestamp"],
    ).consume()


def _handle_sorter(tx, ev: dict, active: bool) -> None:
    _link_about(tx, ev["event_id"], "Sorter", "sorter_id", SORTER_ID)
    if not active:
        return
    d = ev["data"]
    tx.run(
        """
        MATCH (so:Sorter {sorter_id: $soid})
        SET so.status = coalesce($status, so.status),
            so.active_route = coalesce($route, so.active_route),
            so.last_updated = datetime($ts)
        """,
        soid=SORTER_ID, status=d.get("status"),
        route=d.get("active_route", d.get("route")), ts=ev["timestamp"],
    ).consume()


def _handle_bay(tx, ev: dict, active: bool) -> None:
    d = ev["data"]
    bay = _bay_from(d, ev["source_id"])
    _link_about(tx, ev["event_id"], "DispatchBay", "bay_id", bay)
    if not active or bay is None:
        return
    rec = tx.run("MATCH (b:DispatchBay {bay_id: $bay}) RETURN b.occupancy AS o, b.capacity AS c", bay=bay).single()
    occupancy = d.get("occupancy", rec["o"])
    capacity = d.get("capacity", rec["c"])
    tx.run(
        """
        MATCH (b:DispatchBay {bay_id: $bay})
        SET b.occupancy = $occ, b.capacity = $cap, b.status = $status, b.last_updated = datetime($ts)
        WITH b
        MATCH (b)-[:MONITORED_BY]->(s:Sensor)
        SET s.latest_value = $occ, s.timestamp = datetime($ts)
        """,
        bay=bay, occ=occupancy, cap=capacity, status=bay_status(occupancy, capacity), ts=ev["timestamp"],
    ).consume()


def _handle_simulator(tx, ev: dict, active: bool) -> None:
    _link_about(tx, ev["event_id"], "Warehouse", "warehouse_id", WAREHOUSE_ID)
    if not active:
        return
    d = ev["data"]
    status = d.get("simulator_status", d.get("status"))
    if status in SIMULATOR_STATUSES:
        tx.run(
            "MATCH (w:Warehouse {warehouse_id: $wid}) SET w.simulator_status = $st, w.last_updated = datetime($ts)",
            wid=WAREHOUSE_ID, st=status, ts=ev["timestamp"],
        ).consume()


HANDLERS = {
    "conveyor_status": _handle_conveyor,
    "scanner_status": _handle_scanner,
    "sorter_status": _handle_sorter,
    "bay_occupancy_changed": _handle_bay,
    "simulator_status": _handle_simulator,
}


# ---------------------------------------------------------------------------
# apply_event
# ---------------------------------------------------------------------------
def _apply_event_tx(tx, ev: dict) -> bool:
    exists = tx.run(
        "MATCH (e:TelemetryEvent {event_id: $eid}) RETURN count(e) AS c", eid=ev["event_id"]
    ).single()["c"]
    if exists:
        return False

    active = _resolve_run_tx(tx, ev["run_id"], ev["timestamp"])

    extra = {k: ev["data"][k] for k in DENORMALISED_FIELDS
             if isinstance(ev["data"].get(k), (str, int, float, bool))}
    tx.run(
        """
        CREATE (e:TelemetryEvent {
            event_id: $event_id, run_id: $run_id, event_type: $event_type,
            timestamp: datetime($timestamp), source_id: $source_id,
            parcel_id: $parcel_id, data_json: $data_json, ingested_at: datetime(),
            name: $event_type})
        SET e += $extra
        """,
        extra=extra, **{k: ev[k] for k in
                        ("event_id", "run_id", "event_type", "timestamp", "source_id", "parcel_id", "data_json")},
    ).consume()

    et = ev["event_type"]
    if et in PARCEL_EVENT_STATUS:
        _handle_parcel(tx, ev, active)
        _parcel_about(tx, ev)
    elif et in HANDLERS:
        HANDLERS[et](tx, ev, active)
    return True


def apply_event(event: dict, driver: Driver | None = None) -> bool:
    """Persist one validated event and update the twin.

    Returns True if the event was applied, False if it was a duplicate.
    Raises ValueError for malformed events (nothing is written).
    """
    ev = normalize_event(event)
    driver = driver or get_driver()
    try:
        with driver.session(database=get_database()) as session:
            return session.execute_write(_apply_event_tx, ev)
    except ConstraintError:
        return False  # concurrent duplicate delivery lost the race


def is_known_event_type(event_type: str) -> bool:
    return event_type in KNOWN_EVENT_TYPES


# ---------------------------------------------------------------------------
# Alerts and calculated values (written by Member 4's analytics)
# ---------------------------------------------------------------------------
def upsert_alert(
    alert_id: str,
    run_id: str,
    alert_type: str,
    severity: str,
    target_type: str,
    target_id: str,
    triggered_at: str | None = None,
    message: str | None = None,
    driver: Driver | None = None,
) -> None:
    """Create (or update the severity/message of) an active alert.

    target_type is one of: warehouse, conveyor, scanner, sorter, bay,
    reject_lane, parcel. The alert is linked with
    (Alert)-[:TRIGGERED_BY]->(target).
    """
    if target_type not in ALERT_TARGETS:
        raise ValueError(f"unknown target_type {target_type!r}")
    label, key = ALERT_TARGETS[target_type]
    run_filter = ", run_id: $run_id" if label == "Parcel" else ""
    query = f"""
        MERGE (a:Alert {{alert_id: $alert_id}})
          ON CREATE SET a.run_id = $run_id, a.type = $type, a.name = $type, a.active = true,
                        a.triggered_at = datetime($ts), a.resolved_at = null
        SET a.severity = $severity, a.message = coalesce($message, a.message)
        WITH a
        OPTIONAL MATCH (t:{label} {{{key}: $target_id{run_filter}}})
        FOREACH (_ IN CASE WHEN t IS NULL THEN [] ELSE [1] END | MERGE (a)-[:TRIGGERED_BY]->(t))
    """
    driver = driver or get_driver()
    with driver.session(database=get_database()) as session:
        session.execute_write(lambda tx: tx.run(
            query, alert_id=alert_id, run_id=run_id, type=alert_type, severity=severity,
            message=message, target_id=target_id, ts=triggered_at or _now_iso(),
        ).consume())


def resolve_alert(alert_id: str, resolved_at: str | None = None, driver: Driver | None = None) -> bool:
    """Mark an alert resolved. Returns False if it was not active."""
    driver = driver or get_driver()

    def _tx(tx):
        rec = tx.run(
            "MATCH (a:Alert {alert_id: $aid}) WHERE a.active "
            "SET a.active = false, a.resolved_at = datetime($ts) RETURN count(a) AS c",
            aid=alert_id, ts=resolved_at or _now_iso(),
        ).single()
        return rec["c"] > 0

    with driver.session(database=get_database()) as session:
        return session.execute_write(_tx)


def write_metrics(run_id: str, metrics: dict, calculated_at: str | None = None,
                  driver: Driver | None = None) -> bool:
    """Store calculated KPIs on the Warehouse node as kpi_* properties.

    ``metrics`` maps name -> number | None | {"value": ...} | {sub: ...},
    e.g. {"throughput": {"value": 4, "unit": "parcels/min"},
          "bay_occupancy": {"A": {"value": 40.0}, "B": 0, "C": None}}.
    Only written for the active run. Returns False otherwise.
    """
    flat: dict[str, Any] = {}

    def _value(v):
        return v.get("value") if isinstance(v, dict) and "value" in v else v

    for name, raw in metrics.items():
        val = _value(raw)
        if isinstance(val, dict):
            for sub, subraw in val.items():
                flat[f"kpi_{name}_{sub}"] = _value(subraw)
        else:
            flat[f"kpi_{name}"] = val
    flat = {k: v for k, v in flat.items() if v is None or isinstance(v, (int, float, str, bool))}

    driver = driver or get_driver()

    def _tx(tx):
        rec = tx.run(
            "MATCH (w:Warehouse {warehouse_id: $wid}) WHERE w.run_id = $run_id "
            "SET w += $flat, w.kpi_calculated_at = datetime($ts) RETURN count(w) AS c",
            wid=WAREHOUSE_ID, run_id=run_id, flat=flat, ts=calculated_at or _now_iso(),
        ).single()
        return rec["c"] > 0

    with driver.session(database=get_database()) as session:
        return session.execute_write(_tx)
