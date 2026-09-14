# TerraVeil: LoRa receiver to USB dashboard

Current selected topology (no Wi-Fi required):

```text
UG-01 --433 MHz LoRa--> HOST-01 --USB / COM7--> PC Flask application
                                                |-- terraveil.db
                                                |-- offline ML / risk engine
                                                |-- dashboard + GIS + digital twin
```

## Start with the receiver already flashed

Your original receiver sketch is supported directly. No firmware upload is needed
for live dashboard telemetry. The PC adapter reads the complete serial log block:
`RAW: DATA,...`, Host ID, Zone ID, RSSI, SNR, and `[OK] Packet processed.`
Incomplete blocks are discarded; missing RSSI/SNR is never invented.

1. Connect HOST-01's USB cable to the PC.
2. Close Arduino Serial Monitor / Serial Plotter and other tools using COM7.
3. From this project directory run:

```powershell
python -m pip install -r requirements.txt
python app.py
```

Open `http://localhost:5000` and select **REAL HARDWARE**. The app automatically
opens COM7 at 115200 baud, without requesting an ESP32 reset, and retries when the
port is absent or busy. The status line shows USB port/state separately from node
freshness. A connected USB cable alone does not mark UG-01 ONLINE.

Optional environment settings, set before starting Flask:

```powershell
$env:TERRAVEIL_SERIAL_PORT = 'COM7'
$env:TERRAVEIL_SERIAL_BAUD = '115200'
$env:NODE_TIMEOUT_SECONDS = '15'
# To use HTTP ingestion without the USB adapter:
$env:TERRAVEIL_SERIAL_ENABLED = '0'
```

USB auto-start is part of `python app.py`. For an imported Flask/WSGI deployment,
explicitly call `serial_bridge.start(app.accept_telemetry)` once in its entry
point. Use one Mother Host process: COM7 is exclusive, and the existing manual
alert state is in memory. Do not run the Arduino monitor alongside the app.

The original receiver acknowledges LoRa packets independently of the PC. It does
**not** buffer telemetry while the PC app is stopped or USB is disconnected.
The PC adapter queues up to 256 observations during temporary database failures;
it reports overflow and retries storage. It clears stale OS serial backlog when
opening a connection because the original log format has no capture timestamp.

## Optional buffered USB receiver firmware

`firmware/host01/host01.ino` adds a 256-packet gateway RAM queue and a machine-readable
USB stream. It contains no Wi-Fi/HTTP code. The original provided receiver is saved
as `firmware/host01/receiver-original.ino.txt` for reference/recovery.

The radio configuration matches the supplied working sketch:

| Setting | Value |
|---|---|
| SCK / MISO / MOSI | GPIO 18 / 19 / 23 |
| CS / RESET / DIO0 | GPIO 5 / 14 / 26 |
| Frequency / TX power | 433 MHz / 17 dBm |
| SF / bandwidth / coding rate | 7 / 125 kHz / 4/5 |
| Sync word / CRC | 0x34 / enabled |

The LoRa protocol is unchanged:

```text
DATA,UG-01,126,54.37,-6.19,0,0.0
ACK,UG-01,126
```

The firmware sends the LoRa ACK before queueing telemetry. A separate FreeRTOS
task forwards USB frames, so waiting for the PC does not block LoRa reception
apart from the radio's own half-duplex ACK airtime. DIO0 is used for asynchronous
ACK completion. The PC responds `STORED,UG-01,126` only after successful database
processing (including idempotent duplicates). Until that confirmation, the gateway
repeats its oldest packet every second. USB confirmations are not LoRa ACKs.

Each USB frame includes actual roll/pitch/vibration/soil/RSSI/SNR plus gateway
`queue_age_ms`. A monotonic clock preserves capture age without Wi-Fi or NTP.
SQLite adds `server_timestamp`; `timestamp`/`last_seen` estimate original gateway
receipt time. Stale replay does not make a stopped node appear live.

The full queue drops newest packets and reports their sequence/count; 256 packets
cover about 8.5 minutes at one packet per two seconds. **Power loss/reset clears
RAM buffers.** Persistent flash buffering is not implemented.

To upload in Arduino IDE, open `firmware/host01/host01.ino`, keep its
`config.example.h` alongside it, install **LoRa by Sandeep Mistry** and
**ArduinoJson 7**, choose the ESP32 DOIT DevKit V1 board and COM7. Close TerraVeil
before uploading so COM7 is free. Radio overrides can be kept in git-ignored
`host_config.h`. No SSID, password or backend IP configuration is needed.

PlatformIO build/upload alternative:

```powershell
python -m pip install platformio
python -m platformio run -d firmware/host01
python -m platformio run -d firmware/host01 -t upload --upload-port COM7
```

## Data integrity and interpretation

The existing `terraveil.db` is reused and migrated in place; `subsentry.db` is not
used. Existing history remains SIMULATION. REAL and SIMULATION use the same
validation, median preprocessing, offline Isolation Forest and risk pipeline.
HTTP `/api/telemetry` remains available but is not required for USB operation.
`TERRAVEIL_DB` can override the database path; default is beside `database.py`.

REAL `(node_id, session_id, sequence)` is unique. Duplicates do not add rows or
refresh last_seen. Older unseen sequences are retained as history with `processed=0`
and cannot replace live state. The unchanged DATA format cannot distinguish a
reboot/counter reset from delayed packets; no automatic reboot guessing is used.

If UG-01 restarts and its sequence returns to zero, drain any previous gateway
buffered packets, then use **Node restarted? Start new session** in its telemetry
panel. Confirm only after the physical restart. This begins a new backend sequence
session and resets live/persistence state while keeping all historical measurements.
Until a fresh packet arrives the node remains offline. Historical rows retain their
session IDs. System Health distinguishes received, live, duplicate and older packets.

`python app.py` holds a process lock. A second invocation exits with a link to the
running server instead of competing for COM7.

Hardware values map to tilt, vibration events, raw soil ADC and actual RSSI/SNR.
Battery, BME280 and displacement are unavailable. Soil is not a calibrated percent.
UG-01 stays unlocated until both `UG01_LATITUDE` and `UG01_LONGITUDE` are configured
before startup. No demo GPS location is assigned to the physical node.

An isolated anomaly remains MEDIUM / ANOMALY DETECTED. HIGH requires persistent,
nearby correlated evidence; CRITICAL additionally requires sustained persistence,
a worsening trend, vibration and large tilt. A single UG-01 cannot establish
correlated subsidence. The model's synthetic training and prototype thresholds
need physical calibration. The manual emergency path remains independent of
Tier-2 advisory analysis. Existing siren state/browser audio do not claim remote
physical siren actuation.

Dashboard polling and the twin use the same backend-derived state. Histories and
advisories remain source-filtered; missing measurements are never generated in
REAL mode. Twin settlement/geographic registration is illustrative, not surveyed.

## Verification

```powershell
python -m unittest discover -s tests -v
node --check static/js/app.js
python -m platformio run -d firmware/host01
```

Tests use temporary databases and supplied log fixtures. They cover complete and
partial serial frames, malformed/oversized input, queue-age preservation, duplicate
handling, storage confirmations, source isolation, offline expiry and twin state.
No fixture measurements are inserted into the real database.

On connected hardware, verify real measurements appear after a DATA packet; rotate
the MPU6050 and trigger SW-420; stop UG-01 and observe OFFLINE after the timeout.
For buffered firmware, stop/restart the PC app with the gateway powered and verify
FIFO replay without duplicate rows, then resume current live telemetry.
