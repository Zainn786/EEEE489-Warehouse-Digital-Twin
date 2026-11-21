# backend/graph – Neo4j digital twin layer (Member 2)

Ontology: [docs/graph/ontology.md](../../docs/graph/ontology.md) · Sample outputs: [docs/graph/samples/](../../docs/graph/samples/)

## Configuration

Environment variables (never commit real values – see `.env.example` at repo root):

| Variable | Default | Notes |
|---|---|---|
| `NEO4J_URI` | `bolt://localhost:7687` | `bolt://neo4j:7687` inside Docker Compose |
| `NEO4J_USER` | `neo4j` | |
| `NEO4J_PASSWORD` | – | required |
| `NEO4J_DATABASE` | `neo4j` | |

Neo4j service for `docker-compose.yml` (Member 1 maintains the file). Requires Neo4j **5.26+**:

```yaml
  neo4j:
    image: neo4j:5.26-community
    ports: ["7474:7474", "7687:7687"]
    environment:
      NEO4J_AUTH: neo4j/${NEO4J_PASSWORD}
    volumes: [neo4j_data:/data]
    healthcheck:
      test: ["CMD-SHELL", "cypher-shell -u neo4j -p $${NEO4J_PASSWORD} 'RETURN 1' || exit 1"]
      interval: 5s
      retries: 20
# volumes:
#   neo4j_data:
```

## Usage from the backend (Member 4)

```python
from backend.graph import init_graph, apply_event, get_state  # etc.

init_graph()                 # on FastAPI start-up (idempotent)
applied = apply_event(event) # per validated MQTT message; False = duplicate, ValueError = malformed
```

### Writers (`updates.py`)

| Function | Purpose |
|---|---|
| `apply_event(event) -> bool` | Store TelemetryEvent + update parcel/equipment/bay state in one transaction. Duplicate `event_id` → `False`, nothing changes. An unseen `run_id` automatically becomes the active run. |
| `start_new_run(run_id)` | Explicitly activate a run and reset live counters/occupancy (history kept). |
| `upsert_alert(alert_id, run_id, alert_type, severity, target_type, target_id, triggered_at=None, message=None)` | Create/update an active alert, linked `(Alert)-[:TRIGGERED_BY]->(target)`. `target_type`: warehouse, conveyor, scanner, sorter, bay, reject_lane, parcel. |
| `resolve_alert(alert_id, resolved_at=None) -> bool` | Mark resolved. |
| `write_metrics(run_id, metrics, calculated_at=None) -> bool` | Store KPI values on the Warehouse node (`kpi_*`). Accepts numbers, `None`, `{"value": ..}` and one nested level (e.g. per-bay). |

### Readers (`queries.py`) – all default to the active run

| Function | Returns |
|---|---|
| `get_state(run_id=None)` | `/api/state` shape: run_id, simulator_status, conveyor, scanner, sorter, bays, reject_lane, active_parcels, kpis |
| `get_parcels(run_id=None, status=None, limit=None)` | `{run_id, parcels}` with parcel_id, target_bay, actual_bay, status, current_stage, created_at, sorted_at, scan_ok, correctly_routed, completed_at, … |
| `get_telemetry_events(run_id=None, event_types=None, since=None, until=None, parcel_id=None, limit=1000)` | `{run_id, events}` – raw history for time-series |
| `get_alerts(run_id=None, active=None)` | `{run_id, alerts}` with alert_id, type, severity, active, triggered_at, resolved_at, target |
| `find_active_alert(run_id, alert_type, target_type=None, target_id=None)` | the active alert or `None` (avoid duplicate alerts) |
| `get_graph(run_id=None, node_types=None, include_events=False)` | `{run_id, nodes:[{id,type,label,properties}], edges:[{source,target,type}]}` |
| `get_active_run_id()`, `get_run_ids()`, `check_connection()` | helpers for `/api/health` etc. |

KPI formulas and alert rules are Member 4's; these functions only provide stored data.

### Event handling assumptions

- Parcel events need `parcel_id`; `target_bay` is read from `data`, `actual_bay` (or `bay_id`) for `parcel_sorted`.
- `bay_occupancy_changed` reads `data.bay_id` or falls back to a `source_id` such as `BAY_A`.
- `conveyor_status` reads `speed_mps`, `running`, `jammed`; `simulator_status` events read `data.status` (`stopped|running|paused`).
- Unknown event types are still stored as TelemetryEvents.

## Testing

Tests wipe their database, so they use a separate instance on port 7688:

```bash
docker run -d --name neo4j-test -p 7688:7687 -p 7475:7474 -e NEO4J_AUTH=neo4j/testpassword neo4j:5.26-community
# Browser for this test instance: http://localhost:7475 (connect to bolt://localhost:7688)
pip install -r backend/graph/requirements.txt pytest
python -m pytest tests/graph -v
```

Regenerate sample outputs (also leaves a demo graph for screenshots):

```bash
NEO4J_URI=bolt://localhost:7688 NEO4J_PASSWORD=testpassword python -m tests.graph.generate_samples
```

### Screenshot queries (Neo4j Browser)

```cypher
CALL db.schema.visualization();                                   // ontology meta-schema
MATCH (n) WHERE NOT n:TelemetryEvent RETURN n;                    // full live twin
MATCH (p:Parcel {parcel_id:'P001'})-[r]-(x) RETURN p, r, x;       // one parcel + relationships
MATCH (e:TelemetryEvent)-[:ABOUT]->(n) RETURN e, n LIMIT 25;      // telemetry history
```
