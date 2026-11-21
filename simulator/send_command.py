"""Development-only command sender; no PowerShell JSON escaping required.

Examples:
  python -m simulator.send_command start
  python -m simulator.send_command fill_bay A
"""
import argparse
import json

import paho.mqtt.client as mqtt

from .config import BAYS, Settings
from .mqtt_client import CONTROL_TOPIC


def main():
    parser = argparse.ArgumentParser(description="Send a command to the warehouse simulator")
    parser.add_argument("command", choices=(
        "start", "pause", "resume", "reset", "inject_jam", "clear_jam",
        "fail_next_scan", "fill_bay", "dispatch_bay",
    ))
    parser.add_argument("bay_id", nargs="?", choices=BAYS)
    args = parser.parse_args()
    if args.command in ("fill_bay", "dispatch_bay") and not args.bay_id:
        parser.error(f"{args.command} requires bay_id A, B or C")
    if args.command not in ("fill_bay", "dispatch_bay") and args.bay_id:
        parser.error(f"{args.command} does not take a bay_id")
    obj = {"command": args.command}
    if args.bay_id:
        obj["bay_id"] = args.bay_id
    config = Settings.from_env()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect(config.mqtt_host, config.mqtt_port, 60)
    client.loop_start()
    try:
        info = client.publish(CONTROL_TOPIC, json.dumps(obj), qos=1, retain=False)
        info.wait_for_publish(timeout=5)
        if not info.is_published():
            raise RuntimeError("Command was not published")
        print(f"Command published: {obj}")
    finally:
        client.disconnect()
        client.loop_stop()


if __name__ == "__main__":
    main()
