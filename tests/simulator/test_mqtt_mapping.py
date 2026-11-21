"""MQTT topic mapping compatible with Member 4's planned ingestion subscriptions."""
from simulator.mqtt_client import TOPIC_BY_EVENT, CONTROL_TOPIC


def test_topics():
    assert TOPIC_BY_EVENT["simulator_status"] == "warehouse/simulator/status"
    assert TOPIC_BY_EVENT["conveyor_status"] == "warehouse/events/equipment"
    assert TOPIC_BY_EVENT["bay_occupancy_changed"] == "warehouse/events/bay"
    assert CONTROL_TOPIC == "warehouse/control/commands"
