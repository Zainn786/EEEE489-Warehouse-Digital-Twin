"""Read-only graph queries used by the FastAPI endpoints (Member 4).

All functions return plain JSON-serialisable dicts/lists (datetimes as
UTC ISO 8601 strings). Every function defaults to the active run_id
stored on the Warehouse node; pass ``run_id`` to read an older run.
KPI formulas are intentionally NOT implemented here (Member 4 owns
analytics) - these functions only return the stored state and history.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable

from neo4j import Driver

from .connection import get_database, get_driver
from .ontology import ACTIVE_PARCEL_STATUSES, ALERT_TARGETS, ENTITY_KEYS, WAREHOUSE_ID


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _iso(value: Any) -> Any:
    if hasattr(value, "to_native"):
        value = value.to_native()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return value


def _clean(props: dict) -> dict:
    return {k: _iso(v) for k, v in props.items()}


def _read(driver: Driver | None, query: str, **params) -> list[dict]:
    driver = driver or get_driver()
    with driver.session(database=get_database()) as session:
        return session.execute_read(lambda tx: [r.data() for r in tx.run(query, **params)])


def _run(run_id: str | None, driver: Driver | None) -> str | None:
    return run_id if run_id is not None else get_active_run_id(driver)


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------
def get_active_run_id(driver: Driver | None = None) -> str | None:
    rows = _read(driver, "MATCH (w:Warehouse {warehouse_id: $wid}) RETURN w.run_id AS run_id", wid=WAREHOUSE_ID)
    return rows[0]["run_id"] if rows else None


def get_run_ids(driver: Driver | None = None) -> list[str]:
    rows = _read(driver, "MATCH (w:Warehouse {warehouse_id: $wid}) RETURN coalesce(w.run_ids, []) AS ids",
                 wid=WAREHOUSE_ID)
    return rows[0]["ids"] if rows else []


# ---------------------------------------------------------------------------
# Parcels
# ---------------------------------------------------------------------------
PARCEL_FIELDS = (
    "parcel_id", "target_bay", "actual_bay", "status", "current_stage", "created_at", "sorted_at",
    "scan_ok", "correctly_routed", "rejected_at", "completed_at", "dispatched_at", "reject_reason",
    "updated_at",
)


def _parcel_dict(props: dict) -> dict:
    props = _clean(props)
    return {k: props.get(k) for k in PARCEL_FIELDS}


def get_parcels(run_id: str | None = None, status: str | Iterable[str] | None = None,
                limit: int | None = None, driver: Driver | None = None) -> dict:
    """{"run_id", "parcels": [...]} ordered by creation time.

    Each parcel has the agreed fields (parcel_id, target_bay, actual_bay,
    status, current_stage, created_at, sorted_at) plus completion details.
    """
    run_id = _run(run_id, driver)
    statuses = [status] if isinstance(status, str) else (list(status) if status else None)
    rows = _read(
        driver,
        """
        MATCH (p:Parcel {run_id: $run_id})
        WHERE $statuses IS NULL OR p.status IN $statuses
        RETURN properties(p) AS p
        ORDER BY p.created_at, p.parcel_id
        """ + (" LIMIT $limit" if limit else ""),
        run_id=run_id, statuses=statuses, limit=limit,
    )
    return {"run_id": run_id, "parcels": [_parcel_dict(r["p"]) for r in rows]}


# ---------------------------------------------------------------------------
# Current state
# ---------------------------------------------------------------------------
def get_state(run_id: str | None = None, driver: Driver | None = None) -> dict:
    """Current twin state in the /api/state shape.

    Equipment state is live (it belongs to the active run); parcels are
    scoped to ``run_id``. Stored KPIs appear under "kpis".
    """
    rows = _read(
        driver,
        """
        MATCH (w:Warehouse {warehouse_id: $wid})
        OPTIONAL MATCH (w)-[:HAS_CONVEYOR]->(c:Conveyor)
        OPTIONAL MATCH (w)-[:HAS_SCANNER]->(sc:Scanner)
        OPTIONAL MATCH (w)-[:HAS_SORTER]->(so:Sorter)
        OPTIONAL MATCH (w)-[:HAS_REJECT_LANE]->(rl:RejectLane)
        OPTIONAL MATCH (w)-[:HAS_BAY]->(b:DispatchBay)
        WITH w, c, sc, so, rl, b ORDER BY b.bay_id
        RETURN properties(w) AS w, properties(c) AS c, properties(sc) AS sc,
               properties(so) AS so, properties(rl) AS rl, collect(properties(b)) AS bays
        """,
        wid=WAREHOUSE_ID,
    )
    if not rows:
        raise RuntimeError("Warehouse node missing - call init_graph() first")
    r = rows[0]
    w = _clean(r["w"])
    active_run = w.get("run_id")
    run_id = run_id if run_id is not None else active_run
    c, sc, so, rl = (_clean(r[k] or {}) for k in ("c", "sc", "so", "rl"))

    active = get_parcels(run_id, status=ACTIVE_PARCEL_STATUSES, driver=driver)["parcels"]
    return {
        "run_id": run_id,
        "active_run_id": active_run,
        "simulator_status": w.get("simulator_status"),
        "warehouse": {"warehouse_id": w.get("warehouse_id"), "name": w.get("name"), "status": w.get("status"),
                      "run_started_at": w.get("run_started_at")},
        "conveyor": {k: c.get(k) for k in ("conveyor_id", "speed_mps", "running", "jammed", "status",
                                           "jammed_since", "stopped_since", "last_updated")},
        "scanner": {k: sc.get(k) for k in ("scanner_id", "status", "successful_scans", "failed_scans")},
        "sorter": {k: so.get(k) for k in ("sorter_id", "status", "active_route")},
        "bays": [{k: _iso(b.get(k)) for k in ("bay_id", "capacity", "occupancy", "status")} for b in r["bays"]],
        "reject_lane": {k: rl.get(k) for k in ("lane_id", "rejected_count")},
        "active_parcels": active,
        "kpis": {k[4:]: v for k, v in w.items() if k.startswith("kpi_")},
    }


# ---------------------------------------------------------------------------
# History (TelemetryEvent nodes)
# ---------------------------------------------------------------------------
def get_telemetry_events(run_id: str | None = None, event_types: Iterable[str] | None = None,
                         since: str | None = None, until: str | None = None,
                         parcel_id: str | None = None, limit: int = 1000,
                         newest_first: bool = False, driver: Driver | None = None) -> dict:
    """Stored telemetry for a run, filtered by type / time window / parcel.

    ``since``/``until`` are ISO 8601 strings. Each event has the common
    fields and its parsed ``data`` object.
    """
    run_id = _run(run_id, driver)
    order = "DESC" if newest_first else "ASC"
    rows = _read(
        driver,
        f"""
        MATCH (e:TelemetryEvent {{run_id: $run_id}})
        WHERE ($types IS NULL OR e.event_type IN $types)
          AND ($since IS NULL OR e.timestamp >= datetime($since))
          AND ($until IS NULL OR e.timestamp <= datetime($until))
          AND ($pid IS NULL OR e.parcel_id = $pid)
        RETURN e.event_id AS event_id, e.run_id AS run_id, e.event_type AS event_type,
               e.timestamp AS timestamp, e.source_id AS source_id, e.parcel_id AS parcel_id,
               e.data_json AS data_json
        ORDER BY e.timestamp {order}, e.ingested_at {order}
        LIMIT $limit
        """,
        run_id=run_id, types=list(event_types) if event_types else None,
        since=since, until=until, pid=parcel_id, limit=limit,
    )
    events = []
    for r in rows:
        ev = _clean(r)
        ev["data"] = json.loads(ev.pop("data_json") or "{}")
        events.append(ev)
    return {"run_id": run_id, "events": events}


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------
def _alert_dict(a: dict, target_label: str | None, target_props: dict | None) -> dict:
    a = _clean(a)
    target = None
    if target_label:
        kind = next((k for k, (lbl, _) in ALERT_TARGETS.items() if lbl == target_label), target_label)
        target = {"type": kind, "id": (target_props or {}).get(ENTITY_KEYS.get(target_label, ""))}
    return {
        "alert_id": a.get("alert_id"), "run_id": a.get("run_id"), "type": a.get("type"),
        "severity": a.get("severity"), "active": a.get("active"), "message": a.get("message"),
        "triggered_at": a.get("triggered_at"), "resolved_at": a.get("resolved_at"), "target": target,
    }


def get_alerts(run_id: str | None = None, active: bool | None = None, driver: Driver | None = None) -> dict:
    """{"run_id", "alerts": [...]} newest first."""
    run_id = _run(run_id, driver)
    rows = _read(
        driver,
        """
        MATCH (a:Alert {run_id: $run_id})
        WHERE $active IS NULL OR a.active = $active
        OPTIONAL MATCH (a)-[:TRIGGERED_BY]->(t)
        RETURN properties(a) AS a, head(labels(t)) AS tl, properties(t) AS tp
        ORDER BY a.triggered_at DESC
        """,
        run_id=run_id, active=active,
    )
    return {"run_id": run_id, "alerts": [_alert_dict(r["a"], r["tl"], r["tp"]) for r in rows]}


def find_active_alert(run_id: str, alert_type: str, target_type: str | None = None,
                      target_id: str | None = None, driver: Driver | None = None) -> dict | None:
    """The active alert of a type (optionally for one target), or None.

    Lets the alert engine avoid raising the same alert twice.
    """
    for alert in get_alerts(run_id, active=True, driver=driver)["alerts"]:
        if alert["type"] != alert_type:
            continue
        if target_type is not None and (alert["target"] or {}).get("type") != target_type:
            continue
        if target_id is not None and (alert["target"] or {}).get("id") != target_id:
            continue
        return alert
    return None


# ---------------------------------------------------------------------------
# Graph for visualisation
# ---------------------------------------------------------------------------
STATIC_LABELS = ("Warehouse", "Conveyor", "Scanner", "Sensor", "Sorter", "DispatchBay", "RejectLane")


def _node_id(label: str, props: dict) -> str:
    key = ENTITY_KEYS.get(label)
    return f"{label}:{props.get(key)}"


def _node_label(label: str, props: dict) -> str:
    if label == "Warehouse":
        return props.get("name") or props.get("warehouse_id")
    if label == "DispatchBay":
        return f"Bay {props.get('bay_id')}"
    if label == "Alert":
        return str(props.get("type"))
    if label == "TelemetryEvent":
        return str(props.get("event_type"))
    return str(props.get(ENTITY_KEYS.get(label, ""), label))


def get_graph(run_id: str | None = None, node_types: Iterable[str] | None = None,
              include_events: bool = False, event_limit: int = 25,
              include_dispatched: bool = True, driver: Driver | None = None) -> dict:
    """{"run_id", "nodes": [{id, type, label, properties}], "edges": [{source, target, type}]}.

    Contains the equipment plus this run's parcels and alerts (and,
    optionally, the newest TelemetryEvents). ``node_types`` filters by
    entity type, e.g. ["Parcel", "DispatchBay"].
    """
    run_id = _run(run_id, driver)
    wanted = set(node_types) if node_types else None
    if wanted and "TelemetryEvent" in wanted:
        include_events = True

    rows = _read(
        driver,
        """
        CALL () {
          MATCH (n) WHERE any(l IN labels(n) WHERE l IN $static) RETURN n
          UNION
          MATCH (n:Parcel {run_id: $run_id})
          WHERE $include_dispatched OR n.status <> 'dispatched' RETURN n
          UNION
          MATCH (n:Alert {run_id: $run_id}) RETURN n
          UNION
          MATCH (n:TelemetryEvent {run_id: $run_id}) WHERE $include_events
          WITH n ORDER BY n.timestamp DESC LIMIT $event_limit RETURN n
        }
        RETURN elementId(n) AS eid, head(labels(n)) AS label, properties(n) AS props
        """,
        static=list(STATIC_LABELS), run_id=run_id, include_dispatched=include_dispatched,
        include_events=include_events, event_limit=event_limit,
    )

    nodes, id_map = [], {}
    for r in rows:
        label, props = r["label"], _clean(r["props"])
        if wanted and label not in wanted:
            continue
        if label == "TelemetryEvent" and props.get("data_json"):
            props["data"] = json.loads(props.pop("data_json"))
        nid = _node_id(label, props)
        id_map[r["eid"]] = nid
        nodes.append({"id": nid, "type": label, "label": _node_label(label, props), "properties": props})

    edges = []
    if id_map:
        rel_rows = _read(
            driver,
            """
            MATCH (a)-[r]->(b)
            WHERE elementId(a) IN $ids AND elementId(b) IN $ids
            RETURN elementId(a) AS s, elementId(b) AS t, type(r) AS type
            """,
            ids=list(id_map),
        )
        edges = [{"source": id_map[r["s"]], "target": id_map[r["t"]], "type": r["type"]} for r in rel_rows]

    return {"run_id": run_id, "nodes": nodes, "edges": edges}
