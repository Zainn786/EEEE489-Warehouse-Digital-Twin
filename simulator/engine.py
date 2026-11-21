"""Deterministic, tick-driven warehouse simulation without MQTT dependencies.

Owner: Member 1. Feed a list.append or MQTT publisher as emit callback.
No sleeps or threads here: main.py owns wall clock; this engine owns logical time.
"""
from __future__ import annotations

from collections.abc import Callable
import random
from uuid import uuid4

from .config import BAYS, CONVEYOR_ID, SCANNER_ID, SIMULATOR_ID, SORTER_ID, Settings
from .events import make_event
from .models import Parcel


class WarehouseSimulator:
    def __init__(self, emit: Callable[[dict], None], settings: Settings | None = None):
        self.emit = emit
        self.settings = settings or Settings()
        self.rng = random.Random(self.settings.rng_seed)
        self.run_id = str(uuid4())
        self.status = "stopped"
        self.jammed = False
        self.fail_next_scan = False
        self.force_bay: str | None = None
        self.disable_auto_dispatch: set[str] = set()
        self.active: dict[str, Parcel] = {}
        self.waiting: dict[str, list[Parcel]] = {b: [] for b in BAYS}
        self.next_parcel_number = 1
        self.elapsed = 0.0
        self.next_arrival = 0.0
        self.simulated_total_sorted = 0
        self.simulated_total_rejected = 0
        self.simulated_total_dispatched = 0

    def _publish(self, event_type: str, source_id: str, parcel_id: str | None = None, **data) -> None:
        self.emit(make_event(self.run_id, event_type, source_id, parcel_id, data))

    def _status(self, command: str | None = None):
        data = {"status": self.status}
        if command is not None:
            data["last_command"] = command
        self._publish("simulator_status", SIMULATOR_ID, **data)

    def _conveyor_status(self):
        moving = self.status == "running" and not self.jammed
        self._publish("conveyor_status", CONVEYOR_ID,
                      speed_mps=self.settings.conveyor_speed_mps if moving else 0.0,
                      running=self.status == "running", jammed=self.jammed)

    def _bay_update(self, bay: str):
        self._publish("bay_occupancy_changed", f"BAY_{bay}",
                      bay_id=bay, occupancy=len(self.waiting[bay]), capacity=self.settings.bay_capacity)

    def start(self):
        """Starts stopped run, or resumes a paused run."""
        if self.status == "running":
            return
        self.status = "running"
        self._status("start")
        self._conveyor_status()
        self._publish("scanner_status", SCANNER_ID, status="operational")
        self._publish("sorter_status", SORTER_ID, status="operational")
        for bay in BAYS:
            self._bay_update(bay)

    def pause(self):
        if self.status != "running":
            return
        self.status = "paused"
        self._status("pause")
        self._conveyor_status()

    def resume(self):
        if self.status != "paused":
            return
        self.status = "running"
        self._status("resume")
        self._conveyor_status()

    def reset(self):
        """Begin a fresh stopped run; historical graph events remain under old run_id."""
        self.run_id = str(uuid4())
        self.status = "stopped"
        self.jammed = False
        self.fail_next_scan = False
        self.force_bay = None
        self.disable_auto_dispatch.clear()
        self.active.clear()
        self.waiting = {b: [] for b in BAYS}
        self.next_parcel_number = 1
        self.elapsed = 0.0
        self.next_arrival = 0.0
        self.simulated_total_sorted = 0
        self.simulated_total_rejected = 0
        self.simulated_total_dispatched = 0
        self._status("reset")
        self._conveyor_status()
        for bay in BAYS:
            self._bay_update(bay)

    def stop(self):
        """Stop process gracefully, emitting final equipment state."""
        if self.status != "stopped":
            self.status = "stopped"
            self._status("stop")
            self._conveyor_status()

    def inject_jam(self):
        if self.status != "running":
            raise ValueError("inject_jam requires the simulator to be running")
        if not self.jammed:
            self.jammed = True
            self._conveyor_status()

    def clear_jam(self):
        if self.jammed:
            self.jammed = False
            self._conveyor_status()

    def fill_bay(self, bay: str):
        if bay not in BAYS:
            raise ValueError(f"unknown bay_id: {bay}")
        self.force_bay = bay
        self.disable_auto_dispatch.add(bay)
        # Existing bay parcels remain while this test is active.
        self._bay_update(bay)

    def dispatch_bay(self, bay: str):
        if bay not in BAYS:
            raise ValueError(f"unknown bay_id: {bay}")
        for parcel in list(self.waiting[bay]):
            self._publish("parcel_dispatched", f"BAY_{bay}", parcel.parcel_id,
                          target_bay=parcel.target_bay, actual_bay=bay, status="dispatched")
            self.simulated_total_dispatched += 1
        self.waiting[bay].clear()
        self.disable_auto_dispatch.discard(bay)
        if self.force_bay == bay:
            self.force_bay = None
        self._bay_update(bay)

    def handle_command(self, command: dict) -> None:
        """Consume Member 4's /api/control JSON command.

        Expected {"command": "start"} or {"command": "fill_bay", "bay_id": "A"}.
        """
        if not isinstance(command, dict):
            raise ValueError("command must be a JSON object")
        name = command.get("command")
        if name == "start":
            self.start()
        elif name == "pause":
            self.pause()
        elif name == "resume":
            self.resume()
        elif name == "reset":
            self.reset()
        elif name == "inject_jam":
            self.inject_jam()
        elif name == "clear_jam":
            self.clear_jam()
        elif name == "fail_next_scan":
            self.fail_next_scan = True
        elif name == "fill_bay":
            self.fill_bay(command.get("bay_id"))
        elif name == "dispatch_bay":
            self.dispatch_bay(command.get("bay_id"))
        else:
            raise ValueError(f"unknown command: {name!r}")
        # Acknowledge commands that do not otherwise change simulator_status.
        if name not in {"start", "pause", "resume", "reset"}:
            self._status(name)

    def _create_parcel(self) -> None:
        parcel_id = f"P{self.next_parcel_number:03d}"
        self.next_parcel_number += 1
        target = self.force_bay or self.rng.choice(BAYS)
        parcel = Parcel(parcel_id=parcel_id, target_bay=target)
        self.active[parcel_id] = parcel
        self._publish("parcel_created", CONVEYOR_ID, parcel_id,
                      target_bay=target, status="created", location="ENTRY")

    def _advance_parcel(self, parcel: Parcel) -> None:
        if parcel.status == "created":
            parcel.status = "scanned"
            parcel.scan_ok = not self.fail_next_scan
            if self.fail_next_scan:
                self.fail_next_scan = False
            self._publish("parcel_scanned", SCANNER_ID, parcel.parcel_id,
                          target_bay=parcel.target_bay, scan_ok=parcel.scan_ok, status="scanned")
        elif parcel.status == "scanned":
            parcel.status = "at_sorter"
            self._publish("parcel_at_sorter", SORTER_ID, parcel.parcel_id,
                          target_bay=parcel.target_bay, status="at_sorter")
        elif parcel.status == "at_sorter":
            bay = parcel.target_bay
            if not parcel.scan_ok or len(self.waiting[bay]) >= self.settings.bay_capacity:
                parcel.status = "rejected"
                reason = "scan_failed" if not parcel.scan_ok else "bay_full"
                self.simulated_total_rejected += 1
                self._publish("parcel_rejected", SORTER_ID, parcel.parcel_id,
                              target_bay=bay, actual_bay=None, reason=reason, status="rejected")
            else:
                parcel.status = "sorted"
                parcel.actual_bay = bay
                parcel.bay_elapsed = 0.0
                self.waiting[bay].append(parcel)
                self.simulated_total_sorted += 1
                self._publish("parcel_sorted", SORTER_ID, parcel.parcel_id,
                              target_bay=bay, actual_bay=bay, status="sorted")
                self._bay_update(bay)
            del self.active[parcel.parcel_id]

    def _small_step(self, dt: float) -> None:
        self.elapsed += dt
        # First process waiting bays; dispatch is independent of a conveyor jam.
        for bay, queue in self.waiting.items():
            if bay in self.disable_auto_dispatch:
                continue
            eligible = []
            for p in queue:
                p.bay_elapsed += dt
                if p.bay_elapsed + 1e-9 >= self.settings.auto_dispatch_delay:
                    eligible.append(p)
            if eligible:
                for p in eligible:
                    self._publish("parcel_dispatched", f"BAY_{bay}", p.parcel_id,
                                  target_bay=p.target_bay, actual_bay=bay, status="dispatched")
                    self.simulated_total_dispatched += 1
                    queue.remove(p)
                self._bay_update(bay)

        if self.jammed:
            # Do not "catch up" missed arrivals with a burst after a jam clears.
            self.next_arrival = max(self.next_arrival, self.elapsed + self.settings.arrival_interval)
            return

        # Progress current parcels before creating replacements on a free slot.
        for parcel in list(self.active.values()):
            parcel.stage_elapsed += dt
            if parcel.stage_elapsed + 1e-9 >= self.settings.stage_duration:
                parcel.stage_elapsed -= self.settings.stage_duration
                self._advance_parcel(parcel)

        if self.elapsed + 1e-9 >= self.next_arrival:
            # No backlog: skip arrivals while the conveyor is at capacity.
            self.next_arrival += self.settings.arrival_interval
            if len(self.active) < self.settings.max_active:
                self._create_parcel()

    def step(self, elapsed_seconds: float) -> None:
        """Advance simulated time by `elapsed_seconds` (nothing happens while paused).

        Break into small increments so events are ordered even if the caller's loop lags.
        """
        if elapsed_seconds < 0:
            raise ValueError("elapsed_seconds cannot be negative")
        if self.status != "running":
            return
        remaining = float(elapsed_seconds)
        while remaining > 1e-9:
            dt = min(remaining, 0.1)
            self._small_step(dt)
            remaining -= dt

    def snapshot(self) -> dict:
        """Local simulator status for tests/logging; the dashboard uses FastAPI."""
        return {
            "run_id": self.run_id,
            "status": self.status,
            "jammed": self.jammed,
            "active_parcels": len(self.active),
            "bay_occupancy": {b: len(v) for b, v in self.waiting.items()},
            "sorted": self.simulated_total_sorted,
            "rejected": self.simulated_total_rejected,
            "dispatched": self.simulated_total_dispatched,
        }
