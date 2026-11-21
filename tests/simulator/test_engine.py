"""Fast, deterministic unit tests; never need Docker or a live broker."""
import json
from uuid import UUID

import pytest

from simulator.config import Settings
from simulator.engine import WarehouseSimulator


def sim(**kwargs):
    messages = []
    s = Settings(**kwargs)
    engine = WarehouseSimulator(messages.append, settings=s)
    return engine, messages


def events(messages, kind):
    return [e for e in messages if e["event_type"] == kind]


def test_exact_envelope_and_identifiers():
    engine, messages = sim()
    engine.start()
    engine.step(0.2)
    for e in messages:
        assert set(e) == {"event_id", "run_id", "event_type", "timestamp", "source_id", "parcel_id", "data"}
        assert UUID(e["event_id"])
        assert UUID(e["run_id"])
        assert e["timestamp"].endswith("Z")
        assert isinstance(e["data"], dict)
        json.dumps(e)
    assert len({e["event_id"] for e in messages}) == len(messages)
    assert {e["run_id"] for e in messages} == {engine.run_id}
    assert events(messages, "parcel_created")[0]["source_id"] == "CONVEYOR_01"


def test_parcel_lifecycle_then_auto_dispatch():
    engine, messages = sim(arrival_interval=1000)
    engine.start()
    engine.step(6.3)
    pid = events(messages, "parcel_created")[0]["parcel_id"]
    lifecycle = [e["event_type"] for e in messages if e["parcel_id"] == pid]
    assert lifecycle == ["parcel_created", "parcel_scanned", "parcel_at_sorter", "parcel_sorted"]
    bay = events(messages, "parcel_sorted")[0]["data"]["actual_bay"]
    assert engine.snapshot()["bay_occupancy"][bay] == 1
    engine.step(15.2)
    assert engine.snapshot()["bay_occupancy"][bay] == 0
    assert events(messages, "parcel_dispatched")[0]["parcel_id"] == pid
    assert engine.simulated_total_sorted == 1
    assert engine.simulated_total_dispatched == 1


def test_three_active_limit():
    engine, messages = sim(arrival_interval=0.5, max_active=3)
    engine.start()
    for _ in range(120):
        engine.step(0.1)
        assert len(engine.active) <= 3
    assert len(events(messages, "parcel_created")) > 3


def test_pause_freezes_parcels_and_auto_dispatch():
    engine, messages = sim(arrival_interval=1000)
    engine.start()
    engine.step(6.3)
    before = engine.snapshot()
    engine.pause()
    engine.step(100)
    assert engine.snapshot()["bay_occupancy"] == before["bay_occupancy"]
    assert engine.snapshot()["dispatched"] == 0
    engine.resume()
    engine.step(15.2)
    assert engine.snapshot()["dispatched"] == 1


def test_jam_freezes_active_parcels_and_recovers():
    engine, messages = sim(arrival_interval=1000)
    engine.start()
    engine.step(0.3)
    engine.inject_jam()
    assert events(messages, "conveyor_status")[-1]["data"] == {
        "speed_mps": 0.0, "running": True, "jammed": True
    }
    engine.step(15.0)
    assert not events(messages, "parcel_scanned")
    engine.clear_jam()
    engine.step(6.2)
    assert len(events(messages, "parcel_sorted")) == 1


def test_fail_next_scan_rejects_once():
    engine, messages = sim(arrival_interval=1000)
    engine.start()
    engine.handle_command({"command": "fail_next_scan"})
    engine.step(6.5)
    assert len(events(messages, "parcel_scanned")) == 1
    assert events(messages, "parcel_scanned")[0]["data"]["scan_ok"] is False
    assert events(messages, "parcel_rejected")[0]["data"]["reason"] == "scan_failed"
    assert engine.snapshot()["rejected"] == 1
    assert not events(messages, "parcel_sorted")


def test_fill_bay_then_reject_overflow_then_dispatch():
    engine, messages = sim(arrival_interval=1, auto_dispatch_delay=15)
    engine.start()
    engine.handle_command({"command": "fill_bay", "bay_id": "B"})
    engine.step(40)
    assert engine.snapshot()["bay_occupancy"]["B"] == 5
    assert all(e["data"]["target_bay"] == "B" for e in events(messages, "parcel_created"))
    assert any(e["data"]["reason"] == "bay_full" for e in events(messages, "parcel_rejected"))
    engine.handle_command({"command": "dispatch_bay", "bay_id": "B"})
    assert engine.snapshot()["bay_occupancy"]["B"] == 0
    assert engine.snapshot()["dispatched"] == 5
    assert engine.force_bay is None
    assert "B" not in engine.disable_auto_dispatch


def test_reset_new_run_and_new_ids():
    engine, messages = sim()
    engine.start()
    engine.step(4)
    previous = engine.run_id
    engine.handle_command({"command": "reset"})
    assert engine.run_id != previous
    assert engine.status == "stopped"
    assert not engine.active
    assert all(v == 0 for v in engine.snapshot()["bay_occupancy"].values())
    assert events(messages, "simulator_status")[-1]["data"]["status"] == "stopped"
    engine.start()
    engine.step(0.1)
    assert events(messages, "parcel_created")[-1]["parcel_id"] == "P001"
    assert events(messages, "parcel_created")[-1]["run_id"] == engine.run_id


def test_invalid_command_and_negative_step():
    engine, messages = sim()
    with pytest.raises(ValueError):
        engine.handle_command({"command": "fill_bay", "bay_id": "D"})
    with pytest.raises(ValueError):
        engine.handle_command({"command": "unknown"})
    with pytest.raises(ValueError):
        engine.step(-1)


def test_sensor_equipment_topics_are_valid_event_types():
    engine, messages = sim()
    engine.start()
    allowed = {"simulator_status", "conveyor_status", "scanner_status", "sorter_status", "bay_occupancy_changed"}
    assert {e["event_type"] for e in messages} == allowed
