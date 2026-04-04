# MQTT-DB Connector — Tool Room Access Control

Bridges the Eclipse Mosquitto MQTT broker and the PostgreSQL `signout_bot` database. Subscribes to RFID card-scan events, checks for a valid tool-room reservation, and publishes `GRANTED` or `DENIED`.

## How It Works

1. ESP32 publishes a decimal card ID to `access/room/toolroom/card`.
2. This connector receives the card ID via MQTT.
3. Looks up the card in the `rfid_cards` table → gets the `user_id`.
4. Queries `reservations` + `tools` for an `ACTIVE` reservation on a tool-room tool (`is_tool_room = true`) whose `start_time` is within ±10 minutes of now.
5. Publishes `GRANTED` or `DENIED` to `access/room/toolroom/response`.

## Database Migration

Run the migration against your PostgreSQL instance to create the `rfid_cards` table:

```bash
psql -h 192.168.30.151 -U botuser -d signout_bot -f migrations/001_create_rfid_cards.sql
```

Then register cards:

```sql
INSERT INTO rfid_cards (user_id, card_id, username)
VALUES ('1022173205651275927', '12345678', 'mmennelle');
```

## Deployment (systemd on the Mosquitto LXC)

```bash
# 1. Copy files to the LXC
scp -r ./* root@<mqtt-lxc-ip>:/opt/mqtt-db-connector/

# 2. SSH into the LXC
ssh root@<mqtt-lxc-ip>

# 3. Create venv and install deps
cd /opt/mqtt-db-connector
python3 -m venv venv
venv/bin/pip install -r requirements.txt

# 4. Copy and edit .env
cp .env.example .env
nano .env

# 5. Install the systemd service
cp mqtt-db-connector.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now mqtt-db-connector

# 6. Check status / logs
systemctl status mqtt-db-connector
journalctl -u mqtt-db-connector -f
```

## Mosquitto User Setup

Create the `toolbot-db` user in your Mosquitto password file:

```bash
mosquitto_passwd /etc/mosquitto/passwd toolbot-db
# Enter password: nwcomm@toolbot
systemctl restart mosquitto
```

Make sure the Mosquitto ACL allows `toolbot-db` to subscribe to `access/room/toolroom/card` and publish to `access/room/toolroom/response`.

## Project Structure

```
├── connector.py          # Main MQTT loop — entry point
├── config.py             # Loads .env, sets up logging
├── db.py                 # PostgreSQL access check logic
├── requirements.txt      # Python dependencies
├── .env.example          # Template environment config
├── .gitignore
├── mqtt-db-connector.service  # systemd unit file
└── migrations/
    └── 001_create_rfid_cards.sql
```

## Testing

From the Mosquitto LXC:

```bash
# Simulate a card scan
mosquitto_pub -h localhost -u toolbot-db -P 'nwcomm@toolbot' \
  -t 'access/room/toolroom/card' -m '12345678'

# Watch responses
mosquitto_sub -h localhost -u toolbot-db -P 'nwcomm@toolbot' \
  -t 'access/room/toolroom/response' -v
```
