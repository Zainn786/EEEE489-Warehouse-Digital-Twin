"""Create timestamped JSON-ready telemetry in Member 2's agreed envelope."""
from datetime import datetime, timezone
from uuid import uuid4


def make_event(run_id: str, event_type: str, source_id: str,
               parcel_id: str | None = None, data: dict | None = None) -> dict:
    return {
        "event_id": str(uuid4()),
        "run_id": run_id,
        "event_type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_id": source_id,
        "parcel_id": parcel_id,
        "data": dict(data or {}),
    }


# Backward compatibility with the first_parcel.py exercise.
create_event = make_event
