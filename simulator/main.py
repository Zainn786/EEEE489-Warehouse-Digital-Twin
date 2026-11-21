"""Run the simulator: python -m simulator.main [--auto-start]."""
import argparse
import logging
import queue
import time

from .config import Settings
from .engine import WarehouseSimulator
from .mqtt_client import MQTTTransport

LOG = logging.getLogger("simulator")


def main():
    parser = argparse.ArgumentParser(description="Group 6 sorting-line simulator")
    parser.add_argument("--auto-start", action="store_true", help="start immediately without dashboard command")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    settings = Settings.from_env()
    transport = MQTTTransport(settings.mqtt_host, settings.mqtt_port)
    transport.connect()
    engine = WarehouseSimulator(transport.publish_event, settings=settings)
    if args.auto_start or settings.autostart:
        engine.start()
    else:
        LOG.info("Ready. Awaiting 'start' on warehouse/control/commands")

    last = time.monotonic()
    try:
        while True:
            # Process commands from Paho's callback thread in the engine's main thread.
            while True:
                try:
                    command = transport.commands.get_nowait()
                except queue.Empty:
                    break
                try:
                    engine.handle_command(command)
                    LOG.info("Accepted command %s", command.get("command"))
                except (ValueError, TypeError) as exc:
                    LOG.warning("Rejected command %r: %s", command, exc)
            now = time.monotonic()
            engine.step(min(now - last, 1.0))
            last = now
            time.sleep(0.1)
    except KeyboardInterrupt:
        LOG.info("Stopping simulator")
    finally:
        try:
            engine.stop()
            time.sleep(0.2)  # permit final QoS1 message to leave on background loop
        finally:
            transport.close()


if __name__ == "__main__":
    main()
