"""Fixtures for graph tests.

Tests WIPE the target database, so they use a dedicated test instance:
    NEO4J_TEST_URI       default bolt://localhost:7688
    NEO4J_TEST_USER      default neo4j
    NEO4J_TEST_PASSWORD  default testpassword

Start it with:
    docker run -d --name neo4j-test -p 7688:7687 -p 7475:7474 -e NEO4J_AUTH=neo4j/testpassword neo4j:5.26-community
"""

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from backend.graph import create_driver, init_graph

BASE_TIME = datetime(2026, 11, 20, 10, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="session")
def driver():
    drv = create_driver(
        os.getenv("NEO4J_TEST_URI", "bolt://localhost:7688"),
        os.getenv("NEO4J_TEST_USER", "neo4j"),
        os.getenv("NEO4J_TEST_PASSWORD", "testpassword"),
    )
    try:
        drv.verify_connectivity()
    except Exception as exc:  # pragma: no cover
        drv.close()
        pytest.skip(f"test Neo4j not reachable: {exc}")
    yield drv
    drv.close()


@pytest.fixture()
def graph(driver):
    """Empty database with the seeded warehouse."""
    with driver.session() as s:
        s.run("MATCH (n) DETACH DELETE n").consume()
    init_graph(driver)
    return driver


@pytest.fixture()
def run_id():
    return str(uuid.uuid4())


@pytest.fixture()
def make_event(run_id):
    """Build agreement-compliant events: make_event(type, parcel_id, data, t=seconds, ...)."""

    def _make(event_type, parcel_id=None, data=None, t=0.0, source_id=None, run=None, event_id=None):
        default_sources = {
            "parcel_created": "CONVEYOR_01", "parcel_scanned": "SCANNER_01",
            "parcel_at_sorter": "SORTER_01", "parcel_sorted": "SORTER_01",
            "parcel_rejected": "SORTER_01", "parcel_dispatched": "BAY_A",
            "conveyor_status": "CONVEYOR_01", "scanner_status": "SCANNER_01",
            "sorter_status": "SORTER_01", "bay_occupancy_changed": "BAY_A",
            "simulator_status": "SIMULATOR",
        }
        ts = (BASE_TIME + timedelta(seconds=t)).isoformat().replace("+00:00", "Z")
        return {
            "event_id": event_id or str(uuid.uuid4()),
            "run_id": run or run_id,
            "event_type": event_type,
            "timestamp": ts,
            "source_id": source_id or default_sources.get(event_type, "TEST"),
            "parcel_id": parcel_id,
            "data": data or {},
        }

    return _make


def scalar(driver, query, **params):
    with driver.session() as s:
        rec = s.run(query, **params).single()
        return rec[0] if rec else None
