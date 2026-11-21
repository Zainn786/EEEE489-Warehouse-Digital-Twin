"""Neo4j digital twin graph layer (Member 2).

Public API used by the backend:

    from backend.graph import (
        init_graph, apply_event, start_new_run,
        upsert_alert, resolve_alert, write_metrics,
        get_state, get_parcels, get_telemetry_events, get_graph,
        get_alerts, find_active_alert, get_active_run_id, get_run_ids,
        check_connection, close_driver,
    )
"""

from .connection import check_connection, close_driver, create_driver, get_driver, set_driver
from .init_graph import init_graph
from .queries import (
    find_active_alert,
    get_active_run_id,
    get_alerts,
    get_graph,
    get_parcels,
    get_run_ids,
    get_state,
    get_telemetry_events,
)
from .updates import (
    apply_event,
    is_known_event_type,
    normalize_event,
    resolve_alert,
    start_new_run,
    upsert_alert,
    write_metrics,
)

__all__ = [
    "apply_event", "check_connection", "close_driver", "create_driver", "find_active_alert",
    "get_active_run_id", "get_alerts", "get_driver", "get_graph", "get_parcels", "get_run_ids",
    "get_state", "get_telemetry_events", "init_graph", "is_known_event_type", "normalize_event",
    "resolve_alert", "set_driver", "start_new_run", "upsert_alert", "write_metrics",
]
