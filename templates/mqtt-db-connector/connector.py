"""
connector.py — MQTT loop for the tool-room access controller.

Subscribes to card-scan events, calls db.check_access(), and publishes
GRANTED or DENIED back to the ESP32.

Drop this into the mqtt-db-connector repo as connector.py.
"""

import logging
import paho.mqtt.client as mqtt

from config import (
    MQTT_BROKER, MQTT_PORT, MQTT_USER, MQTT_PASSWORD,
    TOPIC_CARD, TOPIC_RESPONSE,
)
from db import check_access

logger = logging.getLogger(__name__)


def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        logger.info("Connected to MQTT broker")
        client.subscribe(TOPIC_CARD)
        logger.info("Subscribed to %s", TOPIC_CARD)
    else:
        logger.error("MQTT connect failed: rc=%s", rc)


def on_message(client, userdata, msg):
    card_id = msg.payload.decode().strip()
    if not card_id:
        return

    logger.info("Card scan received: %s", card_id)

    granted, reason = check_access(card_id)
    response = "GRANTED" if granted else "DENIED"

    client.publish(TOPIC_RESPONSE, response)
    logger.info("Published %s (reason: %s) for card %s", response, reason, card_id)


def main():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
    client.on_connect = on_connect
    client.on_message = on_message

    logger.info("Connecting to %s:%s", MQTT_BROKER, MQTT_PORT)
    client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    client.loop_forever()


if __name__ == "__main__":
    from config import setup_logging
    setup_logging()
    main()
