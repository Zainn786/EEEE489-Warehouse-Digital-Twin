"""Graph schema (constraints/indexes) and initial seed of the warehouse.

Both steps are idempotent, so the backend can call ``init_graph()`` on
every start-up. Run manually with:

    python -m backend.graph.init_graph
"""

from __future__ import annotations

from neo4j import Driver

from .connection import get_database, get_driver
from .ontology import (
    BAY_CAPACITY,
    BAY_IDS,
    CONVEYOR_ID,
    REJECT_LANE_ID,
    SCANNER_ID,
    SENSORS,
    SORTER_ID,
    WAREHOUSE_ID,
    WAREHOUSE_NAME,
)

CONSTRAINTS = (
    "CREATE CONSTRAINT warehouse_id IF NOT EXISTS FOR (n:Warehouse) REQUIRE n.warehouse_id IS UNIQUE",
    "CREATE CONSTRAINT conveyor_id IF NOT EXISTS FOR (n:Conveyor) REQUIRE n.conveyor_id IS UNIQUE",
    "CREATE CONSTRAINT scanner_id IF NOT EXISTS FOR (n:Scanner) REQUIRE n.scanner_id IS UNIQUE",
    "CREATE CONSTRAINT sensor_id IF NOT EXISTS FOR (n:Sensor) REQUIRE n.sensor_id IS UNIQUE",
    "CREATE CONSTRAINT sorter_id IF NOT EXISTS FOR (n:Sorter) REQUIRE n.sorter_id IS UNIQUE",
    "CREATE CONSTRAINT bay_id IF NOT EXISTS FOR (n:DispatchBay) REQUIRE n.bay_id IS UNIQUE",
    "CREATE CONSTRAINT lane_id IF NOT EXISTS FOR (n:RejectLane) REQUIRE n.lane_id IS UNIQUE",
    "CREATE CONSTRAINT alert_id IF NOT EXISTS FOR (n:Alert) REQUIRE n.alert_id IS UNIQUE",
    # Agreement 7.3: unique event IDs and unique (run_id, parcel_id).
    "CREATE CONSTRAINT event_id IF NOT EXISTS FOR (n:TelemetryEvent) REQUIRE n.event_id IS UNIQUE",
    "CREATE CONSTRAINT parcel_run_id IF NOT EXISTS FOR (n:Parcel) REQUIRE (n.run_id, n.parcel_id) IS UNIQUE",
)

INDEXES = (
    "CREATE INDEX event_run_time IF NOT EXISTS FOR (n:TelemetryEvent) ON (n.run_id, n.timestamp)",
    "CREATE INDEX event_type IF NOT EXISTS FOR (n:TelemetryEvent) ON (n.event_type)",
    "CREATE INDEX parcel_run_status IF NOT EXISTS FOR (n:Parcel) ON (n.run_id, n.status)",
    "CREATE INDEX alert_run IF NOT EXISTS FOR (n:Alert) ON (n.run_id, n.active)",
)

SEED_EQUIPMENT = """
MERGE (w:Warehouse {warehouse_id: $wid})
  ON CREATE SET w.name = $name, w.status = 'operational', w.simulator_status = 'stopped',
                w.run_id = null, w.run_ids = []
MERGE (c:Conveyor {conveyor_id: $cid})
  ON CREATE SET c.speed_mps = 0.0, c.running = false, c.jammed = false, c.status = 'stopped'
MERGE (sc:Scanner {scanner_id: $sid})
  ON CREATE SET sc.status = 'operational', sc.successful_scans = 0, sc.failed_scans = 0
MERGE (so:Sorter {sorter_id: $soid})
  ON CREATE SET so.status = 'operational', so.active_route = null
MERGE (rl:RejectLane {lane_id: $rlid})
  ON CREATE SET rl.rejected_count = 0
SET c.name = coalesce(c.name, $cid), sc.name = coalesce(sc.name, $sid),
    so.name = coalesce(so.name, $soid), rl.name = coalesce(rl.name, 'Reject Lane')
MERGE (w)-[:HAS_CONVEYOR]->(c)
MERGE (w)-[:HAS_SCANNER]->(sc)
MERGE (w)-[:HAS_SORTER]->(so)
MERGE (w)-[:HAS_REJECT_LANE]->(rl)
MERGE (c)-[:FEEDS]->(sc)
MERGE (sc)-[:FEEDS]->(so)
MERGE (so)-[:ROUTES_TO]->(rl)
"""

SEED_BAYS = """
MATCH (w:Warehouse {warehouse_id: $wid}), (so:Sorter {sorter_id: $soid})
UNWIND $bay_ids AS bid
MERGE (b:DispatchBay {bay_id: bid})
  ON CREATE SET b.capacity = $capacity, b.occupancy = 0, b.status = 'available'
SET b.name = coalesce(b.name, 'Bay ' + bid)
MERGE (w)-[:HAS_BAY]->(b)
MERGE (so)-[:ROUTES_TO]->(b)
"""

SEED_SENSORS = """
UNWIND $sensors AS s
MERGE (n:Sensor {sensor_id: s.sensor_id})
  ON CREATE SET n.sensor_type = s.sensor_type, n.latest_value = null, n.timestamp = null
SET n.name = coalesce(n.name, s.sensor_id)
WITH n, s
CALL (n, s) {
  MATCH (asset) WHERE s.label IN labels(asset) AND asset[s.key] = s.asset_id
  MERGE (asset)-[:MONITORED_BY]->(n)
}
"""


def create_constraints(driver: Driver | None = None) -> None:
    driver = driver or get_driver()
    with driver.session(database=get_database()) as session:
        for stmt in CONSTRAINTS + INDEXES:
            session.run(stmt).consume()


def seed_graph(driver: Driver | None = None) -> None:
    driver = driver or get_driver()
    sensors = [
        {"sensor_id": sid, "sensor_type": stype, "label": label, "key": key, "asset_id": asset_id}
        for sid, stype, label, key, asset_id in SENSORS
    ]
    params = {
        "wid": WAREHOUSE_ID,
        "name": WAREHOUSE_NAME,
        "cid": CONVEYOR_ID,
        "sid": SCANNER_ID,
        "soid": SORTER_ID,
        "rlid": REJECT_LANE_ID,
        "bay_ids": list(BAY_IDS),
        "capacity": BAY_CAPACITY,
        "sensors": sensors,
    }

    def _tx(tx):
        tx.run(SEED_EQUIPMENT, params).consume()
        tx.run(SEED_BAYS, params).consume()
        tx.run(SEED_SENSORS, params).consume()

    with driver.session(database=get_database()) as session:
        session.execute_write(_tx)


def init_graph(driver: Driver | None = None) -> None:
    """Create constraints, indexes and the fixed equipment nodes."""
    create_constraints(driver)
    seed_graph(driver)


if __name__ == "__main__":
    init_graph()
    print("Neo4j graph initialised")
