"""Single source of truth for simulator defaults and equipment IDs."""
from dataclasses import dataclass
import os

CONVEYOR_ID = "CONVEYOR_01"
SCANNER_ID = "SCANNER_01"
SORTER_ID = "SORTER_01"
REJECT_LANE_ID = "REJECT_LANE_01"
SIMULATOR_ID = "SIMULATOR"
BAYS = ("A", "B", "C")


@dataclass(frozen=True)
class Settings:
    mqtt_host: str = "127.0.0.1"
    mqtt_port: int = 1883
    autostart: bool = False
    arrival_interval: float = 3.0
    stage_duration: float = 2.0
    auto_dispatch_delay: float = 15.0
    max_active: int = 3
    bay_capacity: int = 5
    conveyor_speed_mps: float = 0.5
    rng_seed: int = 489

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            mqtt_host=os.getenv("MQTT_HOST", "127.0.0.1"),
            mqtt_port=int(os.getenv("MQTT_PORT", "1883")),
            autostart=os.getenv("SIMULATOR_AUTOSTART", "false").lower() in ("true", "1", "yes"),
            rng_seed=int(os.getenv("SIMULATOR_SEED", "489")),
        )
