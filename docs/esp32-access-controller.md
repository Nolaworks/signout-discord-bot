# Access Control — Tool Room

ESP32-based RFID access control node for the tool room. Reads Wiegand 34-bit RFID cards, publishes the card ID over MQTT, and actuates a door lock relay based on the broker's GRANTED/DENIED response.

## System Flow

1. **Discord signout bot** publishes to the MQTT broker when a signout is active.
2. The backend grants the user access in the database for a 10-minute window around their signout start time.
3. User taps RFID card → ESP32 reads Wiegand data → publishes card ID to `access/room/toolroom/card`.
4. Backend checks the DB → publishes `GRANTED` or `DENIED` to `access/room/toolroom/response`.
5. On `GRANTED` → relay energizes (door unlocks) for 5 seconds, green LED flashes.
6. On `DENIED` → red LED flashes for 2 seconds, relay stays locked.

## Hardware

| Component             | Description                              |
|-----------------------|------------------------------------------|
| ESP32 DevKit v1       | Microcontroller                          |
| Wiegand RFID Reader   | 34-bit (e.g., HID ProxPoint Plus)       |
| Relay Module          | 5V single-channel (for door strike/lock) |
| Green LED + Resistor  | Access granted indicator (220Ω)          |
| Red LED + Resistor    | Access denied / status indicator (220Ω)  |

## Pin Wiring

| ESP32 GPIO | Function       | Connects To                        |
|------------|----------------|------------------------------------|
| GPIO 12    | GREEN_LED_PIN  | Green LED anode → 220Ω → GND      |
| GPIO 32    | RED_LED_PIN    | Red LED anode → 220Ω → GND        |
| GPIO 16    | D0_PIN         | RFID Reader DATA0 (Green wire)     |
| GPIO 17    | D1_PIN         | RFID Reader DATA1 (White wire)     |
| GPIO 19    | RELAY_PIN      | Relay module IN (signal)           |
| 3.3V       | Power          | RFID Reader VCC (if 3.3V tolerant) |
| 5V (VIN)   | Power          | Relay module VCC, RFID Reader VCC  |
| GND        | Ground         | Common ground for all components   |

### Wiegand RFID Reader Wiring

```
RFID Reader          ESP32
-----------          -----
DATA0 (Green) ---→   GPIO 16 (D0_PIN, INPUT_PULLUP)
DATA1 (White) ---→   GPIO 17 (D1_PIN, INPUT_PULLUP)
VCC           ---→   5V (VIN) or 3.3V depending on reader
GND           ---→   GND
```

> **Note:** Both D0 and D1 are configured with internal pull-up resistors. The reader pulls the lines LOW to signal bits (FALLING edge interrupt).

### Relay Module Wiring

```
Relay Module         ESP32
------------         -----
IN (Signal)   ---→   GPIO 19 (RELAY_PIN)
VCC           ---→   5V (VIN)
GND           ---→   GND

Door Strike / Mag Lock
----------------------
COM           ---→   Power supply +
NO            ---→   Door strike +  (Normally Open = fail-secure)
```

> **Relay LOW = Locked, Relay HIGH = Unlocked.** The relay is driven LOW on boot and after the unlock timer expires.

### LED Wiring

```
Green LED:  GPIO 12 → LED Anode → 220Ω Resistor → GND
Red LED:    GPIO 32 → LED Anode → 220Ω Resistor → GND
```

## MQTT Topics

| Topic                            | Direction | Payload              |
|----------------------------------|-----------|----------------------|
| `access/room/toolroom/card`     | Publish   | Card ID (decimal)    |
| `access/room/toolroom/response` | Subscribe | `GRANTED` or `DENIED`|

## Configuration

All configuration is in [`src/config.h`](src/config.h):

- WiFi SSID/password
- MQTT broker IP and port
- Pin assignments
- Unlock duration (default: 5000ms)

## Build & Upload

Requires [PlatformIO](https://platformio.org/).

```bash
# Build
pio run

# Upload to ESP32
pio run --target upload

# Monitor serial output
pio device monitor --baud 115200
```

## State Machine

```
IDLE → (card scanned) → AWAITING_RESPONSE
AWAITING_RESPONSE → GRANTED → ACCESS_GRANTED → (unlock timer expires) → IDLE
AWAITING_RESPONSE → DENIED  → ACCESS_DENIED  → (LED timer expires)   → IDLE
```

Invalid actions in any state are silently ignored.

## Debugging

Run these on the MQTT broker (e.g., the `mqtt-runner` LXC) to test and troubleshoot.

```bash
# Subscribe to ALL topics (wildcard) — see everything flowing through the broker
mosquitto_sub -h localhost -u esp32 -P mqtt-control -t '#' -v

# Subscribe to just the card topic
mosquitto_sub -h localhost -u esp32 -P mqtt-control -t 'access/room/toolroom/card' -v

# Manually send a GRANTED response
mosquitto_pub -h localhost -u esp32 -P mqtt-control -t 'access/room/toolroom/response' -m 'GRANTED'

# Manually send a DENIED response
mosquitto_pub -h localhost -u esp32 -P mqtt-control -t 'access/room/toolroom/response' -m 'DENIED'
```

> **Note:** Replace credentials with your own if using a different MQTT user.
