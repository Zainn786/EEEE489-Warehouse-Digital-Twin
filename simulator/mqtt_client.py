"""MQTT transport; Paho callback thread only queues control commands."""
import json
import logging
import queue
import threading


LOG = logging.getLogger(__name__)
CONTROL_TOPIC = "warehouse/control/commands"
TOPIC_BY_EVENT = {
    "simulator_status": "warehouse/simulator/status",
    "conveyor_status": "warehouse/events/equipment",
    "scanner_status": "warehouse/events/equipment",
    "sorter_status": "warehouse/events/equipment",
    "bay_occupancy_changed": "warehouse/events/bay",
}


class MQTTTransport:
    def __init__(self, host: str, port: int):
        # Deferred import also lets pure simulation tests run without a broker client.
        import paho.mqtt.client as mqtt
        self._mqtt = mqtt
        self.host = host
        self.port = port
        self.commands: queue.Queue[dict] = queue.Queue()
        self.connected = threading.Event()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            LOG.error("MQTT broker rejected connection: %s", reason_code)
            return
        client.subscribe(CONTROL_TOPIC, qos=1)
        self.connected.set()
        LOG.info("Connected to MQTT %s:%s and listening for controls", self.host, self.port)

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        self.connected.clear()
        if reason_code.value != 0:
            LOG.warning("Unexpected MQTT disconnection: %s", reason_code)

    def _on_message(self, client, userdata, msg):
        try:
            obj = json.loads(msg.payload.decode("utf-8"))
            if not isinstance(obj, dict) or not isinstance(obj.get("command"), str):
                raise ValueError("control payload needs command string")
            self.commands.put(obj)
        except (ValueError, UnicodeDecodeError) as exc:
            LOG.warning("Ignoring malformed MQTT command: %s", exc)

    def connect(self, timeout: float = 10.0):
        self.client.connect(self.host, self.port, keepalive=60)
        self.client.loop_start()
        if not self.connected.wait(timeout):
            self.close()
            raise RuntimeError("MQTT broker connection timed out")

    def publish_event(self, event: dict):
        topic = TOPIC_BY_EVENT.get(event["event_type"], "warehouse/events/parcel")
        payload = json.dumps(event, separators=(",", ":"))
        info = self.client.publish(topic, payload, qos=1, retain=False)
        if info.rc != self._mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"MQTT publish failed with result {info.rc}")
        LOG.info("Published %s %s", event["event_type"], event.get("parcel_id") or "")
        return info

    def close(self):
        try:
            self.client.disconnect()
        finally:
            self.client.loop_stop()
