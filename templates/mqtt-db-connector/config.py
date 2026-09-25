"""
config.py — Configuration for the MQTT-DB connector.

Reads from .env file. Drop into the mqtt-db-connector repo as config.py.
"""

import os
import logging
from dotenv import load_dotenv

load_dotenv()

# ── PostgreSQL ────────────────────────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL must be set in the environment")

# Reservation timestamps in this bot DB are stored as timezone-naive local times.
# Use the same timezone here when evaluating now() in SQL comparisons.
DB_TIMEZONE = os.getenv("DB_TIMEZONE", "America/Chicago")

# ── MQTT ──────────────────────────────────────────────────────────────────────
MQTT_BROKER   = os.getenv("MQTT_BROKER", "localhost")
MQTT_PORT     = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER     = os.getenv("MQTT_USER", "toolbot-db")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "nwcomm@toolbot")

# ── Topics ────────────────────────────────────────────────────────────────────
TOPIC_CARD     = os.getenv("TOPIC_CARD", "access/room/toolroom/card")
TOPIC_RESPONSE = os.getenv("TOPIC_RESPONSE", "access/room/toolroom/response")

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")


def setup_logging():
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
