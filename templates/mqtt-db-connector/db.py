"""
db.py — PostgreSQL access-check logic for the MQTT-DB connector.

Drop this into the mqtt-db-connector repo as db.py.

Decision flow:
  1. Check global access_override table.
     - GRANT_ALL → GRANTED
     - DENY_ALL  → check if card owner is an admin (users.is_admin) → GRANTED / DENIED
    - NORMAL    → if card owner is admin, GRANTED; otherwise continue to reservation check
  2. Look up card_id in rfid_cards → get user_id (card must be enabled).
  3. Query reservations + tools for a valid tool-room reservation:
      - ACTIVE:   start window or end window only
                      * start_time − 10 min ≤ now ≤ start_time + 10 min
                      * end_time   − 10 min ≤ now ≤ end_time   + 10 min
      - RETURNED: returned_at + 10 min ≥ now  (post-return grace)
  4. Return GRANTED or DENIED.
"""

import logging

import psycopg2
from psycopg2.extras import RealDictCursor

from config import DATABASE_URL, DB_TIMEZONE   # adjust import to match your config module

logger = logging.getLogger(__name__)

# ── SQL ──────────────────────────────────────────────────────────────────────

GET_OVERRIDE = """
SELECT mode FROM access_override ORDER BY id LIMIT 1;
"""

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
SELECT r.id, r.status, r.start_time, r.end_time, r.returned_at,
       r.tool_name, r.username
  FROM reservations r
  JOIN tools t ON r.tool_id = t.id
 WHERE r.user_id   = %s
   AND t.is_tool_room = true
   AND (
        -- ACTIVE reservation: access only near the start or end of the reservation.
         (r.status = 'ACTIVE'
                AND (
                    (
                     r.start_time - INTERVAL '10 minutes' <= (NOW() AT TIME ZONE %s)
                     AND r.start_time + INTERVAL '10 minutes' >= (NOW() AT TIME ZONE %s)
                    )
                    OR
                    (
                     r.end_time - INTERVAL '10 minutes' <= (NOW() AT TIME ZONE %s)
                     AND r.end_time + INTERVAL '10 minutes' >= (NOW() AT TIME ZONE %s)
                    )
                ))
       OR
         -- RETURNED reservation: 10-min grace after the user returned
         (r.status = 'RETURNED'
          AND r.returned_at IS NOT NULL
                    AND r.returned_at + INTERVAL '10 minutes' >= (NOW() AT TIME ZONE %s))
       )
 LIMIT 1;
"""

# ── Public API ───────────────────────────────────────────────────────────────

def check_access(card_id: str) -> tuple[bool, str]:
    """
    Main entry point called by connector.py on each card-scan event.

    Returns:
        (granted: bool, reason: str)
    """
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                return _check(cur, card_id)
    except Exception:
        logger.exception("Database error during access check")
        return False, "DB_ERROR"


def _check(cur, card_id: str) -> tuple[bool, str]:
    # Log every swipe for admin capture workflows.
    try:
        cur.execute(INSERT_SCAN_EVENT, (card_id,))
    except Exception as e:
        logger.warning("Failed to log scan event for card %s: %s", card_id, e)

    # ── 1. Global override ────────────────────────────────────────────────
    cur.execute(GET_OVERRIDE)
    row = cur.fetchone()
    mode = row["mode"] if row else "NORMAL"

    if mode == "GRANT_ALL":
        logger.info("Override GRANT_ALL — granting card %s", card_id)
        return True, "OVERRIDE_GRANT_ALL"

    # Look up card (needed for both DENY_ALL admin check and NORMAL flow)
    cur.execute(GET_CARD, (card_id,))
    card = cur.fetchone()

    if mode == "DENY_ALL":
        if card and card["enabled"]:
            # Check if card owner is a Discord admin
            cur.execute(IS_USER_ADMIN, (card["user_id"],))
            user = cur.fetchone()
            if user and user["is_admin"]:
                logger.info("Override DENY_ALL but admin user %s — granting", card["username"])
                return True, "OVERRIDE_DENY_ALL_ADMIN"
        logger.info("Override DENY_ALL — denying card %s", card_id)
        return False, "OVERRIDE_DENY_ALL"

    # ── 2. NORMAL mode — look up card ─────────────────────────────────────
    if not card:
        logger.info("Unknown card %s", card_id)
        return False, "UNKNOWN_CARD"

    if not card["enabled"]:
        logger.info("Disabled card %s (%s)", card_id, card["username"])
        return False, "CARD_DISABLED"

    user_id = card["user_id"]
    username = card["username"]

    # Optional policy: admins always unlock in NORMAL mode.
    # Card still must be enabled, so revoke/remove continues to work.
    cur.execute(IS_USER_ADMIN, (user_id,))
    user = cur.fetchone()
    if user and user["is_admin"]:
        logger.info("Admin bypass in NORMAL mode — granting card %s (%s)", card_id, username)
        return True, "ADMIN_BYPASS_NORMAL"

    # ── 3. Check for valid tool-room reservation ──────────────────────────
    cur.execute(
        CHECK_RESERVATION,
        (user_id, DB_TIMEZONE, DB_TIMEZONE, DB_TIMEZONE, DB_TIMEZONE, DB_TIMEZONE),
    )
    reservation = cur.fetchone()

    if reservation:
        logger.info(
            "GRANTED — %s (card %s) — %s [%s]",
            username, card_id, reservation["tool_name"], reservation["status"],
        )
        return True, "RESERVATION_VALID"

    logger.info("DENIED — %s (card %s) — no valid reservation", username, card_id)
    return False, "NO_RESERVATION"
