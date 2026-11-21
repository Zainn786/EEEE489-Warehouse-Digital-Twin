"""Neo4j driver management.

Credentials come from environment variables only (never committed):
    NEO4J_URI       default bolt://localhost:7687 (use bolt://neo4j:7687 inside Docker Compose)
    NEO4J_USER      default neo4j
    NEO4J_PASSWORD  required
    NEO4J_DATABASE  default neo4j
"""

from __future__ import annotations

import os
import threading

from neo4j import Driver, GraphDatabase

_driver: Driver | None = None
_lock = threading.Lock()


def get_database() -> str:
    return os.getenv("NEO4J_DATABASE", "neo4j")


def create_driver(uri: str | None = None, user: str | None = None, password: str | None = None) -> Driver:
    """Create a new driver (callers own it and must close it)."""
    uri = uri or os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = user or os.getenv("NEO4J_USER", "neo4j")
    password = password or os.getenv("NEO4J_PASSWORD")
    if not password:
        raise RuntimeError("NEO4J_PASSWORD environment variable is not set")
    return GraphDatabase.driver(uri, auth=(user, password))


def get_driver() -> Driver:
    """Shared process-wide driver, created lazily from environment variables."""
    global _driver
    with _lock:
        if _driver is None:
            _driver = create_driver()
        return _driver


def set_driver(driver: Driver | None) -> None:
    """Override the shared driver (used by tests)."""
    global _driver
    with _lock:
        _driver = driver


def close_driver() -> None:
    global _driver
    with _lock:
        if _driver is not None:
            _driver.close()
            _driver = None


def check_connection(driver: Driver | None = None) -> bool:
    """True when Neo4j is reachable. Used by GET /api/health."""
    try:
        (driver or get_driver()).verify_connectivity()
        return True
    except Exception:
        return False
