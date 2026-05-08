#!/usr/bin/env python3
"""
Fallback MQTT access connector.

This daemon is intentionally separate from the main Discord bot. It listens for
RFID card scans and only responds when the primary bot service is unhealthy.
When active, it queries PostgreSQL directly for access decisions.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass
from typing import Optional, Tuple

import paho.mqtt.client as mqtt
import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor

load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _reason_code_to_int(reason_code) -> int:
    """Normalize paho-mqtt reason codes (int or ReasonCode object) to int."""
    if isinstance(reason_code, (int, float)):
        return int(reason_code)

    value = getattr(reason_code, "value", None)
    if isinstance(value, (int, float)):
        return int(value)

    try:
        return int(str(reason_code))
    except (TypeError, ValueError):
        return -1


@dataclass(frozen=True)
class Settings:
    database_url: str
    db_timezone: str
    access_window_minutes: int
    return_grace_minutes: int

    mqtt_broker: str
    mqtt_port: int
    mqtt_user: str
    mqtt_password: str
    topic_card: str
    topic_response: str

    primary_service_name: str
    health_check_interval_seconds: int
    fail_open_on_service_check_error: bool
    primary_heartbeat_file: Optional[str]
    primary_heartbeat_max_age_seconds: int
    primary_log_file: Optional[str]
    primary_log_max_age_seconds: int

    log_level: str


def load_settings() -> Settings:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise ValueError("DATABASE_URL is required")

    return Settings(
        database_url=database_url,
        db_timezone=os.getenv("DB_TIMEZONE", "America/Chicago"),
        access_window_minutes=max(1, int(os.getenv("ACCESS_WINDOW_MINUTES", "10"))),
        return_grace_minutes=max(1, int(os.getenv("RETURN_GRACE_MINUTES", "10"))),
        mqtt_broker=os.getenv("MQTT_BROKER", "localhost"),
        mqtt_port=int(os.getenv("MQTT_PORT", "1883")),
        mqtt_user=os.getenv("MQTT_USER", "toolbot-db"),
        mqtt_password=os.getenv("MQTT_PASSWORD", ""),
        topic_card=os.getenv("TOPIC_CARD", "access/room/toolroom/card"),
        topic_response=os.getenv("TOPIC_RESPONSE", "access/room/toolroom/response"),
        primary_service_name=os.getenv("PRIMARY_BOT_SERVICE", "test-signout"),
        health_check_interval_seconds=max(1, int(os.getenv("PRIMARY_HEALTH_CHECK_INTERVAL_SECONDS", "5"))),
        fail_open_on_service_check_error=_env_bool("FAIL_OPEN_ON_SERVICE_CHECK_ERROR", False),
        primary_heartbeat_file=(os.getenv("PRIMARY_HEARTBEAT_FILE", "").strip() or None),
        primary_heartbeat_max_age_seconds=max(1, int(os.getenv("PRIMARY_HEARTBEAT_MAX_AGE_SECONDS", "120"))),
        primary_log_file=(os.getenv("PRIMARY_LOG_FILE", "").strip() or None),
        primary_log_max_age_seconds=max(1, int(os.getenv("PRIMARY_LOG_MAX_AGE_SECONDS", "300"))),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )


def setup_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


class PrimaryBotHealth:
    """Caches primary-service health checks to avoid a systemctl call per swipe."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._last_check_ts = 0.0
        self._cached_healthy = True
        self._cached_reason = "UNKNOWN"

    def is_primary_healthy(self) -> Tuple[bool, str]:
        now = time.time()
        if now - self._last_check_ts < self.settings.health_check_interval_seconds:
            return self._cached_healthy, self._cached_reason

        healthy, reason = self._check_now()
        self._cached_healthy = healthy
        self._cached_reason = reason
        self._last_check_ts = now
        return healthy, reason

    def _check_now(self) -> Tuple[bool, str]:
        svc = self.settings.primary_service_name

        try:
            proc = subprocess.run(
                ["systemctl", "is-active", svc],
                capture_output=True,
                text=True,
                check=False,
            )
            state = (proc.stdout or "").strip()
        except Exception as e:
            logging.getLogger(__name__).warning("Service health check failed for %s: %s", svc, e)
            if self.settings.fail_open_on_service_check_error:
                return False, "SERVICE_CHECK_ERROR_FAIL_OPEN"
            return True, "SERVICE_CHECK_ERROR_FAIL_SAFE"

        if state != "active":
            return False, f"SERVICE_STATE_{state or 'UNKNOWN'}"

        hb_path = self.settings.primary_heartbeat_file
        if hb_path:
            fresh, age = self._is_file_fresh(hb_path, self.settings.primary_heartbeat_max_age_seconds)
            if not fresh:
                return False, f"HEARTBEAT_STALE_{int(age)}s"

        log_path = self.settings.primary_log_file
        if log_path:
            fresh, age = self._is_file_fresh(log_path, self.settings.primary_log_max_age_seconds)
            if not fresh:
                return False, f"LOG_STALE_{int(age)}s"

        return True, "PRIMARY_HEALTHY"

    @staticmethod
    def _is_file_fresh(path: str, max_age_seconds: int) -> Tuple[bool, float]:
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return False, float("inf")
        age = time.time() - mtime
        return age <= max_age_seconds, age


class AccessChecker:
    GET_OVERRIDE = "SELECT mode FROM access_override ORDER BY id LIMIT 1;"

    GET_CARD = """
    SELECT user_id, username, enabled
      FROM rfid_cards
     WHERE card_id = %s
     LIMIT 1;
    """

    IS_USER_ADMIN = """
    SELECT is_admin
      FROM users
     WHERE user_id = %s
     LIMIT 1;
    """

    INSERT_SCAN_EVENT = """
    INSERT INTO rfid_scan_events (card_id, scanned_at, consumed)
    VALUES (%s, NOW(), false);
    """

    CHECK_RESERVATION = """
    SELECT r.id, r.status, r.start_time, r.end_time, r.returned_at, r.tool_name, r.username
      FROM reservations r
      JOIN tools t ON r.tool_id = t.id
     WHERE r.user_id = %s
       AND t.is_tool_room = true
       AND (
             (r.status = 'ACTIVE' AND (
                 (
                     r.start_time - (%s * INTERVAL '1 minute') <= (NOW() AT TIME ZONE %s)
                     AND r.start_time + (%s * INTERVAL '1 minute') >= (NOW() AT TIME ZONE %s)
                 )
                 OR
                 (
                     r.end_time - (%s * INTERVAL '1 minute') <= (NOW() AT TIME ZONE %s)
                     AND r.end_time + (%s * INTERVAL '1 minute') >= (NOW() AT TIME ZONE %s)
                 )
             ))
             OR
             (r.status = 'RETURNED' AND r.returned_at IS NOT NULL
                 AND r.returned_at + (%s * INTERVAL '1 minute') >= (NOW() AT TIME ZONE %s)
             )
       )
     LIMIT 1;
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.logger = logging.getLogger(self.__class__.__name__)

    def check_access(self, card_id: str) -> Tuple[bool, str]:
        try:
            with psycopg2.connect(self.settings.database_url) as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    return self._check(cur, card_id)
        except Exception:
            self.logger.exception("Database error during access check")
            return False, "DB_ERROR"

    def _check(self, cur: RealDictCursor, card_id: str) -> Tuple[bool, str]:
        self._try_log_scan_event(cur, card_id)

        mode = self._safe_override_mode(cur)
        cur.execute(self.GET_CARD, (card_id,))
        card = cur.fetchone()

        if mode == "GRANT_ALL":
            return True, "OVERRIDE_GRANT_ALL"

        if mode == "DENY_ALL":
            if card and card.get("enabled") and self._is_admin_user(cur, card["user_id"]):
                return True, "OVERRIDE_DENY_ALL_ADMIN"
            return False, "OVERRIDE_DENY_ALL"

        if not card:
            return False, "UNKNOWN_CARD"
        if not card.get("enabled"):
            return False, "CARD_DISABLED"

        user_id = card["user_id"]
        if self._is_admin_user(cur, user_id):
            return True, "ADMIN_BYPASS_NORMAL"

        window = self.settings.access_window_minutes
        grace = self.settings.return_grace_minutes
        tz = self.settings.db_timezone

        cur.execute(
            self.CHECK_RESERVATION,
            (user_id, window, tz, window, tz, window, tz, window, tz, grace, tz),
        )
        reservation = cur.fetchone()
        if reservation:
            return True, "RESERVATION_VALID"

        return False, "NO_RESERVATION"

    def _safe_override_mode(self, cur: RealDictCursor) -> str:
        try:
            cur.execute(self.GET_OVERRIDE)
            row = cur.fetchone()
            if row and row.get("mode"):
                return str(row["mode"])
        except Exception as e:
            self.logger.debug("access_override unavailable, defaulting to NORMAL: %s", e)
        return "NORMAL"

    def _try_log_scan_event(self, cur: RealDictCursor, card_id: str) -> None:
        try:
            cur.execute(self.INSERT_SCAN_EVENT, (card_id,))
        except Exception as e:
            self.logger.debug("rfid_scan_events insert skipped: %s", e)

    def _is_admin_user(self, cur: RealDictCursor, user_id: str) -> bool:
        cur.execute(self.IS_USER_ADMIN, (user_id,))
        row = cur.fetchone()
        return bool(row and row.get("is_admin"))


class FallbackConnector:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.logger = logging.getLogger(self.__class__.__name__)
        self.health = PrimaryBotHealth(settings)
        self.checker = AccessChecker(settings)

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.username_pw_set(settings.mqtt_user, settings.mqtt_password)
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self._mqtt_active = False
        self._is_connected = False

    def on_connect(self, client, userdata, flags, rc, properties=None):
        rc_int = _reason_code_to_int(rc)
        if rc_int == 0:
            self._is_connected = True
            self.logger.info("Connected to MQTT broker")
            client.subscribe(self.settings.topic_card)
            self.logger.info("Subscribed to %s", self.settings.topic_card)
        else:
            self.logger.error("MQTT connect failed: rc=%s", rc)

    def on_message(self, client, userdata, msg):
        card_id = msg.payload.decode(errors="ignore").strip()
        if not card_id:
            return

        healthy, health_reason = self.health.is_primary_healthy()
        if healthy:
            self.logger.debug(
                "Primary service healthy (%s); fallback passive for card %s",
                health_reason,
                card_id,
            )
            return

        granted, reason = self.checker.check_access(card_id)
        payload = "GRANTED" if granted else "DENIED"
        client.publish(self.settings.topic_response, payload)

        self.logger.info(
            "Fallback active (%s): card=%s result=%s reason=%s",
            health_reason,
            card_id,
            payload,
            reason,
        )

    def on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties=None):
        self._is_connected = False
        if _reason_code_to_int(reason_code) != 0 and self._mqtt_active:
            self.logger.warning("Disconnected from MQTT broker (rc=%s), auto-reconnecting", reason_code)

    def _start_mqtt(self) -> bool:
        """Start MQTT network loop and connect to broker."""
        if self._mqtt_active:
            return True

        try:
            self.client.connect(self.settings.mqtt_broker, self.settings.mqtt_port, keepalive=60)
            self.client.loop_start()
            self._mqtt_active = True
            self.logger.info(
                "Fallback ACTIVE: MQTT connected path enabled (%s:%s)",
                self.settings.mqtt_broker,
                self.settings.mqtt_port,
            )
            return True
        except Exception as e:
            self.logger.error(
                "Failed to start MQTT fallback connection to %s:%s (%s)",
                self.settings.mqtt_broker,
                self.settings.mqtt_port,
                e,
            )
            self._mqtt_active = False
            self._is_connected = False
            return False

    def _stop_mqtt(self) -> None:
        """Stop MQTT network loop and disconnect from broker."""
        if not self._mqtt_active:
            return

        self._mqtt_active = False
        try:
            if self._is_connected:
                self.client.disconnect()
        finally:
            self.client.loop_stop()
            self._is_connected = False
            self.logger.info("Fallback passive: disconnected from MQTT broker")

    def run(self):
        self.logger.info(
            "Starting fallback connector. Primary service=%s, broker=%s:%s",
            self.settings.primary_service_name,
            self.settings.mqtt_broker,
            self.settings.mqtt_port,
        )

        last_health_state = None
        last_health_reason = None

        while True:
            healthy, reason = self.health.is_primary_healthy()

            if healthy:
                if last_health_state is not True or last_health_reason != reason:
                    self.logger.info("Primary healthy (%s) - fallback staying passive", reason)
                if self._mqtt_active:
                    self._stop_mqtt()
            else:
                if last_health_state is not False or last_health_reason != reason:
                    self.logger.warning("Primary unhealthy (%s) - enabling fallback", reason)
                if not self._mqtt_active:
                    started = self._start_mqtt()
                    if not started:
                        # Fast retry when broker is unavailable while fallback is required.
                        time.sleep(5)
                        last_health_state = healthy
                        last_health_reason = reason
                        continue

            last_health_state = healthy
            last_health_reason = reason
            time.sleep(self.settings.health_check_interval_seconds)


def main() -> None:
    settings = load_settings()
    setup_logging(settings.log_level)
    connector = FallbackConnector(settings)
    connector.run()


if __name__ == "__main__":
    main()
